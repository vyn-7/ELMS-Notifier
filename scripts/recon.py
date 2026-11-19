"""Interactive ELMS recon and session helper.

Run from the repo root:
    .venv/bin/python scripts/recon.py              # capture HTML + network, and save the session
    .venv/bin/python scripts/recon.py --login-only # only refresh the saved browser session

The session state and browser profile in .session/ are gitignored and must never be committed or shared.
Recon output in recon/ is also gitignored and may contain personal data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import BrowserContext, Page, Request, Response, async_playwright

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / ".session" / "browser-profile"
STORAGE_STATE_PATH = ROOT / ".session" / "storage-state.json"
RECON_ROOT = ROOT / "recon"
DEFAULT_URL = "https://elms.sti.edu/"

SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "x-amz-security-token",
    "x-csrf-token",
    "x-xsrf-token",
}
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "csrf",
    "password",
    "passwd",
    "secret",
    "session",
    "signature",
    "token",
    "apikey",
    "api_key",
    "xsrf",
)
_SENSITIVE_EXACT = {"auth", "code", "key", "sig", "state", "ticket", "assertion"}
_REDACTED = "[REDACTED]"


def _is_sensitive_key(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return key.lower() in _SENSITIVE_EXACT or any(part in compact for part in _SENSITIVE_KEY_PARTS)


def redact_url(url: str, *, keep_query: bool = True) -> str:
    """Return a URL with sensitive query values removed; drops the fragment."""
    parts = urlsplit(url)
    query = ""
    if keep_query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        query = urlencode([(k, _REDACTED if _is_sensitive_key(k) else v) for k, v in pairs])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return headers with credential-bearing values replaced."""
    clean: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in SENSITIVE_HEADERS or _is_sensitive_key(lower):
            clean[key] = _REDACTED
        elif lower == "referer":
            clean[key] = redact_url(value)
        else:
            clean[key] = value
    return clean


def redact_json(value: object) -> object:
    """Recursively redact sensitive keys in parsed JSON."""
    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_sensitive_key(str(key)) else redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    return value


def redact_post_data(body: str | None, content_type: str) -> str | None:
    """Redact JSON or form bodies; never save opaque request bodies."""
    if not body:
        return None
    lowered = content_type.lower()
    if "json" in lowered:
        try:
            return json.dumps(redact_json(json.loads(body)), ensure_ascii=False)
        except (TypeError, ValueError):
            return _REDACTED
    if "form" in lowered:
        pairs = parse_qsl(body, keep_blank_values=True)
        return urlencode([(k, _REDACTED if _is_sensitive_key(k) else v) for k, v in pairs])
    return _REDACTED


def is_elms_host(host: str | None) -> bool:
    """Return True for elms.sti.edu and its subdomains."""
    host = (host or "").lower()
    return host == "elms.sti.edu" or host.endswith(".elms.sti.edu")


def _new_run_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = RECON_ROOT / f"run-{stamp}"
    run_dir.mkdir(parents=True, mode=0o700)
    return run_dir


async def save_html(page: Page, run_dir: Path, filename: str) -> None:
    """Save the current page HTML locally."""
    html = await page.content()
    (run_dir / filename).write_text(html, encoding="utf-8")
    print(f"Saved {filename} ({len(html):,} chars)")


async def save_session(context: BrowserContext) -> None:
    """Persist cookies and localStorage while the logged-in browser is open."""
    await context.storage_state(path=str(STORAGE_STATE_PATH))
    print(f"Session state saved: {STORAGE_STATE_PATH}")


