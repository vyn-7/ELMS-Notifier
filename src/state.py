"""Seen-item state, baseline, and failure tracking."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .config import TIMEZONE

STATE_VERSION = 1
EMPTY_ALERT_HOURS = 6
MAX_CONSECUTIVE_FAILURES = 3


class StateError(RuntimeError):
    """Raised when the state file exists but cannot be trusted."""


def new_state() -> dict[str, Any]:
    """Return a fresh state document with no baseline."""

    return {
        "version": STATE_VERSION,
        "baseline_done": False,
        "seen": {},
        "failures": {},
        "last_empty_alert": None,
    }


def load_state(path: Path) -> dict[str, Any]:
    """Load state, or return a fresh one when the file is absent.

    Raises StateError when the file exists but is not a valid state document.
    """

    if not path.exists():
        return new_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError("state file is unreadable") from exc
    if not isinstance(data, dict):
        raise StateError("state file is not a JSON object")
    if data.get("version") != STATE_VERSION:
        raise StateError("unsupported state version")
    if not isinstance(data.get("seen"), dict):
        raise StateError("state file has no seen map")
    data.setdefault("baseline_done", False)
    data.setdefault("failures", {})
    if not isinstance(data["failures"], dict):
        raise StateError("state file has an invalid failures map")
    data.setdefault("last_empty_alert", None)
    return data


def save_state(path: Path, state_doc: dict[str, Any]) -> None:
    """Atomically write state with owner-only permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state_doc, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def diff_new_items(state_doc: dict[str, Any], items: Iterable[Any]) -> list[Any]:
    """Return items whose IDs are not in the seen map."""

    seen = state_doc.get("seen", {})
    return [item for item in items if item.id not in seen]


def baseline(state_doc: dict[str, Any], items: Iterable[Any], now: datetime | None = None) -> None:
    """Mark every current item as seen without sending notifications."""

    now = now or datetime.now(TIMEZONE)
    for item in items:
        mark_seen(state_doc, item, now)
    state_doc["baseline_done"] = True


def mark_seen(state_doc: dict[str, Any], item: Any, now: datetime | None = None) -> None:
    """Record a delivered item; call only after Discord accepted the message."""

    now = now or datetime.now(TIMEZONE)
    deadline = getattr(item, "deadline", None)
    state_doc.setdefault("seen", {})[item.id] = {
        "first_seen": now.isoformat(),
        "title": getattr(item, "title", ""),
        "deadline": deadline.isoformat() if deadline else None,
    }
    state_doc.setdefault("failures", {}).pop(item.id, None)


def mark_failure(
    state_doc: dict[str, Any],
    item: Any,
    error: str,
    now: datetime | None = None,
) -> int:
    """Increment an item's consecutive failure count and return the new count."""

    now = now or datetime.now(TIMEZONE)
    entry = state_doc.setdefault("failures", {}).setdefault(
        item.id,
        {
            "first_failed": now.isoformat(),
            "title": getattr(item, "title", ""),
        },
    )
    entry["failures"] = int(entry.get("failures", 0)) + 1
    entry["last_error"] = error[:200]
    if entry["failures"] >= MAX_CONSECUTIVE_FAILURES:
        entry["status"] = "failed"
    return entry["failures"]


def is_failed(state_doc: dict[str, Any], item_id: str) -> bool:
    """Return True when an item has exhausted its retries."""

    entry = state_doc.get("failures", {}).get(item_id, {})
    return entry.get("status") == "failed"


def empty_alert_due(state_doc: dict[str, Any], now: datetime | None = None) -> bool:
    """Rate-limit empty-list alerts to once per six hours."""

    now = now or datetime.now(TIMEZONE)
    raw = state_doc.get("last_empty_alert")
    if not raw:
        return True
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=TIMEZONE)
    return now - last >= timedelta(hours=EMPTY_ALERT_HOURS)


def set_empty_alert(state_doc: dict[str, Any], now: datetime | None = None) -> None:
    """Record the time of the last empty-list alert."""

    now = now or datetime.now(TIMEZONE)
    state_doc["last_empty_alert"] = now.isoformat()
