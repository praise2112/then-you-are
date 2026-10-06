import json
from pathlib import Path

from arena_server.app import app

COMMITTED = Path(__file__).parents[1] / "frontend" / "openapi.json"


def test_the_committed_openapi_matches_the_server():
    assert app.openapi() == json.loads(COMMITTED.read_text())