def _redact_meta_url(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return redact_url(url, keep_query=is_elms_host(host))


def _require_display() -> None:
    """Fail clearly when the helper is launched outside a desktop session."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit(
            "No graphical session detected. Run this from a terminal inside your Linux desktop session."
        )


async def run_recon(url: str) -> int:
    """Open a visible browser, let the user sign in, then capture recon files."""
    _require_display()
    run_dir = _new_run_dir()
    records: list[dict[str, object]] = []
    tasks: set[asyncio.Task[None]] = set()
    page_urls: dict[str, str] = {}

    def schedule(coro: object) -> None:
        task = asyncio.create_task(coro)  # type: ignore[arg-type]
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def capture_request(request: Request) -> None:
        host = urlsplit(request.url).hostname or ""
        elms = is_elms_host(host)
        try:
            headers = await request.all_headers()
        except Exception:
            headers = {}
        records.append(
            {
                "kind": "request",
                "at": datetime.now(timezone.utc).isoformat(),
                "method": request.method,
                "host": host,
                "resource_type": request.resource_type,
                "url": redact_url(request.url, keep_query=elms),
                "headers": redact_headers(headers) if elms else {},
                "post_data": redact_post_data(
                    request.post_data, headers.get("content-type", "")
                )
                if elms
                else None,
            }
        )

    async def capture_response(response: Response) -> None:
        request = response.request
        host = urlsplit(request.url).hostname or ""
        elms = is_elms_host(host)
        content_type = response.headers.get("content-type", "")
        record: dict[str, object] = {
            "kind": "response",
            "at": datetime.now(timezone.utc).isoformat(),
            "status": response.status,
            "host": host,
            "resource_type": request.resource_type,
            "url": redact_url(request.url, keep_query=elms),
            "mime": content_type,
        }
        if elms:
            record["headers"] = redact_headers(dict(response.headers))
            if request.resource_type in {"xhr", "fetch"} and "json" in content_type.lower():
                try:
                    text = await response.text()
                    record["body"] = (
                        redact_json(json.loads(text))
                        if len(text) <= 2_000_000
                        else "[TRUNCATED]"
                    )
                except Exception as exc:
                    record["body_error"] = type(exc).__name__
        records.append(record)

    def attach(page: Page) -> None:
        page.on("request", lambda request: schedule(capture_request(request)))
        page.on("response", lambda response: schedule(capture_response(response)))

    PROFILE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"Session profile: {PROFILE_DIR}")
    print(f"Recon output:    {run_dir}")
    print("Keep both folders private. They are gitignored and may contain personal data.")

    async with async_playwright() as playwright:
        context: BrowserContext = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=["--start-maximized", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        try:
            for existing in context.pages:
                attach(existing)
            context.on("page", attach)
            page = context.pages[0] if context.pages else await context.new_page()

            print("\nOpening ELMS. Sign in manually, including any Microsoft MFA.")
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(2_000)
            page_urls["login"] = _redact_meta_url(page.url)
            await save_html(page, run_dir, "01-login.html")

            await asyncio.to_thread(
                input,
                "\nLog in, then open your ELMS to-do list in this same tab. "
                "Press Enter when the list is visible: ",
            )
            await page.wait_for_timeout(1_500)
            page_urls["todo"] = _redact_meta_url(page.url)
            await save_session(context)
            await save_html(page, run_dir, "02-todo.html")

            await asyncio.to_thread(
                input,
                "\nOpen one assignment without a PDF in the same tab. "
                "Press Enter when its detail page is visible: ",
            )
            await page.wait_for_timeout(1_500)
            page_urls["assignment"] = _redact_meta_url(page.url)
            await save_html(page, run_dir, "03-assignment.html")

            await asyncio.to_thread(
                input,
                "\nOpen one assignment that has a PDF attachment in the same tab. "
                "Press Enter when its detail page is visible: ",
            )
            await page.wait_for_timeout(1_500)
            page_urls["assignment_with_pdf"] = _redact_meta_url(page.url)
            await save_html(page, run_dir, "04-assignment-with-pdf.html")

            await page.wait_for_timeout(1_500)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await save_session(context)

            meta = {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "pages": page_urls,
                "final_url": _redact_meta_url(page.url),
                "note": "Raw HTML and network dumps may contain personal data. Do not commit.",
            }
            (run_dir / "meta.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
            (run_dir / "network.json").write_text(
                json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"\nWrote meta.json and network.json ({len(records)} records).")
            print("Raw dumps may contain personal data. Do not commit or upload them.")
            return 0
        finally:
            await context.close()


async def login_only(url: str) -> int:
    """Open a visible browser so the user can refresh the saved session."""
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
            await asyncio.to_thread(
                input,
                "\nSign in manually, including any Microsoft MFA. "
                "Press Enter once you can see the ELMS dashboard: ",
            )
            await save_session(context)
            print("Session saved. You can close the browser window.")
            return 0
        finally:
            await context.close()


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="only open a visible browser and refresh the saved session",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="ELMS start URL")
    args = parser.parse_args()
    try:
        if args.login_only:
            return asyncio.run(login_only(args.url))
        return asyncio.run(run_recon(args.url))
    except KeyboardInterrupt:
        print("\nAborted. The saved session profile was left in place.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
