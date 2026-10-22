import os

import psycopg
import pytest

from arena_server.page_views import device_class, section_of, source_of
from tests.conftest import FakeCaller, run_app


def test_a_replay_page_counts_under_its_section_not_its_match():
    assert section_of("/r/abc123") == "/r"
    assert section_of("/") == "/"


def test_the_source_is_the_referring_site_and_blank_for_the_site_itself():
    assert source_of("https://www.linkedin.com/feed/", "thenyouare.com") == "linkedin.com"
    assert source_of("https://thenyouare.com/r/x", "thenyouare.com") == ""


def test_link_preview_crawlers_count_as_automated():
    assert device_class("LinkedInBot/1.0") == "automated"
    assert device_class("Mozilla/5.0 (iPhone)") == "mobile"


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL")
@pytest.mark.xdist_group("database")
@pytest.mark.anyio
async def test_each_page_served_adds_one_view_and_api_calls_add_none():
    app, manager, client = await run_app(FakeCaller(rulings=[], opponent_moves=[]))
    source = "page-views-test.example"
    headers = {"referer": f"https://{source}/", "cf-ipcountry": "FR", "user-agent": "Mozilla/5.0"}
    try:
        await client.get("/", headers=headers)
        await client.get("/", headers=headers)
        await client.get("/healthz", headers=headers)
    finally:
        await client.aclose()
        await manager.__aexit__(None, None, None)

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        rows = conn.execute(
            "select section, country, device, views from page_views where source = %s", (source,)
        ).fetchall()
        conn.execute("delete from page_views where source = %s", (source,))
    assert rows == [("/", "FR", "desktop", 2)]
