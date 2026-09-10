"""Per-game standings for signed-in players, ranked by wins, and account streaks."""

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from arena_core.template import Template
from arena_server.db import Pool
from arena_server.views import BoardView, StandingView

MIN_PLAYED = 3
TOP = 20


async def leaderboard(pool: Pool, templates: dict[str, Template]) -> list[BoardView]:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                "select m.template_id, a.display_name, a.avatar_url, "
                "count(*) filter (where m.winner = 'p1') as wins, count(*) as played "
                "from matches m "
                "join sessions s on s.session_key = m.p1_session_key "
                "join accounts a on a.id = s.account_id "
                "where m.status = 'ended' "
                "group by m.template_id, a.id, a.display_name, a.avatar_url "
                "having count(*) >= %s "
                "order by wins desc, played asc, a.display_name",
                (MIN_PLAYED,),
            )
        ).fetchall()
    boards = {
        slug: BoardView(slug=slug, title=t.title, standings=[]) for slug, t in templates.items()
    }
    for row in rows:
        board = boards.get(row["template_id"])
        if board is None or len(board.standings) >= TOP:
            continue
        board.standings.append(
            StandingView(
                rank=len(board.standings) + 1,
                display_name=row["display_name"],
                avatar_url=row["avatar_url"],
                wins=row["wins"],
                played=row["played"],
            )
        )
    return list(boards.values())


def streaks(winners: list[str | None]) -> tuple[int, int]:
    """Current and best win streak over results in play order. A draw leaves both alone."""
    current = best = 0
    for winner in winners:
        if winner is None:
            continue
        current = current + 1 if winner == "p1" else 0
        best = max(best, current)
    return current, best


async def account_streaks(conn: AsyncConnection[DictRow], account_id: str) -> tuple[int, int]:
    rows = await (
        await conn.execute(
            "select m.winner from matches m join sessions s on s.session_key = m.p1_session_key "
            "where s.account_id = %s and m.status = 'ended' order by m.ended_at, m.created_at",
            (account_id,),
        )
    ).fetchall()
    return streaks([row["winner"] for row in rows])
