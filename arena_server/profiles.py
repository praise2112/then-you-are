"""A player's programme: record per game against the House, badges, every duel, best duels,
and the games played against people, which never count on the record."""

from typing import Any

from arena_core.state import FINISHED, JUDGED, STANDING, result_kind
from arena_server.leaderboard import account_ranks, account_streaks, streaks
from arena_server.matches import MatchService
from arena_server.sessions import account_of
from arena_server.store import MatchError
from arena_server.views import BadgeCount, DuelRow, GameRecord, ProfileView

BEST_SHOWN = 2


async def profile(service: MatchService, account_id: str, session_key: str | None) -> ProfileView:
    async with service.pool.connection() as conn:
        account = await (
            await conn.execute(
                "select id, display_name, avatar_url, created_at from accounts where id = %s",
                (account_id,),
            )
        ).fetchone()
        if account is None:
            raise MatchError(404, "no such player")
        is_yours = await account_of(conn, session_key) == account_id
        matches = await (
            await conn.execute(
                "select m.id, m.template_id, m.status, m.winner, m.winner = se.seat as won, "
                "m.end_reason, m.round_n, se.points as my_points, "
                "(select coalesce(max(o.points), 0) from seats o "
                "where o.match_id = m.id and o.seat <> se.seat) as their_points, "
                "m.created_at, m.ended_at, m.is_public, m.is_curated, m.kind, "
                "(select array_agg(case when o.kind = 'model' then null else "
                "os.stage_name end order by o.seat) from seats o "
                "left join sessions os on os.session_key = o.session_key "
                "where o.match_id = m.id and o.seat <> se.seat) as others, "
                "(select count(*) from turns t where t.match_id = m.id and t.seq is not null "
                "and t.outcome = any(%(judged)s)) as judged "
                "from matches m join seats se on se.match_id = m.id and se.kind = 'human' "
                "join sessions s on s.session_key = se.session_key "
                "where s.account_id = %(account_id)s order by m.created_at desc",
                {"judged": list(JUDGED), "account_id": account_id},
            )
        ).fetchall()
        badge_rows = await (
            await conn.execute(
                "select b as name, count(*) as count from turns t "
                "join verdicts v on v.id = t.live_verdict_id "
                "join matches m on m.id = t.match_id "
                "join seats se on se.match_id = m.id and se.kind = 'human' "
                "join sessions s on s.session_key = se.session_key "
                "cross join jsonb_array_elements_text(v.host -> 'badges') b "
                "where s.account_id = %s and t.actor = se.seat and m.status = 'ended' "
                "group by b order by count desc, b",
                (account_id,),
            )
        ).fetchall()
        won_ids = [m["id"] for m in matches if m["status"] == "ended" and m["won"]]
        peaks = await (
            await conn.execute(
                "select t.match_id, coalesce(max(t.points), 0) as peak from turns t "
                "join matches m on m.id = t.match_id "
                "where t.match_id = any(%s) and t.actor = m.winner "
                "and t.outcome = any(%s) group by t.match_id",
                (won_ids, list(STANDING)),
            )
        ).fetchall()
        ranks = await account_ranks(conn, account_id)
        streak, best_streak = await account_streaks(conn, account_id)
        ranked = [m for m in matches if m["kind"] == "house"]
        records = []
        for slug, template in service.templates.items():
            ended = [m for m in ranked if m["template_id"] == slug and m["status"] == "ended"]
            if not ended:
                continue
            _, best = streaks([m["won"] for m in sorted(ended, key=lambda m: m["ended_at"])])
            records.append(
                GameRecord(
                    slug=slug,
                    title=template.title,
                    played=len(ended),
                    won=sum(m["won"] is True for m in ended),
                    drawn=sum(m["won"] is None for m in ended),
                    best_streak=best,
                    rank=ranks.get(slug),
                )
            )

    ended_all = [m for m in ranked if m["status"] == "ended"]
    shown = [m for m in matches if is_yours or (m["status"] == "ended" and m["is_public"])]
    peak = {r["match_id"]: r["peak"] for r in peaks}
    best_ids = _best_ids([m for m in shown if m["kind"] == "house"], peak)
    return ProfileView(
        id=account["id"],
        display_name=account["display_name"],
        avatar_url=account["avatar_url"],
        since=account["created_at"].isoformat(),
        played=len(ended_all),
        won=sum(m["won"] is True for m in ended_all),
        streak=streak,
        best_streak=best_streak,
        records=records,
        badges=[BadgeCount(name=b["name"], count=b["count"]) for b in badge_rows],
        duels=[_row(m, service) for m in shown if m["kind"] == "house"],
        people=[_row(m, service) for m in shown if m["kind"] != "house"],
        best=[await service.replay(match_id, session_key) for match_id in best_ids],
        is_yours=is_yours,
    )


def _best_ids(matches: list[Any], peak: dict[str, int]) -> list[str]:
    """Won duels ranked by the player's highest-scoring move; curated duels first."""
    won = [m for m in matches if m["status"] == "ended" and m["won"]]
    won.sort(key=lambda m: (m["is_curated"], peak.get(m["id"], 0)), reverse=True)
    return [m["id"] for m in won[:BEST_SHOWN]]


def _row(m: Any, service: MatchService) -> DuelRow:
    template = service.templates[m["template_id"]]
    finished = m["status"] in FINISHED
    return DuelRow(
        id=m["id"],
        title=template.title,
        mode=template.mode,
        created_at=m["created_at"].isoformat(),
        status=m["status"],
        result_kind=result_kind(m["end_reason"], m["winner"]) if finished else None,
        won=None if m["status"] != "ended" else m["won"] is True,
        points=m["my_points"],
        their_points=m["their_points"],
        judged_moves=m["judged"],
        rounds_played=min(m["round_n"], template.rounds_budget),
        is_public=m["is_public"],
        against=_against(m["others"] or []),
    )


def _against(others: list[str | None]) -> str:
    """The other seats in seat order. None is a House seat, numbered as on the match page."""
    names = []
    houses = 0
    for name in others:
        if name is None:
            houses += 1
            name = "The House" if houses == 1 else f"The House {houses}"
        names.append(name)
    return ", ".join(names)
