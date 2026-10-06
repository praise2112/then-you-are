"""Social sign-in through Authlib: one login and one callback route per configured provider."""

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from urllib.parse import quote

from authlib.integrations.starlette_client import OAuth, StarletteOAuth2App
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from psycopg import AsyncConnection, sql
from psycopg.rows import DictRow
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from arena_server.config import Settings
from arena_server.db import Pool

SESSION_COOKIE = "thenyouare_session"
COOKIE_AGE = 60 * 60 * 24 * 365
GUEST_KEPT = timedelta(days=365)
FINISHED = ("ended", "abandoned")


def set_session_cookie(response: Response, key: str, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE, key, httponly=True, samesite="lax", max_age=COOKIE_AGE, secure=secure
    )


def local_path(next: str) -> str:
    """The post-login target, kept to a path on this site."""
    if not next.startswith("/") or next.startswith(("//", "/\\")):
        return "/"
    return next


class SessionCheck:
    """ASGI middleware: a signed-out session's cookies are hidden from every route, and a live
    session's last visit is recorded, at most once a day."""

    def __init__(self, app: ASGIApp, pool: Pool):
        self.app = app
        self.pool = pool

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            key = HTTPConnection(scope).cookies.get(SESSION_COOKIE)
            if key and await self._revoked(key):
                scope["headers"] = [(k, v) for k, v in scope["headers"] if k != b"cookie"]
        await self.app(scope, receive, send)

    async def _revoked(self, key: str) -> bool:
        async with self.pool.connection() as conn:
            await conn.execute(
                "update sessions set last_seen_at = now() where session_key = %s "
                "and not revoked and last_seen_at < now() - interval '1 day'",
                (key,),
            )
            row = await (
                await conn.execute("select revoked from sessions where session_key = %s", (key,))
            ).fetchone()
        return row is not None and row["revoked"]


PROVIDERS: dict[str, dict] = {
    "google": {
        "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
        "api_base_url": "https://openidconnect.googleapis.com/v1/",
        "client_kwargs": {"scope": "openid profile"},
    },
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "access_token_url": "https://github.com/login/oauth/access_token",
        "api_base_url": "https://api.github.com/",
        "client_kwargs": {"scope": "read:user"},
    },
    "discord": {
        "authorize_url": "https://discord.com/oauth2/authorize",
        "access_token_url": "https://discord.com/api/oauth2/token",
        "api_base_url": "https://discord.com/api/",
        "client_kwargs": {"scope": "identify"},
    },
}


@dataclass(frozen=True)
class Profile:
    provider: str
    provider_id: str
    display_name: str
    avatar_url: str


def first_name(full_name: str | None, fallback: str) -> str:
    """Standings are public, so a legal name is cut to its first word."""
    words = (full_name or "").split()
    return words[0] if words else fallback


async def fetch_profile(client: StarletteOAuth2App, provider: str, request: Request) -> Profile:
    token = await client.authorize_access_token(request)
    if provider == "google":
        data = (await client.get("userinfo", token=token)).json()
        name = data.get("given_name") or first_name(data.get("name"), "Player")
        return Profile("google", data["sub"], name, data.get("picture", ""))
    if provider == "github":
        data = (await client.get("user", token=token)).json()
        return Profile("github", str(data["id"]), data["login"], data.get("avatar_url", ""))
    data = (await client.get("users/@me", token=token)).json()
    name = data.get("global_name") or data["username"]
    avatar = data.get("avatar")
    url = f"https://cdn.discordapp.com/avatars/{data['id']}/{avatar}.png" if avatar else ""
    return Profile("discord", data["id"], name, url)


def new_session_key() -> str:
    return secrets.token_urlsafe(24)


async def account_of(conn: AsyncConnection[DictRow], session_key: str | None) -> str | None:
    """The account the session is signed in to, or None for a guest or an unknown key."""
    row = await (
        await conn.execute("select account_id from sessions where session_key = %s", (session_key,))
    ).fetchone()
    return row["account_id"] if row else None


SignIn = Literal["signed_in", "linked", "taken"]


async def link_account(
    pool: Pool, session_key: str | None, profile: Profile
) -> tuple[str, SignIn, str]:
    """Signs the session in to the identity's account; a new identity joins the session's account
    or opens one. Returns the key, the result ("taken": another account owns it) and a name."""
    key = session_key or new_session_key()
    async with pool.connection() as conn:
        current_id = await account_of(conn, key)
        known = await (
            await conn.execute(
                "select i.account_id, a.display_name from identities i "
                "join accounts a on a.id = i.account_id "
                "where i.provider = %s and i.provider_id = %s",
                (profile.provider, profile.provider_id),
            )
        ).fetchone()
        if known and current_id and known["account_id"] != current_id:
            return key, "taken", known["display_name"]
        outcome: SignIn = "signed_in"
        if known:
            account_id = known["account_id"]
            if current_id:
                outcome = "linked"
        else:
            account_id = current_id
            if account_id is None:
                account_id = secrets.token_urlsafe(12)
                await conn.execute(
                    "insert into accounts (id, display_name, avatar_url) values (%s, %s, %s)",
                    (account_id, profile.display_name[:40], profile.avatar_url),
                )
            else:
                outcome = "linked"
            await conn.execute(
                "insert into identities (provider, provider_id, account_id) values (%s, %s, %s)",
                (profile.provider, profile.provider_id, account_id),
            )
        await conn.execute(
            "update accounts set avatar_url = %s where id = %s and avatar_url = ''",
            (profile.avatar_url, account_id),
        )
        await conn.execute(
            "insert into sessions (session_key, stage_name, account_id) "
            "select %s, display_name, id from accounts where id = %s "
            "on conflict (session_key) do update set account_id = excluded.account_id, "
            "stage_name = excluded.stage_name",
            (key, account_id),
        )
    return key, outcome, profile.display_name


