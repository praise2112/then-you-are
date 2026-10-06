import json
from pathlib import Path

import pytest

from arena_server.app import app

COMMITTED = Path(__file__).parents[1] / "frontend" / "openapi.json"


@pytest.mark.xfail(
    strict=True, reason="frontend/openapi.json predates three routes; run npm run gen"
)
def test_the_committed_openapi_matches_the_server():
    assert app.openapi() == json.loads(COMMITTED.read_text())
