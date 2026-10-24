# -*- coding: utf-8 -*-
"""Restore a pre-authenticated Epic Games browser session from a secret.

Epic's login is protected by an hCaptcha that the bundled solver cannot beat reliably
(measured: 43 failures / 45 attempts, because hCaptcha serves challenge prompts that the
`hcaptcha-challenger` prompt mapper does not know, so the vision model receives an empty
question and guesses). Solving it on a server is a dead end.

Instead we authenticate once, on a workstation, where a human solves the challenge in a
real browser (see ``tools/bootstrap_epic_cookies.py``), and store the resulting cookies as
the ``EPIC_COOKIES`` secret. Injecting them before the page loads makes
``EpicAuthorization.invoke()`` take its "already logged in" short-circuit, so no login
captcha is ever requested.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Any

from loguru import logger
from playwright.async_api import BrowserContext

_ENV_NAME = "EPIC_COOKIES"
_ALLOWED_SAME_SITE = {"Strict", "Lax", "None"}


def _decode_payload(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        return json.loads(text)
    try:
        decoded = base64.b64decode(text, validate=False).decode("utf-8", "replace").strip()
    except (binascii.Error, UnicodeDecodeError) as err:
        raise ValueError(
            f"{_ENV_NAME} must be raw JSON or base64-encoded JSON, got {text[:16]!r}..."
        ) from err
    return json.loads(decoded)


def _normalise_cookie(cookie: dict) -> dict | None:
    name = cookie.get("name")
    value = cookie.get("value")
    if not name or value is None:
        return None

    item: dict[str, Any] = {
        "name": str(name),
        "value": str(value),
        "domain": cookie.get("domain") or ".epicgames.com",
        "path": cookie.get("path") or "/",
    }

    expires = cookie.get("expires", cookie.get("expirationDate"))
    if isinstance(expires, (int, float)) and expires > 0:
        item["expires"] = float(expires)
    else:
        item["expires"] = -1

    for source, target in (("httpOnly", "httpOnly"), ("secure", "secure")):
        if source in cookie:
            item[target] = bool(cookie[source])

    same_site = cookie.get("sameSite")
    if same_site in _ALLOWED_SAME_SITE:
        item["sameSite"] = same_site

    return item


def load_session_cookies() -> list[dict]:
    """Return the cookies carried by ``EPIC_COOKIES``; empty list when unset."""
    raw = os.getenv(_ENV_NAME, "")
    if not raw.strip():
        return []

    payload = _decode_payload(raw)
    if isinstance(payload, dict):
        payload = payload.get("cookies", [])
    if not isinstance(payload, list):
        raise ValueError(f"{_ENV_NAME} must contain a JSON array of cookies")

    cookies: list[dict] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        normalised = _normalise_cookie(entry)
        if normalised is not None:
            cookies.append(normalised)
    return cookies


async def apply_session_cookies(context: BrowserContext) -> int:
    """Inject the stored Epic session into ``context``; returns the cookie count applied."""
    cookies = load_session_cookies()
    if not cookies:
        logger.info("{} is not configured; Epic login will be attempted in-browser", _ENV_NAME)
        return 0

    await context.add_cookies(cookies)
    logger.success("Restored {} Epic session cookie(s) from {}", len(cookies), _ENV_NAME)
    return len(cookies)
