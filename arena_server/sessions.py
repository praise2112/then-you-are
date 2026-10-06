"""Player sessions: making and naming one, the account behind it, the seat it holds at a match,
and the unfinished matches it can return to."""

import secrets
from typing import TYPE_CHECKING

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from arena_server.db import Pool
from arena_server.leaderboard import account_streaks
from arena_server.store import Record
from arena_server.views import AccountView, OpenDuel, SessionView, TableKind

if TYPE_CHECKING:
    from arena_server.matches import MatchService


def new_session_key() -> str:
    return secrets.token_urlsafe(24)


async def account_of(conn: AsyncConnection[DictRow], session_key: str | None) -> str | None:
    """The account the session is signed in to, or None for a guest or an unknown key."""
    row = await (
        await conn.execute("select account_id from sessions where session_key = %s", (session_key,))
    ).fetchone()
    return row["account_id"] if row else None


async def ensure_session(
    pool: Pool, session_key: str | None, stage_name: str | None, list_duels: bool | None = None
) -> str:
    key = session_key or new_session_key()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into sessions (session_key, stage_name, list_duels) "
            "values (%s, %s, coalesce(%s, false)) on conflict (session_key) do update "
            "set stage_name = coalesce(%s, sessions.stage_name), "
            "list_duels = coalesce(%s, sessions.list_duels)",
            (
                key,
                stage_name or "Challenger",
                list_duels,
                stage_name,
                list_duels,
            ),
        )
    return key


async def lists_duels(pool: Pool, session_key: str) -> bool:
    """Whether the session has chosen to list its duels on the stage."""
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "select list_duels from sessions where session_key = %s", (session_key,)
            )
        ).fetchone()
    return bool(row and row["list_duels"])


async def session_view(service: "MatchService", session_key: str | None) -> SessionView:
    if session_key:
        async with service.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "select s.stage_name, s.list_duels, a.id, a.display_name, a.avatar_url, "
                    "(select coalesce(array_agg(i.provider order by i.created_at), '{}') "
                    "from identities i where i.account_id = a.id) as providers "
                    "from sessions s left join accounts a on a.id = s.account_id "
                    "where s.session_key = %s",
                    (session_key,),
                )
            ).fetchone()
            account = None
            if row and row["id"]:
                streak, best = await account_streaks(conn, row["id"])
                account = AccountView(
                    id=row["id"],
                    providers=row["providers"],
                    display_name=row["display_name"],
                    avatar_url=row["avatar_url"],
                    streak=streak,
                    best_streak=best,
                )
        if row:
            return SessionView(
                stage_name=row["stage_name"],
                list_duels=row["list_duels"],
                account=account,
                open_duels=[
                    await open_duel(service, m, session_key)
                    for m in await open_match_ids(service.pool, session_key)
                ],
            )
    return SessionView(stage_name="Challenger", list_duels=False)


async def open_match_ids(
    pool: Pool, session_key: str, template_id: str | None = None, kind: TableKind | None = None
) -> list[str]:
    """Unfinished matches where the session, or any session on its account, holds a live
    seat."""
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                "select distinct m.id, m.created_at from matches m "
                "join seats se on se.match_id = m.id "
                "join sessions s on s.session_key = se.session_key "
                "where m.status in ('open', 'active', 'awaiting_judgment', 'paused') "
                "and se.eliminated_at is null "
                "and (s.session_key = %s or s.account_id = "
                "(select account_id from sessions where session_key = %s)) "
                "and (%s::text is null or m.template_id = %s) "
                "and (%s::text is null or m.kind = %s) order by m.created_at",
                (session_key, session_key, template_id, template_id, kind, kind),
            )
        ).fetchall()
    return [row["id"] for row in rows]


async def open_duel(service: "MatchService", match_id: str, session_key: str) -> OpenDuel:
    match, rec = await service.load(match_id)
    template = rec.template
    mine = await seat_of(service.pool, rec, session_key)
    if match.status == "open":
        line = f"waiting for {rec.seats_wanted - len(rec.seats)} more"
    elif template.mode == "showcase":
        line = f"round {min(match.round_n, template.rounds_budget)} of "
        line += f"{template.rounds_budget}, {match.card}"
        if match.phase == "guess" and mine in match.owed_guesses():
            line += ", your call"
    else:
        form = match.standing_form
        if form.lower().startswith(template.move_constraints.prefix.lower()):
            form = form[len(template.move_constraints.prefix) :]
        line = f"round {match.round_n}, {form.rstrip('.')} stands"
        if match.clocked and match.to_move == mine:
            line += ", your move"
    return OpenDuel(id=match.id, title=template.title, line=line)


async def seat_of(pool: Pool, rec: Record, session_key: str | None) -> str | None:
    """The seat held by the session, or by any session signed in to the same account."""
    if session_key is None:
        return None
    for row in rec.humans:
        if row.session_key == session_key:
            return row.seat
    if not any(row.account_id for row in rec.humans):
        return None
    async with pool.connection() as conn:
        account = await account_of(conn, session_key)
    return next((r.seat for r in rec.humans if account and r.account_id == account), None)
