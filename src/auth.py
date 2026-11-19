"""Reuse the manually created ELMS session and detect expiry."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator, Protocol

import httpx
from bs4 import BeautifulSoup

from . import config, selectors
from .config import DASHBOARD_URL, REQUEST_TIMEOUT_MS, STORAGE_STATE_PATH

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class SessionExpired(RuntimeError):
    """Raised when the saved ELMS session no longer works."""


class FetchError(RuntimeError):
    """Raised when a page cannot be fetched or returns an error."""


class Session(Protocol):
    """The small surface main.py needs from either session backend."""

    def check_session(self) -> None: ...

    def fetch_html(self, url: str) -> str: ...

    def download(self, url: str, referer: str) -> bytes: ...


class BrowserSession:
    """Playwright-backed session for local runs."""

    def __init__(self, context: object, page: object) -> None:
        self._context = context
        self._page = page

    def check_session(self) -> None:
        check_session(self._page)

    def fetch_html(self, url: str) -> str:
        return fetch_html(self._page, url)

    def download(self, url: str, referer: str) -> bytes:
        response = self._context.request.get(
            url,
            headers={"Referer": referer},
            timeout=REQUEST_TIMEOUT_MS,
        )
        if response.status != 200:
            raise FetchError(f"Attachment returned HTTP {response.status}")
        return response.body()


class HttpSession:
    """Cookie-backed HTTP session for CI or other display-less hosts."""

    def __init__(
        self,
        cookies_json: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        raw = cookies_json if cookies_json is not None else config.ELMS_COOKIES_JSON
        if not raw:
            raise SessionExpired("No ELMS cookies; set ELMS_COOKIES_JSON")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SessionExpired("ELMS_COOKIES_JSON is not valid JSON") from exc
        entries = data.get("cookies", []) if isinstance(data, dict) else data
        cookies = {
            entry["name"]: entry["value"]
            for entry in entries
            if isinstance(entry, dict) and entry.get("name") and entry.get("value") is not None
        }
        if not cookies:
            raise SessionExpired("ELMS_COOKIES_JSON has no cookies")
        self._client = httpx.Client(
            cookies=cookies,
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT_MS / 1000,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str, referer: str | None = None) -> httpx.Response:
        headers = {"Referer": referer} if referer else None
        try:
            response = self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise FetchError("ELMS request failed") from exc
        if response.status_code >= 400:
            raise FetchError(f"ELMS returned HTTP {response.status_code}")
        if any(fragment in str(response.url) for fragment in selectors.SESSION_EXPIRED_URL_FRAGMENTS):
            raise SessionExpired("Login page appeared")
        return response

    def check_session(self) -> None:
        response = self._get(DASHBOARD_URL)
        body = BeautifulSoup(response.text, "lxml").body
        classes = body.get("class", []) if body else []
        if selectors.LOGGED_IN_BODY_CLASS not in classes:
            raise SessionExpired("Dashboard marker missing")

    def fetch_html(self, url: str) -> str:
        return self._get(url).text

    def download(self, url: str, referer: str) -> bytes:
        return self._get(url, referer).content


@contextmanager
def browser_context() -> Iterator[tuple[object, object]]:
    """Yield a headless (context, page) using the saved storage state."""

    from playwright.sync_api import sync_playwright

    if not STORAGE_STATE_PATH.exists():
        raise SessionExpired("No saved session; run scripts/recon.py --login-only")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = browser.new_context(
            storage_state=str(STORAGE_STATE_PATH),
            accept_downloads=True,
        )
        page = context.new_page()
        page.set_default_timeout(REQUEST_TIMEOUT_MS)
        try:
            yield context, page
        finally:
            browser.close()


@contextmanager
def session() -> Iterator[Session]:
    """Yield the HTTP session when cookies are configured, else Playwright."""

    if config.ELMS_COOKIES_JSON:
        client = HttpSession()
        try:
            yield client
        finally:
            client.close()
        return
    with browser_context() as (context, page):
        yield BrowserSession(context, page)


def check_session(page: object) -> None:
    """Verify the dashboard is logged in, or raise SessionExpired."""

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        response = page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
    except PlaywrightTimeoutError as exc:
        raise SessionExpired("Dashboard timed out") from exc
    if response is None:
        raise SessionExpired("Dashboard did not return a response")
    page.wait_for_timeout(500)
    body_class = page.eval_on_selector("body", "el => el.className") or ""
    if any(fragment in page.url for fragment in selectors.SESSION_EXPIRED_URL_FRAGMENTS):
        raise SessionExpired("Login page appeared")
    if selectors.LOGGED_IN_BODY_CLASS not in body_class:
        raise SessionExpired("Dashboard marker missing")


def fetch_html(page: object, url: str) -> str:
    """Navigate to a page and return its HTML, detecting session expiry."""

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        response = page.goto(url, wait_until="domcontentloaded")
    except PlaywrightTimeoutError as exc:
        raise FetchError("Page timed out") from exc
    if response is None:
        raise FetchError("Page did not return a response")
    if response.status >= 400:
        raise FetchError(f"Page returned HTTP {response.status}")
    page.wait_for_timeout(300)
    if any(fragment in page.url for fragment in selectors.SESSION_EXPIRED_URL_FRAGMENTS):
        raise SessionExpired("Login page appeared")
    return page.content()
