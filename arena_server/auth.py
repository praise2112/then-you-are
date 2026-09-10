"""Social sign-in through Authlib: one login and one callback route per configured provider."""

import secrets
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

from authlib.integrations.starlette_client import OAuth, StarletteOAuth2App
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from arena_server.config import Settings
from arena_server.db import Pool

SESSION_COOKIE = "oddstage_session"
COOKIE_AGE = 60 * 60 * 24 * 365

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


Outcome = Literal["signed_in", "linked", "taken"]


async def link_account(
    pool: Pool, session_key: str | None, profile: Profile
) -> tuple[str, Outcome, str]:
    """Signs the session in. Returns the session key, what happened, and a name for the notice.

    A known identity signs into its account. A new identity joins the session's account when
    the session is already signed in, otherwise it opens a new account. A signed-in session
    meeting an identity that belongs to another account is left alone ("taken")."""
    key = session_key or secrets.token_urlsafe(24)
    async with pool.connection() as conn:
        current = await (
            await conn.execute("select account_id from sessions where session_key = %s", (key,))
        ).fetchone()
        current_id = current["account_id"] if current else None
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
        outcome: Outcome = "signed_in"
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
        request.session["next"] = next if next.startswith("/") else "/"
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
        response.set_cookie(SESSION_COOKIE, key, httponly=True, samesite="lax", max_age=COOKIE_AGE)
        return response

    @app.post("/auth/logout", status_code=204)
    async def logout(response: Response) -> Response:
        """Signing out hands the browser a fresh guest session; the account keeps the old one."""
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE)
        return response