async def rename_account(pool: Pool, session_key: str, display_name: str) -> bool:
    """Renames the account behind the session and every session it owns. False if a guest."""
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "update accounts set display_name = %s where id = "
                "(select account_id from sessions where session_key = %s) returning id",
                (display_name, session_key),
            )
        ).fetchone()
        if row is None:
            return False
        await conn.execute(
            "update sessions set stage_name = %s where account_id = %s", (display_name, row["id"])
        )
    return True


async def erase_sessions(conn: AsyncConnection[DictRow], keys: list[str]) -> bool:
    """Deletes the sessions, their votes and every match only they played. Returns False and
    changes nothing if one is unfinished; a session seated elsewhere becomes "Deleted player"."""
    solo = await (
        await conn.execute(
            "select m.id, m.status from matches m "
            "where exists (select 1 from seats s where s.match_id = m.id "
            "and s.session_key = any(%(keys)s)) "
            "and not exists (select 1 from seats s where s.match_id = m.id "
            "and s.kind = 'human' and s.session_key <> all(%(keys)s))",
            {"keys": keys},
        )
    ).fetchall()
    if any(m["status"] not in FINISHED for m in solo):
        return False
    ids = [m["id"] for m in solo]
    await conn.execute(
        "delete from verdicts where turn_id in (select id from turns where match_id = any(%s))",
        (ids,),
    )
    for table in ("turns", "guesses", "disagreements", "seats"):
        await conn.execute(
            sql.SQL("delete from {} where match_id = any(%s)").format(sql.Identifier(table)),
            (ids,),
        )
    await conn.execute("delete from matches where id = any(%s)", (ids,))
    await conn.execute("delete from disagreements where session_key = any(%s)", (keys,))
    await conn.execute(
        "delete from sessions where session_key = any(%s) and not exists "
        "(select 1 from seats s where s.session_key = sessions.session_key)",
        (keys,),
    )
    await conn.execute(
        "update sessions set stage_name = 'Deleted player', account_id = null, "
        "list_duels = false, revoked = true where session_key = any(%s)",
        (keys,),
    )
    return True


Deletion = Literal["deleted", "guest", "unfinished"]


async def delete_account(pool: Pool, session_key: str) -> Deletion:
    """Deletes the account behind the session, its identities and every match no other human
    played in. Its seats in other people's matches stay, shown as "Deleted player"."""
    async with pool.connection() as conn, conn.transaction():
        account_id = await account_of(conn, session_key)
        if account_id is None:
            return "guest"
        keys = [
            r["session_key"]
            for r in await (
                await conn.execute(
                    "select session_key from sessions where account_id = %s", (account_id,)
                )
            ).fetchall()
        ]
        if not await erase_sessions(conn, keys):
            return "unfinished"
        await conn.execute("delete from identities where account_id = %s", (account_id,))
        await conn.execute("delete from accounts where id = %s", (account_id,))
    return "deleted"


async def forget_stale_guests(pool: Pool) -> int:
    """Erases guest sessions unseen for GUEST_KEPT, skipping any with an unfinished match.
    Returns how many were erased."""
    async with pool.connection() as conn, conn.transaction():
        keys = [
            r["session_key"]
            for r in await (
                await conn.execute(
                    "select s.session_key from sessions s "
                    "where s.account_id is null and not s.revoked and s.last_seen_at < now() - %s "
                    "and not exists (select 1 from seats se join matches m on m.id = se.match_id "
                    "where se.session_key = s.session_key and m.status <> all(%s))",
                    (GUEST_KEPT, list(FINISHED)),
                )
            ).fetchall()
        ]
        if keys:
            await erase_sessions(conn, keys)
    return len(keys)


def mount_auth(app: FastAPI, settings: Settings, pool: Pool) -> None:
    oauth = OAuth()
    for provider, (client_id, secret) in settings.oauth_clients.items():
        oauth.register(provider, client_id=client_id, client_secret=secret, **PROVIDERS[provider])

    def client_of(provider: str) -> StarletteOAuth2App:
        if provider not in settings.oauth_clients:
            raise HTTPException(404, "no such sign-in provider")
        return oauth.create_client(provider)

    @app.get("/auth/{provider}/login", include_in_schema=False)
    async def login(provider: str, request: Request, next: str = "/") -> Response:
        client = client_of(provider)
        request.session["next"] = local_path(next)
        redirect_uri = str(request.url_for("callback", provider=provider))
        return await client.authorize_redirect(request, redirect_uri)

    @app.get("/auth/{provider}/callback", include_in_schema=False, name="callback")
    async def callback(provider: str, request: Request) -> Response:
        profile = await fetch_profile(client_of(provider), provider, request)
        key, outcome, name = await link_account(pool, request.cookies.get(SESSION_COOKIE), profile)
        target = request.session.pop("next", "/")
        if outcome != "signed_in":
            joiner = "&" if "?" in target else "?"
            target = f"{target}{joiner}account={outcome}:{provider}:{quote(name)}"
        response = RedirectResponse(target, status_code=303)
        set_session_cookie(response, key, settings.secure_cookies)
        return response

    @app.post("/auth/logout", status_code=204)
    async def logout(request: Request) -> Response:
        """Signing out revokes the session for good; the account keeps its matches."""
        key = request.cookies.get(SESSION_COOKIE)
        if key:
            async with pool.connection() as conn:
                await conn.execute(
                    "update sessions set revoked = true where session_key = %s", (key,)
                )
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE)
        return response
