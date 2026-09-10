"""Social sign-in through Authlib: one login and one callback route per configured provider."""

import secrets
from dataclasses import dataclass

from authlib.integrations.starlette_client import OAuth, StarletteOAuth2App
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from arena_server.config import Settings
from arena_server.db import Pool

SESSION_COOKIE = "oddstage_session"
COOKIE_AGE = 60 * 60 * 24 * 365

PROVIDERS: dict[str, dict] = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "access_token_url": "https://oauth2.googleapis.com/token",
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


async def fetch_profile(client: StarletteOAuth2App, provider: str, request: Request) -> Profile:
    token = await client.authorize_access_token(request)
    if provider == "google":
        data = (await client.get("userinfo", token=token)).json()
        return Profile("google", data["sub"], data.get("name") or "Player", data.get("picture", ""))
    if provider == "github":
        data = (await client.get("user", token=token)).json()
        name = data.get("name") or data["login"]
        return Profile("github", str(data["id"]), name, data.get("avatar_url", ""))
    data = (await client.get("users/@me", token=token)).json()
    name = data.get("global_name") or data["username"]
    avatar = data.get("avatar")
    url = f"https://cdn.discordapp.com/avatars/{data['id']}/{avatar}.png" if avatar else ""
    return Profile("discord", data["id"], name, url)


async def link_account(pool: Pool, session_key: str | None, profile: Profile) -> str:
    """Upserts the account, attaches the session to it, and returns the session key."""
    key = session_key or secrets.token_urlsafe(24)
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "insert into accounts (id, provider, provider_id, display_name, avatar_url) "
                "values (%s, %s, %s, %s, %s) on conflict (provider, provider_id) do update "
                "set display_name = excluded.display_name, avatar_url = excluded.avatar_url "
                "returning id",
                (
                    secrets.token_urlsafe(12),
                    profile.provider,
                    profile.provider_id,
                    profile.display_name[:40],
                    profile.avatar_url,
                ),
            )
        ).fetchone()
        assert row is not None
        await conn.execute(
            "insert into sessions (session_key, stage_name, account_id) values (%s, %s, %s) "
            "on conflict (session_key) do update set account_id = excluded.account_id, "
            "stage_name = case when sessions.stage_name = 'Challenger' "
            "then excluded.stage_name else sessions.stage_name end",
            (key, profile.display_name[:40], row["id"]),
        )
    return key


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
        key = await link_account(pool, request.cookies.get(SESSION_COOKIE), profile)
        response = RedirectResponse(request.session.pop("next", "/"), status_code=303)
        response.set_cookie(SESSION_COOKIE, key, httponly=True, samesite="lax", max_age=COOKIE_AGE)
        return response

    @app.post("/auth/logout", status_code=204)
    async def logout(response: Response) -> Response:
        """Signing out hands the browser a fresh guest session; the account keeps the old one."""
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE)
        return response
