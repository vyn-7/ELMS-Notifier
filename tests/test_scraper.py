from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.config import TIMEZONE
from src.models import TodoItem
from src.scraper import (
    html_to_text,
    parse_dashboard,
    parse_deadline,
    parse_detail,
    parse_due_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_dashboard_to_do_courses() -> None:
    courses = parse_dashboard(fixture("dashboard.html"))
    assert [course.course_id for course in courses] == ["1001", "1002"]
    assert courses[0].course == "Test Course 101"
    assert courses[0].count == 2
    assert courses[0].url == "https://elms.sti.edu/student_assignments/due/1001"


def test_parse_dashboard_accepts_widget_fragment() -> None:
    html = """
    <li><a class="title_and_count" href="/student_assignments/due/1003">
      <span>Test Course 103</span><span class="small_number_block">4 items</span>
    </a></li>
    """
    courses = parse_dashboard(html)
    assert [(course.course_id, course.course, course.count) for course in courses] == [
        ("1003", "Test Course 103", 4)
    ]


def test_parse_due_page_mixed_item_kinds() -> None:
    items = parse_due_page(fixture("due-mixed.html"), "1001", "Test Course 101")
    assert [item.id for item in items] == [
        "dropbox:2001",
        "offline:2002",
        "survey:2003",
    ]
    assert items[0].title == "Sample Essay"
    assert items[0].lesson == "Lesson One"
    assert items[0].due_hint == "Jan 5"
    assert items[0].score == "0/100"
    assert items[1].due_hint is None
    assert items[2].kind == "survey"


def test_parse_due_page_start_and_due_columns() -> None:
    items = parse_due_page(fixture("due-start.html"), "1002", "Test Course 102")
    assert len(items) == 1
    assert items[0].due_hint == "Nov 22 11:58 pm"
    assert items[0].score == "?/100"


def test_parse_detail_with_deadline_and_pdf() -> None:
    item = TodoItem(
        id="dropbox:2001",
        course="Test Course 101",
        title="Sample Essay",
        url="https://elms.sti.edu/student_dropbox_assignment/show/2001",
    )
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TIMEZONE)
    assignment = parse_detail(fixture("assignment.html"), item, now)
    assert assignment.course == "Test Course 101"
    assert assignment.title == "Sample Essay"
    assert assignment.lesson == "Lesson One"
    assert assignment.kind == "dropbox"
    assert assignment.deadline is not None
    assert assignment.deadline.year == 2027
    assert assignment.deadline_raw == "Jan 5"
    assert "Write a short essay." in assignment.instructions_text
    assert "- Use citations." in assignment.instructions_text
    assert len(assignment.attachments) == 1
    assert assignment.attachments[0].name == "sample.pdf"
    assert assignment.attachments[0].mime == "application/pdf"
    assert "lmsauth=FAKE_TOKEN" in assignment.attachments[0].url


def test_parse_detail_without_deadline_or_attachments() -> None:
    item = TodoItem(
        id="offline:2002",
        course="Test Course 102",
        title="Offline Task",
        url="https://elms.sti.edu/student_offline_assignment/show/2002",
        kind="offline",
    )
    assignment = parse_detail(fixture("assignment-no-deadline.html"), item)
    assert assignment.deadline is None
    assert assignment.deadline_raw is None
    assert assignment.instructions_text == ""
    assert assignment.attachments == []
    assert assignment.kind == "offline"


def test_parse_deadline_formats_and_fallbacks() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=TIMEZONE)
    assert parse_deadline("Nov 13", now).isoformat() == "2026-11-13T23:59:00+08:00"
    assert parse_deadline("Nov 12 9:35 am", now).isoformat() == "2026-11-12T09:35:00+08:00"
    assert parse_deadline("Jan 5", now).isoformat() == "2027-01-05T23:59:00+08:00"
    assert parse_deadline("2026-12-01 23:59", now).isoformat() == "2026-12-01T23:59:00+08:00"
    assert parse_deadline("?", now) is None
    assert parse_deadline(None, now) is None
    assert parse_deadline("not a date", now) is None


def test_html_to_text_keeps_list_structure() -> None:
    from bs4 import BeautifulSoup

    node = BeautifulSoup(
        "<div><p>First paragraph.</p><ul><li>One</li><li>Two</li></ul></div>",
        "lxml",
    ).div
    text = html_to_text(node)
    assert "First paragraph." in text
    assert "- One" in text
    assert "- Two" in text
