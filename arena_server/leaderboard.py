"""Per-game standings for signed-in players, ranked by wins, and account streaks."""

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from arena_core.template import Template
from arena_server.db import Pool
from arena_server.views import BoardSummary, BoardView, StandingView

MIN_PLAYED = 3
TOP = 20


# Every signed-in player with MIN_PLAYED ended House duels on a game, ranked within that game.
STANDINGS = (
    "with tally as (select m.template_id, a.id as account_id, a.display_name, a.avatar_url, "
    "count(*) filter (where m.winner = se.seat) as wins, count(*) as played "
    "from matches m "
    "join seats se on se.match_id = m.id and se.kind = 'human' "
    "join sessions s on s.session_key = se.session_key "
    "join accounts a on a.id = s.account_id "
    "where m.status = 'ended' and m.kind = 'house' "
    "group by m.template_id, a.id, a.display_name, a.avatar_url "
    "having count(*) >= %(min_played)s), "
    "standings as (select *, row_number() over (partition by template_id "
    "order by wins desc, played asc, display_name, account_id) as rank, "
    "count(*) over (partition by template_id) as ranked from tally) "
)


def _standing(row: DictRow) -> StandingView:
    return StandingView(
        rank=row["rank"],
        account_id=row["account_id"],
        display_name=row["display_name"],
        avatar_url=row["avatar_url"],
        wins=row["wins"],
        played=row["played"],
    )


async def board(pool: Pool, template: Template) -> BoardView:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                STANDINGS + "select * from standings where template_id = %(slug)s "
                "and rank <= %(top)s order by rank",
                {"min_played": MIN_PLAYED, "slug": template.slug, "top": TOP},
            )
        ).fetchall()
    return BoardView(
        slug=template.slug,
        title=template.title,
        emblem=template.emblem,
        accent=template.accent,
        standings=[_standing(row) for row in rows],
    )


async def boards_index(pool: Pool, templates: dict[str, Template]) -> list[BoardSummary]:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                STANDINGS + "select * from standings where rank = 1",
                {"min_played": MIN_PLAYED},
            )
        ).fetchall()
    leaders = {row["template_id"]: row for row in rows}
    return [
        BoardSummary(
            slug=slug,
            title=template.title,
            emblem=template.emblem,
            accent=template.accent,
            ranked=leaders[slug]["ranked"] if slug in leaders else 0,
            leader=_standing(leaders[slug]) if slug in leaders else None,
        )
        for slug, template in templates.items()
    ]


async def ranked_players(pool: Pool) -> int:
    """Signed-in players ranked on at least one game's board."""
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                STANDINGS + "select count(distinct account_id) as n from standings",
                {"min_played": MIN_PLAYED},
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


async def account_ranks(conn: AsyncConnection[DictRow], account_id: str) -> dict[str, int]:
    """The account's place on each game's board by template; games below the bar are absent."""
    rows = await (
        await conn.execute(
            STANDINGS + "select template_id, rank from standings where account_id = %(account_id)s",
            {"min_played": MIN_PLAYED, "account_id": account_id},
        )
    ).fetchall()
    return {row["template_id"]: row["rank"] for row in rows}
