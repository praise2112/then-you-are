"""A player's programme: record per game, badges, every duel, best duels."""

from typing import Any

from arena_core.state import weighted_total
from arena_server.leaderboard import account_rank, streaks
from arena_server.matches import MatchError, MatchService
from arena_server.views import BadgeCount, DuelRow, GameRecord, ProfileView

OPEN = ("active", "awaiting_judgment", "paused")
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
                "select m.id, m.template_id, m.status, m.winner, m.end_reason, m.points_p1, "
                "m.points_p2, m.created_at, m.ended_at, m.is_public, m.is_curated, "
                "(select count(*) from turns t where t.match_id = m.id and t.seq is not null "
                "and t.outcome in ('accept', 'fail', 'semantic_uncertain')) as judged, "
                "(select count(*) from turns t where t.match_id = m.id and t.actor = 'p1' "
                "and t.outcome in ('accept', 'fail', 'semantic_uncertain')) as p1_moves "
                "from matches m join sessions s on s.session_key = m.p1_session_key "
                "where s.account_id = %s order by m.created_at desc",
                (account_id,),
            )
        ).fetchall()
        badge_rows = await (
            await conn.execute(
                "select b as name, count(*) as count from turns t "
                "join verdicts v on v.id = t.live_verdict_id "
                "join matches m on m.id = t.match_id "
                "join sessions s on s.session_key = m.p1_session_key "
                "cross join jsonb_array_elements_text(v.host -> 'badges') b "
                "where s.account_id = %s and t.actor = 'p1' and m.status = 'ended' "
                "group by b order by count desc, b",
                (account_id,),
            )
        ).fetchall()
        won_ids = [m["id"] for m in matches if m["status"] == "ended" and m["winner"] == "p1"]
        scorings = await (
            await conn.execute(
                "select t.match_id, v.scoring from turns t "
                "join verdicts v on v.id = t.live_verdict_id "
                "where t.match_id = any(%s) and t.actor = 'p1' "
                "and t.outcome in ('accept', 'semantic_uncertain')",
                (won_ids,),
            )
        ).fetchall()
        records = []
        for slug, template in service.templates.items():
            ended = [m for m in matches if m["template_id"] == slug and m["status"] == "ended"]
            if not ended:
                continue
            _, best = streaks([m["winner"] for m in sorted(ended, key=lambda m: m["ended_at"])])
            records.append(
                GameRecord(
                    slug=slug,
                    title=template.title,
                    played=len(ended),
                    won=sum(m["winner"] == "p1" for m in ended),
                    drawn=sum(m["winner"] is None for m in ended),
                    best_streak=best,
                    rank=await account_rank(conn, account_id, slug),
                )
            )

    ended_all = sorted((m for m in matches if m["status"] == "ended"), key=lambda m: m["ended_at"])
    streak, best_streak = streaks([m["winner"] for m in ended_all])
    shown = [m for m in matches if is_yours or (m["status"] == "ended" and m["is_public"])]
    best_ids = _best_ids(shown, scorings, service)
    return ProfileView(
        id=account["id"],
        display_name=account["display_name"],
        avatar_url=account["avatar_url"],
        since=account["created_at"].isoformat(),
        played=len(ended_all),
        won=sum(m["winner"] == "p1" for m in ended_all),
        streak=streak,
        best_streak=best_streak,
        records=records,
        badges=[BadgeCount(name=b["name"], count=b["count"]) for b in badge_rows],
        duels=[_row(m, service) for m in shown],
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
    won = [m for m in matches if m["status"] == "ended" and m["winner"] == "p1"]
    won.sort(key=lambda m: (m["is_curated"], peak.get(m["id"], 0)), reverse=True)
    return [m["id"] for m in won[:BEST_SHOWN]]


def _row(m: Any, service: MatchService) -> DuelRow:
    template = service.templates[m["template_id"]]
    if m["judged"] == 0:
        length = "no moves"
    elif template.mode == "showcase":
        rounds = m["judged"] // 2
        length = f"{rounds} {'round' if rounds == 1 else 'rounds'}"
    else:
        length = f"{m['judged']} {'move' if m['judged'] == 1 else 'moves'}"
    won = None if m["status"] != "ended" else m["winner"] == "p1"
    return DuelRow(
        id=m["id"],
        title=template.title,
        created_at=m["created_at"].isoformat(),
        status=m["status"],
        length=length,
        result=_result(m, template.mode),
        won=won,
        is_public=m["is_public"],
    )


def _result(m: Any, mode: str) -> str:
    if m["status"] in OPEN:
        return "On stage"
    if m["status"] == "abandoned":
        return "Closed, no move for a day"
    won = m["winner"] == "p1"
    if m["end_reason"] in ("move_cap_points", "rounds_complete"):
        if m["winner"] is None:
            return f"Drawn {m['points_p1']} all"
        if mode == "showcase":
            return f"{'Won' if won else 'Lost'} {m['points_p1']} to {m['points_p2']}"
        return "Won on points" if won else "Lost on points"
    if m["end_reason"] == "resign":
        return "The House resigned" if won else "Resigned"
    return "Victory" if won else f"Fell in round {m['p1_moves']}"
