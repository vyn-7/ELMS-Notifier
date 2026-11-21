#!/usr/bin/env python3
"""Refresh the ELMS session and update the GitHub secret.

The manual mode opens a dedicated visible browser profile. If the ELMS session
is still valid, it exports immediately. If it expired, sign in manually
(including Microsoft MFA); the script never enters credentials or automates
sign-in.

The headless mode reuses the existing Microsoft SSO cookies from
`.session/storage-state.json` (or `ELMS_SSO_COOKIES_JSON`) to obtain a fresh
ELMS session. It also never enters credentials or handles MFA. If Microsoft
asks for either, it stops and the manual mode is required.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import selectors  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

PROFILE_DIR = ROOT / ".session" / "elms-profile"
STORAGE_STATE_PATH = ROOT / ".session" / "storage-state.json"
COOKIE_OUT = ROOT / ".session" / "cookies.json"
SSO_OUT = ROOT / ".session" / "sso-cookies.json"
DEFAULT_URL = f"{selectors.BASE_URL}{selectors.DASHBOARD_PATH}?refresh=1"
SSO_URL = (
    f"{selectors.BASE_URL}/auth/microsoft_office365"
    "?state=%7B%22visitor_portal_id%22%3A1200%7D"
)


def _require_display() -> None:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit(
            "No graphical session detected. Run this from a terminal inside your Linux desktop session."
        )


def _elms_cookies(cookies: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        cookie
        for cookie in cookies
        if str(cookie.get("domain") or "").lstrip(".") == "elms.sti.edu"
    ]


def _microsoft_cookies(cookies: list[dict[str, object]]) -> list[dict[str, object]]:
    domains = ("microsoftonline.com", "microsoft.com", "live.com")
    return [
        cookie
        for cookie in cookies
        if str(cookie.get("domain") or "").lstrip(".").lower().endswith(domains)
    ]


def _load_sso_cookies(path: str | None) -> list[dict[str, object]]:
    if path:
        raw = Path(path).read_text(encoding="utf-8")
    elif os.getenv("ELMS_SSO_COOKIES_JSON", "").strip():
        raw = os.environ["ELMS_SSO_COOKIES_JSON"]
    elif STORAGE_STATE_PATH.exists():
        raw = STORAGE_STATE_PATH.read_text(encoding="utf-8")
    else:
        raise SystemExit(
            "No Microsoft SSO cookies. Set ELMS_SSO_COOKIES_JSON or create "
            ".session/storage-state.json with scripts/refresh_session.py."
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit("SSO cookie data is not valid JSON") from exc
    entries = data.get("cookies", []) if isinstance(data, dict) else data
    cookies = _microsoft_cookies(
        [cookie for cookie in entries if isinstance(cookie, dict)]
    )
    if not cookies:
        raise SystemExit("No Microsoft SSO cookies found; sign in manually once.")
    return cookies


def _write_cookie_jar(path: Path, cookies: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cookies": cookies}, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def _update_gh_secret(name: str, path: Path, repo: str | None) -> None:
    command = ["gh", "secret", "set", name]
    if repo:
        command += ["--repo", repo]
    try:
        subprocess.run(
            command,
            input=path.read_text(encoding="utf-8"),
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit("gh CLI not found. Install GitHub CLI and run gh auth login.") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"Failed to update {name} (gh exited {exc.returncode}). Run gh auth login and retry."
        ) from exc


async def refresh_headless(
    timeout: int,
    repo: str | None,
    update_secret: bool,
    sso_path: str | None,
) -> int:
    """Renew the ELMS session from an existing Microsoft SSO cookie."""
    cookies = _load_sso_cookies(sso_path)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await browser.new_context()
        await context.add_cookies(cookies)  # type: ignore[arg-type]
        page = await context.new_page()
        try:
            await page.goto(SSO_URL, wait_until="domcontentloaded", timeout=60_000)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                try:
                    body_class = await page.eval_on_selector("body", "el => el.className") or ""
                except Exception:
                    body_class = ""
                expired = any(
                    fragment in page.url
                    for fragment in selectors.SESSION_EXPIRED_URL_FRAGMENTS
                )
                if selectors.LOGGED_IN_BODY_CLASS in body_class and not expired:
                    break
                await page.wait_for_timeout(1_000)
            else:
                print("Microsoft SSO did not complete; manual refresh is required.")
                return 2

            all_cookies = [dict(cookie) for cookie in await context.cookies()]
            elms = _elms_cookies(all_cookies)
            if not elms:
                print("No elms.sti.edu cookies were found after SSO.")
                return 2
            sso = _microsoft_cookies(all_cookies)
            _write_cookie_jar(COOKIE_OUT, elms)
            print(f"Wrote {len(elms)} ELMS cookie(s) to {COOKIE_OUT}")
            if sso:
                _write_cookie_jar(SSO_OUT, sso)
                print(f"Wrote {len(sso)} Microsoft SSO cookie(s) to {SSO_OUT}")
            if update_secret:
                _update_gh_secret("ELMS_COOKIES", COOKIE_OUT, repo)
                if sso:
                    _update_gh_secret("ELMS_SSO_COOKIES", SSO_OUT, repo)
                    print("Updated GitHub secrets ELMS_COOKIES and ELMS_SSO_COOKIES.")
                else:
                    print("Updated GitHub secret ELMS_COOKIES; no Microsoft cookies to rotate.")
            else:
                print("Skipped GitHub secret update (--no-secret).")
            return 0
        finally:
            await browser.close()


async def refresh(url: str, timeout: int, repo: str | None, update_secret: bool) -> int:
    _require_display()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"Session profile: {PROFILE_DIR}")
    print("Keep this folder private; it is gitignored and contains login cookies.")

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=["--start-maximized", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            waiting_announced = False
            while loop.time() < deadline:
                href = page.url
                try:
                    body_class = await page.eval_on_selector("body", "el => el.className") or ""
                except Exception:
                    body_class = ""
                expired = any(fragment in href for fragment in selectors.SESSION_EXPIRED_URL_FRAGMENTS)
                if selectors.LOGGED_IN_BODY_CLASS in body_class and not expired:
                    break
                if not waiting_announced:
                    print("Sign in to ELMS in the browser window. Waiting for the dashboard...")
                    waiting_announced = True
                await page.wait_for_timeout(1_000)
            else:
                print(f"Timed out after {timeout}s waiting for the ELMS dashboard.")
                return 1

            try:
                username = (await page.locator(selectors.LOGGED_IN_USERNAME).first.inner_text()).strip()
            except Exception:
                username = ""
            print(f"Signed in as: {username or 'unknown'}")
            answer = await asyncio.to_thread(
                input, "Is this the correct ELMS account? [y/N]: "
            )
            if answer.strip().lower() not in {"y", "yes"}:
                print("Aborted; no cookie file or GitHub secret was changed.")
                return 1

            await context.storage_state(path=str(STORAGE_STATE_PATH))
            all_cookies = [dict(cookie) for cookie in await context.cookies()]
            cookies = _elms_cookies(all_cookies)
            if not cookies:
                print("No elms.sti.edu cookies were found; sign-in may not have completed.")
                return 1
            sso = _microsoft_cookies(all_cookies)

            _write_cookie_jar(COOKIE_OUT, cookies)
            print(f"Wrote {len(cookies)} ELMS cookie(s) to {COOKIE_OUT}")
            if sso:
                _write_cookie_jar(SSO_OUT, sso)
                print(f"Wrote {len(sso)} Microsoft SSO cookie(s) to {SSO_OUT}")
            if update_secret:
                _update_gh_secret("ELMS_COOKIES", COOKIE_OUT, repo)
                if sso:
                    _update_gh_secret("ELMS_SSO_COOKIES", SSO_OUT, repo)
                    print("Updated GitHub secrets ELMS_COOKIES and ELMS_SSO_COOKIES.")
                else:
                    print("Updated GitHub secret ELMS_COOKIES; no Microsoft cookies to rotate.")
            else:
                print("Skipped GitHub secret update (--no-secret).")
            return 0
        finally:
            await context.close()


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="ELMS dashboard URL")
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="seconds to wait for sign-in (default: 300)",
    )
    parser.add_argument("--repo", help="GitHub repository for gh secret set (e.g. owner/name)")
    parser.add_argument(
        "--no-secret",
        action="store_true",
        help="write the cookie file but do not update the GitHub secret",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="renew from saved Microsoft SSO cookies; no visible browser",
    )
    parser.add_argument(
        "--sso-cookies",
        help="JSON file with Microsoft cookies (default: ELMS_SSO_COOKIES_JSON or storage-state.json)",
    )
    parser.add_argument(
        "--export-sso",
        action="store_true",
        help="print the saved Microsoft SSO cookies for gh secret set",
    )
    args = parser.parse_args()
    try:
        if args.export_sso:
            print(
                json.dumps(
                    {"cookies": _load_sso_cookies(args.sso_cookies), "origins": []},
                    separators=(",", ":"),
                )
            )
            return 0
        if args.headless:
            return asyncio.run(
                refresh_headless(
                    args.timeout,
                    args.repo,
                    update_secret=not args.no_secret,
                    sso_path=args.sso_cookies,
                )
            )
        return asyncio.run(
            refresh(args.url, args.timeout, args.repo, update_secret=not args.no_secret)
        )
    except KeyboardInterrupt:
        print("\nAborted. The saved session profile was left in place.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())