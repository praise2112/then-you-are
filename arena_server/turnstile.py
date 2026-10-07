"""Cloudflare Turnstile: checks the token a browser got from the widget."""

import logging

import httpx

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
# Cloudflare's published test keys, which always pass; used when the site is not on https.
TEST_SITE_KEY = "1x00000000000000000000AA"
TEST_SECRET_KEY = "1x0000000000000000000000000000000AA"

log = logging.getLogger(__name__)


async def passes_turnstile(secret: str, token: str) -> bool:
    """True when Cloudflare accepts the token; False for an empty token or no answer."""
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(SITEVERIFY_URL, data={"secret": secret, "response": token})
            resp.raise_for_status()
            return resp.json().get("success") is True
    except (httpx.HTTPError, ValueError) as e:
        log.warning("turnstile siteverify failed: %s", e)
        return False
