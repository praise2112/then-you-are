from fastapi.testclient import TestClient

from arena_server.app import app

client = TestClient(app)


def test_healthz_reports_the_game():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "game": "then-i-am"}
