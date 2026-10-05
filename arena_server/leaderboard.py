"""Per-game standings for signed-in players, ranked by wins, and account streaks."""

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from arena_core.template import Template
from arena_server.db import Pool
from arena_server.views import BoardSummary, BoardView, StandingView

MIN_PLAYED = 3
TOP = 20


async def board(pool: Pool, template: Template) -> BoardView:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                "select a.id as account_id, a.display_name, a.avatar_url, "
                "count(*) filter (where m.winner = se.seat) as wins, count(*) as played "
                "from matches m "
                "join seats se on se.match_id = m.id and se.kind = 'human' "
                "join sessions s on s.session_key = se.session_key "
                "join accounts a on a.id = s.account_id "
                "where m.status = 'ended' and m.kind = 'house' and m.template_id = %s "
                "group by a.id, a.display_name, a.avatar_url "
                "having count(*) >= %s "
                "order by wins desc, played asc, a.display_name limit %s",
                (template.slug, MIN_PLAYED, TOP),
            )
        ).fetchall()
    return BoardView(
        slug=template.slug,
        title=template.title,
        emblem=template.emblem,
        accent=template.accent,
        standings=[
            StandingView(
                rank=rank,
                account_id=row["account_id"],
                display_name=row["display_name"],
                avatar_url=row["avatar_url"],
                wins=row["wins"],
                played=row["played"],
            )
            for rank, row in enumerate(rows, start=1)
        ],
    )


async def boards_index(pool: Pool, templates: dict[str, Template]) -> list[BoardSummary]:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                "select template_id, count(*) as ranked from (select m.template_id, s.account_id "
                "from matches m join seats se on se.match_id = m.id and se.kind = 'human' "
                "join sessions s on s.session_key = se.session_key "
                "where m.status = 'ended' and m.kind = 'house' and s.account_id is not null "
                "group by m.template_id, s.account_id having count(*) >= %s) ranked "
                "group by template_id",
                (MIN_PLAYED,),
            )
        ).fetchall()
    ranked = {row["template_id"]: row["ranked"] for row in rows}
    summaries = []
    for slug, template in templates.items():
        top = await board(pool, template) if ranked.get(slug) else None
        summaries.append(
            BoardSummary(
                slug=slug,
                title=template.title,
                emblem=template.emblem,
                accent=template.accent,
                ranked=ranked.get(slug, 0),
                leader=top.standings[0] if top and top.standings else None,
            )
        )
    return summaries


async def ranked_players(pool: Pool) -> int:
    """Signed-in players ranked on at least one game's board."""
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "select count(distinct account_id) as n from (select m.template_id, s.account_id "
                "from matches m join seats se on se.match_id = m.id and se.kind = 'human' "
                "join sessions s on s.session_key = se.session_key "
                "where m.status = 'ended' and m.kind = 'house' and s.account_id is not null "
                "group by m.template_id, s.account_id having count(*) >= %s) ranked",
                (MIN_PLAYED,),
            )
        ).fetchone()
    return row["n"] if row else 0


def streaks(results: list[bool | None]) -> tuple[int, int]:
    """Current and best win streak over results (won, lost, or None for a draw) in play
    order. A draw leaves both alone."""
    current = best = 0
    for won in results:
        if won is None:
            continue
        current = current + 1 if won else 0
        best = max(best, current)
    return current, best


async def account_streaks(conn: AsyncConnection[DictRow], account_id: str) -> tuple[int, int]:
    rows = await (
        await conn.execute(
            "select m.winner = se.seat as won from matches m "
            "join seats se on se.match_id = m.id and se.kind = 'human' "
            "join sessions s on s.session_key = se.session_key "
            "where s.account_id = %s and m.status = 'ended' and m.kind = 'house' "
            "order by m.ended_at, m.created_at",
            (account_id,),
        )
    ).fetchall()
    return streaks([row["won"] for row in rows])


async def account_rank(
    conn: AsyncConnection[DictRow], account_id: str, template_id: str
) -> int | None:
    """Where the account would sit on that game's board, or None below the entry bar."""
    row = await (
        await conn.execute(
            "with tally as (select a.id, count(*) filter (where m.winner = se.seat) as wins, "
            "count(*) as played, a.display_name from matches m "
            "join seats se on se.match_id = m.id and se.kind = 'human' "
            "join sessions s on s.session_key = se.session_key "
            "join accounts a on a.id = s.account_id "
            "where m.status = 'ended' and m.kind = 'house' and m.template_id = %s "
            "group by a.id, a.display_name having count(*) >= %s) "
            "select 1 + (select count(*) from tally t "
            "where (t.wins, -t.played, t.display_name) "
            "> (mine.wins, -mine.played, mine.display_name)) as rank "
            "from tally mine where mine.id = %s",
            (template_id, MIN_PLAYED, account_id),
        )
    ).fetchone()
    return row["rank"] if row else None
