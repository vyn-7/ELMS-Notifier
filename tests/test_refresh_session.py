from __future__ import annotations

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