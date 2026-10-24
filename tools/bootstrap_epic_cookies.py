#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-time helper: capture a logged-in Epic Games session and print it for the
``EPIC_COOKIES`` GitHub secret.

Why this exists
---------------
Epic's login is guarded by an hCaptcha that a server-side solver cannot pass reliably
(the bundled `hcaptcha-challenger` receives an empty challenge prompt for Epic's newer
challenge types, so the vision model guesses coordinates and fails). A human solves it in
a second. So:

1. Run this script on your own machine (once, or whenever the session expires).
2. Log in to Epic in the window that opens — captcha and 2FA included, by hand.
3. The script waits until the session is verified, then prints a single base64 line.
4. Paste that line into the repository secret named ``EPIC_COOKIES``.

Afterwards the workflow never logs in again, so it never meets the login captcha.

Usage
-----
    pip install playwright
    python tools/bootstrap_epic_cookies.py

A locally installed Chrome or Edge is reused, so no browser download is required. Set
``BOOTSTRAP_BROWSER=chrome`` / ``msedge`` / ``chromium`` to force a specific one.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

LOGIN_URL = (
    "https://www.epicgames.com/id/login?lang=en-US&noHostRedirect=true"
    "&redirectUrl=https%3A%2F%2Fstore.epicgames.com%2Fen-US%2Ffree-games"
)
ORDER_HISTORY_URL = "https://www.epicgames.com/account/v2/payment/ajaxGetOrderHistory"
OUTPUT_PATH = Path("epic_cookies.json")
COOKIE_DOMAIN_MARKERS = ("epicgames.com", "epicgames.dev", "on.epicgames.com")
POLL_SECONDS = 3
TIMEOUT_SECONDS = 600


def _channel_candidates() -> list[str | None]:
    forced = os.getenv("BOOTSTRAP_BROWSER", "").strip().lower()
    if forced in {"chrome", "msedge", "chromium"}:
        return [None if forced == "chromium" else forced]
    return ["msedge", "chrome", None]


async def _launch(playwright, channel: str | None):
    kwargs = {"headless": False, "args": ["--start-maximized"], "no_viewport": True}
    if channel:
        kwargs["channel"] = channel
    return await playwright.chromium.launch(**kwargs)


async def _session_is_live(page) -> bool:
    try:
        await page.goto(ORDER_HISTORY_URL, wait_until="domcontentloaded", timeout=20000)
        body = await page.locator("body").inner_text(timeout=8000)
    except Exception:
        return False

    if '"orders"' not in body:
        return False
    try:
        return isinstance(json.loads(body).get("orders"), list)
    except json.JSONDecodeError:
        return False


async def main() -> int:
    async with async_playwright() as playwright:
        browser = None
        for channel in _channel_candidates():
            try:
                browser = await _launch(playwright, channel)
                print(f"[ok] 已启动浏览器：{channel or 'playwright chromium'}")
                break
            except PlaywrightError as err:
                print(f"[--] {channel or 'chromium'} 不可用：{str(err).splitlines()[0]}", file=sys.stderr)
        if browser is None:
            print(
                "[!!] 找不到本机 Chrome/Edge。请先执行 `playwright install chromium`，"
                "或安装 Chrome/Edge 后重试。",
                file=sys.stderr,
            )
            return 2

        context = await browser.new_context(locale="en-US")
        login_page = await context.new_page()
        await login_page.goto(LOGIN_URL, wait_until="domcontentloaded")

        print()
        print("=" * 66)
        print(" 请在弹出的浏览器窗口里手动登录 Epic（含验证码与两步验证）。")
        print(" 登录完成后不要关闭窗口，脚本会自动检测并保存会话。")
        print("=" * 66)
        print()

        probe = await context.new_page()
        verified = False
        waited = 0
        while waited < TIMEOUT_SECONDS:
            await asyncio.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            if await _session_is_live(probe):
                verified = True
                break
            if waited % 30 == 0:
                print(f"[..] 仍在等待登录完成（{waited}s / {TIMEOUT_SECONDS}s）")

        if not verified:
            print("[!!] 超时：没有检测到有效的 Epic 会话。", file=sys.stderr)
            await browser.close()
            return 3

        cookies = await context.cookies()
        epic_cookies = [
            cookie
            for cookie in cookies
            if any(marker in (cookie.get("domain") or "") for marker in COOKIE_DOMAIN_MARKERS)
        ]
        payload = json.dumps(epic_cookies, ensure_ascii=False, separators=(",", ":"))
        OUTPUT_PATH.write_text(payload, encoding="utf-8")
        encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")

        print()
        print(f"[ok] 会话有效，已保存 {len(epic_cookies)} 个 cookie 到 {OUTPUT_PATH.resolve()}")
        print()
        print("下一步：把这个值粘贴到 GitHub → Settings → Secrets and variables → Actions")
        print("新建 secret，名字 EPIC_COOKIES，内容为下面这一整行（只显示一次）：")
        print()
        print(encoded)
        print()

        await browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
