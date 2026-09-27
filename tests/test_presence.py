import dataclasses
import json
import os

import psycopg
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from arena_server.app import build_app
from arena_server.config import load_settings
from tests.conftest import FakeCaller

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="needs TEST_DATABASE_URL pointing at Postgres",
    ),
    pytest.mark.xdist_group("database"),
]


def test_the_socket_counts_who_is_online_and_pushes_the_lobby():
    settings = dataclasses.replace(load_settings(), database_url=os.environ["TEST_DATABASE_URL"])
    with TestClient(build_app(settings, FakeCaller([], []))) as client:
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws") as refused:
            refused.receive_text()

        client.put("/sessions/me", json={"stage_name": "Ana"})
        with client.websocket_connect("/ws") as socket:
            assert json.loads(socket.receive_text()) == {"type": "online", "count": 1}
            assert json.loads(socket.receive_text())["type"] == "lobby"
            table = client.post("/tables/quick", json={"template_id": "front-page", "seats": 3})
            pushed = json.loads(socket.receive_text())
            assert pushed["type"] == "lobby"
            assert table.json()["match_id"] in {t["id"] for t in pushed["tables"]}
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "update matches set status = 'abandoned' where id = %s", (table.json()["match_id"],)
        )
