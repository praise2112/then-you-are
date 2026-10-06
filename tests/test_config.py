from arena_server.config import load_model, load_settings


def test_a_model_base_url_reads_its_host_from_the_environment(monkeypatch):
    monkeypatch.setenv("HOUSE_BASE_URL", "https://house.example")
    assert load_model("student-cloud").base_url == "https://house.example/v1"


def test_every_model_role_can_call_deepseek_directly(monkeypatch):
    monkeypatch.setenv("JUDGE_REF", "judge-v1-direct")
    monkeypatch.setenv("FALLBACK_REF", "opponent-v1-direct")
    settings = load_settings()
    for ref in (settings.judge_ref, settings.fallback_ref):
        spec = load_model(ref)
        assert (spec.base_url, spec.api_key_env) == ("https://api.deepseek.com", "DEEPSEEK_API_KEY")
