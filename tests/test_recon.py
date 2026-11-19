"""Redaction checks for the recon script."""

import json

from scripts.recon import (
    is_elms_host,
    redact_headers,
    redact_json,
    redact_post_data,
    redact_url,
)


def test_redact_headers_removes_cookies_and_auth() -> None:
    cleaned = redact_headers(
        {
            "Cookie": "session=abc",
            "Authorization": "Bearer xyz",
            "Content-Type": "application/json",
        }
    )
    assert cleaned["Cookie"] == "[REDACTED]"
    assert cleaned["Authorization"] == "[REDACTED]"
    assert cleaned["Content-Type"] == "application/json"


def test_redact_url_removes_token_values() -> None:
    cleaned = redact_url("https://elms.sti.edu/x?access_token=secret&page=2#frag")
    assert "secret" not in cleaned
    assert "page=2" in cleaned
    assert "#frag" not in cleaned


def test_redact_url_can_drop_all_query_values() -> None:
    cleaned = redact_url("https://login.microsoftonline.com/x?code=abc&state=def", keep_query=False)
    assert cleaned == "https://login.microsoftonline.com/x"


def test_redact_json_removes_nested_tokens() -> None:
    cleaned = redact_json(
        {"access_token": "abc", "items": [{"refreshToken": "def", "title": "ok"}]}
    )
    dumped = json.dumps(cleaned)
    assert "abc" not in dumped
    assert "def" not in dumped
    assert "ok" in dumped


def test_redact_post_data_handles_json_and_form() -> None:
    assert "abc" not in (redact_post_data('{"password":"abc","x":1}', "application/json") or "")
    assert "abc" not in (
        redact_post_data("password=abc&x=1", "application/x-www-form-urlencoded") or ""
    )


def test_is_elms_host() -> None:
    assert is_elms_host("elms.sti.edu")
    assert is_elms_host("sub.elms.sti.edu")
    assert not is_elms_host("login.microsoftonline.com")
    assert not is_elms_host(None)
