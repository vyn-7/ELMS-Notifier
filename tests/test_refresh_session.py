from __future__ import annotations

import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_refresh_session_keeps_only_elms_cookies() -> None:
    module = runpy.run_path(
        str(ROOT / "scripts" / "refresh_session.py"), run_name="refresh_session"
    )
    rows = [
        {"name": "a", "domain": ".elms.sti.edu"},
        {"name": "b", "domain": "other.test"},
        {"name": "c", "domain": "elms.sti.edu"},
    ]
    assert [row["name"] for row in module["_elms_cookies"](rows)] == ["a", "c"]


def test_refresh_session_keeps_only_microsoft_cookies() -> None:
    module = runpy.run_path(
        str(ROOT / "scripts" / "refresh_session.py"), run_name="refresh_session"
    )
    rows = [
        {"name": "auth", "domain": ".login.microsoftonline.com"},
        {"name": "elms", "domain": "elms.sti.edu"},
        {"name": "live", "domain": "login.live.com"},
        {"name": "other", "domain": "example.com"},
    ]
    assert [row["name"] for row in module["_microsoft_cookies"](rows)] == [
        "auth",
        "live",
    ]


def test_write_cookie_jar_writes_private_json(tmp_path: Path) -> None:
    module = runpy.run_path(
        str(ROOT / "scripts" / "refresh_session.py"), run_name="refresh_session"
    )
    out = tmp_path / "nested" / "cookies.json"
    rows = [{"name": "a", "value": "b"}]
    module["_write_cookie_jar"](out, rows)
    assert json.loads(out.read_text()) == {"cookies": rows}
    assert (out.stat().st_mode & 0o777) == 0o600


def test_update_gh_secret_passes_name_repo_and_file(
    tmp_path: Path, monkeypatch
) -> None:
    module = runpy.run_path(
        str(ROOT / "scripts" / "refresh_session.py"), run_name="refresh_session"
    )
    path = tmp_path / "sso.json"
    path.write_text('{"cookies":[]}')
    calls: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> None:
        calls["command"] = command
        calls["input"] = kwargs["input"]
        calls["check"] = kwargs["check"]

    monkeypatch.setattr(module["subprocess"], "run", fake_run)
    module["_update_gh_secret"]("ELMS_SSO_COOKIES", path, "vyn-7/elms-notifier")
    assert calls["command"] == [
        "gh",
        "secret",
        "set",
        "ELMS_SSO_COOKIES",
        "--repo",
        "vyn-7/elms-notifier",
    ]
    assert calls["input"] == '{"cookies":[]}'
    assert calls["check"] is True