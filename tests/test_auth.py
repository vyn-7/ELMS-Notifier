from __future__ import annotations

import httpx
import pytest

from src.auth import FetchError, HttpSession, SessionExpired, check_session, fetch_html

_COOKIES = '{"cookies":[{"name":"lms_session_v1","value":"test","domain":"elms.sti.edu"}]}'


class FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status


class FakePage:
    def __init__(
        self,
        url: str = "https://elms.sti.edu/user_dashboard",
        body_class: str = "user ctr_user_dashboard",
        final_url: str | None = None,
    ) -> None:
        self.url = url
        self.body_class = body_class
        self.final_url = final_url

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> FakeResponse:
        self.url = self.final_url or url
        return FakeResponse(200)

    def wait_for_timeout(self, milliseconds: int) -> None:
        return None

    def eval_on_selector(self, selector: str, expression: str) -> str:
        return self.body_class

    def content(self) -> str:
        return "<html></html>"


def test_check_session_accepts_dashboard_marker() -> None:
    check_session(FakePage())


def test_check_session_rejects_login_page() -> None:
    with pytest.raises(SessionExpired):
        check_session(FakePage(final_url="https://elms.sti.edu/log_in/form"))


def test_check_session_rejects_missing_marker() -> None:
    with pytest.raises(SessionExpired):
        check_session(FakePage(body_class="user"))


def test_fetch_html_rejects_http_errors() -> None:
    page = FakePage()
    page.goto = lambda url, wait_until="domcontentloaded": FakeResponse(500)
    with pytest.raises(FetchError):
        fetch_html(page, "https://elms.sti.edu/student_assignments/due/1001")


def test_http_session_accepts_dashboard_marker() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body class='user ctr_user_dashboard'></body></html>")

    client = HttpSession(_COOKIES, transport=httpx.MockTransport(handler))
    try:
        client.check_session()
    finally:
        client.close()


def test_http_session_rejects_missing_marker() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body class='user'></body></html>")

    client = HttpSession(_COOKIES, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SessionExpired):
            client.check_session()
    finally:
        client.close()


def test_http_session_rejects_login_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/log_in/form":
            return httpx.Response(200, text="<html><body>login</body></html>")
        return httpx.Response(
            302,
            headers={"Location": "https://elms.sti.edu/log_in/form"},
        )

    client = HttpSession(_COOKIES, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SessionExpired):
            client.fetch_html("https://elms.sti.edu/user_dashboard")
    finally:
        client.close()


def test_http_session_rejects_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = HttpSession(_COOKIES, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FetchError):
            client.fetch_html("https://elms.sti.edu/student_assignments/due/1001")
    finally:
        client.close()


def test_http_session_downloads_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-1.4 test")

    client = HttpSession(_COOKIES, transport=httpx.MockTransport(handler))
    try:
        assert client.download("https://elms.sti.edu/file.pdf", "https://elms.sti.edu/") == b"%PDF-1.4 test"
    finally:
        client.close()


def test_http_session_requires_cookies() -> None:
    with pytest.raises(SessionExpired):
        HttpSession("")
