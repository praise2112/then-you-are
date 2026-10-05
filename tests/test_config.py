from arena_server.config import load_model


def test_a_model_base_url_reads_its_host_from_the_environment(monkeypatch):
    monkeypatch.setenv("HOUSE_BASE_URL", "https://house.example")
    assert load_model("student-cloud").base_url == "https://house.example/v1"
