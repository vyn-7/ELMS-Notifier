"""Data models shared by the scraper, state, and notification layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TodoCourse:
    """A course row in the dashboard's To-do widget."""

    course_id: str
    course: str
    count: int
    url: str


@dataclass
class TodoItem:
    """One assignment-like row from a course's Assignments due page."""

    id: str
    course: str
    title: str
    url: str
    due_hint: str | None = None
    kind: str = "assignment"
    item_id: str = ""
    lesson: str = ""
    score: str = ""


@dataclass(frozen=True)
class Attachment:
    """A file linked from an assignment's instructions."""

    name: str
    url: str
    mime: str | None = None


@dataclass
class Assignment:
    """Detail-page data for one to-do item."""

    id: str
    course: str
    title: str
    url: str
    deadline: datetime | None
    deadline_raw: str | None
    instructions_text: str
    attachments: list[Attachment] = field(default_factory=list)
    kind: str = "assignment"
    lesson: str = ""
