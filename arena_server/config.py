"""Environment settings and the model registry."""

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import yaml

from arena_judge.caller import ModelSpec

MODELS_PATH = Path(__file__).parent / "models.yaml"


@dataclass(frozen=True)
class Settings:
    database_url: str
    openrouter_api_key: str
    judge_ref: str
    opponent_ref: str
    public_base_url: str
    frontend_dist: Path | None
    curator_token: str
    featured_template: str
    session_secret: str
    oauth_clients: dict[str, tuple[str, str]]


def load_settings() -> Settings:
    dist = Path(os.environ.get("FRONTEND_DIST", Path(__file__).parents[1] / "frontend" / "dist"))
    return Settings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://oddstage:oddstage@localhost:5433/oddstage"
        ),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        judge_ref=os.environ.get("JUDGE_REF", "judge-v1"),
        opponent_ref=os.environ.get("OPPONENT_REF", "opponent-v1"),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        frontend_dist=dist if (dist / "index.html").exists() else None,
        curator_token=os.environ.get("CURATOR_TOKEN", ""),
        featured_template=os.environ.get("FEATURED_TEMPLATE", "then-i-am"),
        session_secret=os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(32),
        oauth_clients=_oauth_clients(),
    )


def _oauth_clients() -> dict[str, tuple[str, str]]:
    """Providers with both an id and a secret in the environment, in sign-in menu order."""
    found = {}
    for provider in ("google", "github", "discord"):
        client_id = os.environ.get(f"{provider.upper()}_CLIENT_ID", "")
        secret = os.environ.get(f"{provider.upper()}_CLIENT_SECRET", "")
        if client_id and secret:
            found[provider] = (client_id, secret)
    return found


def load_model(ref: str) -> ModelSpec:
    registry = yaml.safe_load(MODELS_PATH.read_text())
    if ref not in registry:
        raise KeyError(f"no model named {ref} in models.yaml")
    return ModelSpec.model_validate(registry[ref])
