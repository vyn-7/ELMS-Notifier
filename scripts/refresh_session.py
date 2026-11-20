#!/usr/bin/env python3
"""Refresh the ELMS session and update the GitHub secret.

Opens a dedicated visible browser profile. If the ELMS session is still valid,
it exports immediately. If it expired, sign in manually (including Microsoft
MFA); the script never enters credentials or automates sign-in.

After sign-in it prints the account name and asks for confirmation before
writing `.session/cookies.json` and updating `ELMS_COOKIES`.
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
DEFAULT_URL = f"{selectors.BASE_URL}{selectors.DASHBOARD_PATH}?refresh=1"


def _require_display() -> None:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit(
            "No graphical session detected. Run this from a terminal inside your Linux desktop session."
        )


def _elms_cookies(cookies: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        cookie
        for cookie in cookies
        if (cookie.get("domain") or "").lstrip(".") == "elms.sti.edu"
    ]


def _write_cookie_jar(cookies: list[dict[str, object]]) -> None:
    COOKIE_OUT.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_OUT.write_text(
        json.dumps({"cookies": cookies}, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(COOKIE_OUT, 0o600)


def _update_gh_secret(repo: str | None) -> None:
    command = ["gh", "secret", "set", "ELMS_COOKIES"]
    if repo:
        command += ["--repo", repo]
    try:
        subprocess.run(
            command,
            input=COOKIE_OUT.read_text(encoding="utf-8"),
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit("gh CLI not found. Install GitHub CLI and run gh auth login.") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"Failed to update ELMS_COOKIES (gh exited {exc.returncode}). Run gh auth login and retry."
        ) from exc


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
            cookies = _elms_cookies(await context.cookies())
            if not cookies:
                print("No elms.sti.edu cookies were found; sign-in may not have completed.")
                return 1

            _write_cookie_jar(cookies)
            print(f"Wrote {len(cookies)} ELMS cookie(s) to {COOKIE_OUT}")
            if update_secret:
                _update_gh_secret(repo)
                print("Updated GitHub secret ELMS_COOKIES.")
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
    args = parser.parse_args()
    try:
        return asyncio.run(
            refresh(args.url, args.timeout, args.repo, update_secret=not args.no_secret)
        )
    except KeyboardInterrupt:
        print("\nAborted. The saved session profile was left in place.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())