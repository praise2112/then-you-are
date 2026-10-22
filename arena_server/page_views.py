"""Counts the HTML pages served, per day, without cookies or IP addresses."""

import logging
import re
from urllib.parse import urlsplit

from psycopg import Error
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from arena_server.db import Pool

log = logging.getLogger(__name__)

AUTOMATED = re.compile(
    r"bot|crawl|spider|preview|slack|linkedin|facebookexternalhit|whatsapp|telegram|discord|"
    r"skype|curl|wget|python|headless",
    re.IGNORECASE,
)


def device_class(user_agent: str) -> str:
    if AUTOMATED.search(user_agent):
        return "automated"
    return "mobile" if re.search(r"mobile|android|iphone", user_agent, re.IGNORECASE) else "desktop"


def section_of(path: str) -> str:
    """`/r/abc123` counts as `/r`, so a section never grows one row per match."""
    first = path.strip("/").split("/", 1)[0]
    return "/" + first


def source_of(referer: str, own_host: str) -> str:
    host = urlsplit(referer).hostname or ""
    host = host.removeprefix("www.")
    return "" if host == own_host.removeprefix("www.") else host


class PageViews:
    """ASGI middleware: after a GET answered with an HTML page, adds one to that day's count."""

    def __init__(self, app: ASGIApp, pool: Pool):
        self.app = app
        self.pool = pool

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "GET":
            await self.app(scope, receive, send)
            return

        served_page = False

        async def watch(message: Message) -> None:
            nonlocal served_page
            if message["type"] == "http.response.start":
                content_type = Headers(raw=message["headers"]).get("content-type", "")
                served_page = message["status"] == 200 and content_type.startswith("text/html")
            await send(message)

        await self.app(scope, receive, watch)
        if served_page:
            await self._record(scope)

    async def _record(self, scope: Scope) -> None:
        headers = Headers(scope=scope)
        row = {
            "section": section_of(scope["path"]),
            "source": source_of(headers.get("referer", ""), headers.get("host", "").split(":")[0]),
            "country": headers.get("cf-ipcountry", ""),
            "device": device_class(headers.get("user-agent", "")),
        }
        try:
            async with self.pool.connection() as conn:
                await conn.execute(
                    "insert into page_views (section, source, country, device) "
                    "values (%(section)s, %(source)s, %(country)s, %(device)s) "
                    "on conflict (day, section, source, country, device) "
                    "do update set views = page_views.views + 1",
                    row,
                )
        except Error:
            log.warning("page view not counted", exc_info=True)
