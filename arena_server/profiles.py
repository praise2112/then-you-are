"""A player's programme: record per game against the House, badges, every duel, best duels,
and the games played against people, which never count on the record."""

from typing import Any

from arena_core.state import weighted_total
from arena_server.leaderboard import account_rank, streaks
from arena_server.matches import MatchError, MatchService
from arena_server.views import BadgeCount, DuelRow, GameRecord, ProfileView

OPEN = ("open", "active", "awaiting_judgment", "paused")
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
        viewer = await (
            await conn.execute(
                "select account_id from sessions where session_key = %s", (session_key,)
            )
        ).fetchone()
        is_yours = bool(viewer and viewer["account_id"] == account_id)
        matches = await (
            await conn.execute(
                "select m.id, m.template_id, m.status, m.winner = se.seat as won, "
                "m.end_reason, se.points as my_points, (select max(o.points) from seats o "
                "where o.match_id = m.id and o.seat <> se.seat) as their_points, m.created_at, "
                "m.ended_at, m.is_public, m.is_curated, m.kind, "
                "(select count(*) from seats o where o.match_id = m.id) as seat_count, "
                "(select array_agg(case when o.kind = 'model' then null else "
                "coalesce(os.stage_name, 'Challenger') end order by o.seat) from seats o "
                "left join sessions os on os.session_key = o.session_key "
                "where o.match_id = m.id and o.seat <> se.seat) as others, "
                "(select count(*) from turns t where t.match_id = m.id and t.seq is not null "
                "and t.outcome in ('accept', 'fail', 'semantic_uncertain')) as judged, "
                "(select count(*) from turns t where t.match_id = m.id and t.actor = se.seat "
                "and t.outcome in ('accept', 'fail', 'semantic_uncertain')) as my_moves "
                "from matches m join seats se on se.match_id = m.id and se.kind = 'human' "
                "join sessions s on s.session_key = se.session_key "
                "where s.account_id = %s order by m.created_at desc",
                (account_id,),
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
        scorings = await (
            await conn.execute(
                "select t.match_id, v.scoring from turns t "
                "join verdicts v on v.id = t.live_verdict_id "
                "join matches m on m.id = t.match_id "
                "where t.match_id = any(%s) and t.actor = m.winner "
                "and t.outcome in ('accept', 'semantic_uncertain')",
                (won_ids,),
            )
        ).fetchall()
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
                    rank=await account_rank(conn, account_id, slug),
                )
            )

    ended_all = sorted((m for m in ranked if m["status"] == "ended"), key=lambda m: m["ended_at"])
    streak, best_streak = streaks([m["won"] for m in ended_all])
    shown = [m for m in matches if is_yours or (m["status"] == "ended" and m["is_public"])]
    best_ids = _best_ids([m for m in shown if m["kind"] == "house"], scorings, service)
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


def _best_ids(matches: list[Any], scorings: list[Any], service: MatchService) -> list[str]:
    """Won duels ranked by the player's highest-scoring move; curated duels first."""
    peak: dict[str, int] = {}
    template_of = {m["id"]: m["template_id"] for m in matches}
    for row in scorings:
        if row["match_id"] not in template_of:
            continue
        weights = service.templates[template_of[row["match_id"]]].weights
        score = weighted_total(row["scoring"]["scores"], weights)
        peak[row["match_id"]] = max(peak.get(row["match_id"], 0), score)
    won = [m for m in matches if m["status"] == "ended" and m["won"]]
    won.sort(key=lambda m: (m["is_curated"], peak.get(m["id"], 0)), reverse=True)
    return [m["id"] for m in won[:BEST_SHOWN]]


def _row(m: Any, service: MatchService) -> DuelRow:
    template = service.templates[m["template_id"]]
    if m["judged"] == 0:
        length = "no moves"
    elif template.mode == "showcase":
        rounds = m["judged"] // m["seat_count"]
        length = f"{rounds} {'round' if rounds == 1 else 'rounds'}"
    else:
        length = f"{m['judged']} {'move' if m['judged'] == 1 else 'moves'}"
    won = None if m["status"] != "ended" else m["won"] is True
    return DuelRow(
        id=m["id"],
        title=template.title,
        created_at=m["created_at"].isoformat(),
        status=m["status"],
        length=length,
        result=_result(m, template.mode),
        won=won,
        is_public=m["is_public"],
        against=", ".join(name or "The House" for name in m["others"] or []),
    )


def _result(m: Any, mode: str) -> str:
    if m["status"] == "open":
        return "Waiting for players"
    if m["status"] in OPEN:
        return "On stage"
    if m["end_reason"] == "unfilled":
        return "Nobody joined"
    if m["status"] == "abandoned":
        return "Closed, no move for a day"
    won = m["won"] is True
    if m["end_reason"] in ("move_cap_points", "rounds_complete"):
        if m["won"] is None:
            return f"Drawn {m['my_points']} all"
        if mode == "showcase":
            return f"{'Won' if won else 'Lost'} {m['my_points']} to {m['their_points']}"
        return "Won on points" if won else "Lost on points"
    if m["end_reason"] == "resign":
        if won:
            return "The House resigned" if m["kind"] == "house" else "Last one standing"
        return "Resigned"
    if m["end_reason"] == "forfeit":
        return "Last one standing" if won else "Out of turns"
    return "Victory" if won else f"Fell in round {m['my_moves']}"
