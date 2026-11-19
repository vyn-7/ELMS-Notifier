from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.config import TIMEZONE
from src.models import TodoItem
from src.state import (
    StateError,
    baseline,
    diff_new_items,
    empty_alert_due,
    is_failed,
    load_state,
    mark_failure,
    mark_seen,
    new_state,
    save_state,
    set_empty_alert,
)


def item(item_id: str = "dropbox:1", title: str = "Essay") -> TodoItem:
    return TodoItem(
        id=item_id,
        course="Test Course",
        title=title,
        url=f"https://elms.sti.edu/student_dropbox_assignment/show/{item_id.split(':')[-1]}",
    )


def test_missing_state_is_fresh(tmp_path) -> None:
    state = load_state(tmp_path / "state.json")
    assert state["baseline_done"] is False
    assert state["seen"] == {}
    assert state["failures"] == {}


def test_baseline_marks_all_items_silently(tmp_path) -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TIMEZONE)
    state = new_state()
    items = [item("dropbox:1"), item("dropbox:2")]
    baseline(state, items, now)
    assert state["baseline_done"] is True
    assert set(state["seen"]) == {"dropbox:1", "dropbox:2"}
    assert diff_new_items(state, items) == []


def test_new_item_after_baseline(tmp_path) -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TIMEZONE)
    state = new_state()
    baseline(state, [item("dropbox:1")], now)
    new_items = diff_new_items(state, [item("dropbox:1"), item("dropbox:2")])
    assert [entry.id for entry in new_items] == ["dropbox:2"]


def test_save_and_reload_preserves_seen(tmp_path) -> None:
    path = tmp_path / "state.json"
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TIMEZONE)
    state = new_state()
    mark_seen(state, item(), now)
    save_state(path, state)
    loaded = load_state(path)
    assert "dropbox:1" in loaded["seen"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_failures_retry_then_give_up_and_clear_on_success() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TIMEZONE)
    state = new_state()
    entry = item()
    assert mark_failure(state, entry, "timeout", now) == 1
    assert mark_failure(state, entry, "timeout", now) == 2
    assert not is_failed(state, entry.id)
    assert mark_failure(state, entry, "timeout", now) == 3
    assert is_failed(state, entry.id)
    assert entry.id not in state["seen"]
    mark_seen(state, entry, now)
    assert not is_failed(state, entry.id)
    assert entry.id in state["seen"]


def test_empty_alert_rate_limit() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TIMEZONE)
    state = new_state()
    assert empty_alert_due(state, now)
    set_empty_alert(state, now)
    assert not empty_alert_due(state, now)
    assert empty_alert_due(state, datetime(2026, 11, 18, 18, 1, tzinfo=TIMEZONE))


def test_corrupt_state_raises(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError):
        load_state(path)
