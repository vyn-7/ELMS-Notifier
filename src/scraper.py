"""Parse ELMS dashboard, due-list, and assignment-detail HTML."""

from __future__ import annotations

import mimetypes
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from dateutil import parser as date_parser

from . import selectors
from .config import TIMEZONE
from .models import Assignment, Attachment, TodoCourse, TodoItem

_WHITESPACE = re.compile(r"[ \t]+")
_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "ul",
    "ol",
    "table",
    "thead",
    "tbody",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}


class ScraperError(RuntimeError):
    """Raised when the expected page structure is missing."""


def _clean(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def parse_dashboard(html: str) -> list[TodoCourse]:
    """Return course rows from the dashboard or its To-do widget fragment."""

    soup = BeautifulSoup(html, "lxml")
    courses: list[TodoCourse] = []
    for link in soup.select(selectors.TODO_COURSE_LINK):
        path = urlsplit(link.get("href", "")).path
        match = re.fullmatch(r"/student_assignments/due/(\d+)", path)
        if not match:
            continue
        spans = link.find_all("span", recursive=False)
        title = _clean(spans[0].get_text(" ", strip=True)) if spans else _clean(link.get_text(" ", strip=True))
        count_el = link.select_one(selectors.TODO_COURSE_COUNT)
        count_text = re.sub(r"\D", "", count_el.get_text(" ", strip=True)) if count_el else ""
        courses.append(
            TodoCourse(
                course_id=match.group(1),
                course=title,
                count=int(count_text) if count_text else 0,
                url=urljoin(selectors.BASE_URL, path),
            )
        )
    return courses


def parse_due_page(html: str, course_id: str, fallback_course: str = "") -> list[TodoItem]:
    """Return assignment rows from one course's Assignments due page."""

    soup = BeautifulSoup(html, "lxml")
    course = _course_from_detail_page(soup) or fallback_course or f"Course {course_id}"
    table = soup.select_one(selectors.DUE_TABLE)
    if table is None:
        return []

    header_row = table.find("tr")
    colmap: dict[int, str] = {}
    if header_row:
        index = 0
        for th in header_row.find_all("th"):
            label = _clean(th.get_text(" ", strip=True)).lower()
            span = int(th.get("colspan", 1) or 1)
            for offset in range(span):
                colmap[index + offset] = label
            index += span

    items: list[TodoItem] = []
    for row in table.find_all("tr"):
        assignment_cell = row.select_one(selectors.DUE_ASSIGNMENT_CELL)
        if assignment_cell is None:
            continue
        link = assignment_cell.select_one("a[href]")
        if link is None:
            continue
        href = link.get("href", "")
        parts = [part for part in urlsplit(href).path.split("/") if part]
        if not parts:
            continue
        kind = _kind_from_path(parts[0])
        item_id = parts[-1]
        cells = row.find_all("td", recursive=False)

        def cell(label: str) -> str:
            for index, name in colmap.items():
                if name == label and index < len(cells):
                    return _clean(cells[index].get_text(" ", strip=True))
            return ""

        lesson_el = assignment_cell.select_one(selectors.DUE_LESSON_LINK)
        items.append(
            TodoItem(
                id=f"{kind}:{item_id}",
                course=course,
                title=_clean(link.get_text(" ", strip=True)),
                url=urljoin(selectors.BASE_URL, href),
                due_hint=cell("due") or None,
                kind=kind,
                item_id=item_id,
                lesson=_clean(lesson_el.get_text(" ", strip=True)) if lesson_el else "",
                score=cell("score"),
            )
        )
    return items


def parse_detail(html: str, item: TodoItem, now: datetime | None = None) -> Assignment:
    """Return assignment details for a to-do row."""

    soup = BeautifulSoup(html, "lxml")
    course_el = soup.select_one(selectors.DETAIL_COURSE)
    title_el = soup.select_one(selectors.DETAIL_TITLE)
    lesson_el = soup.select_one(selectors.DETAIL_LESSON)
    material = soup.select_one(selectors.DETAIL_INSTRUCTIONS)

    metadata: dict[str, str] = {}
    for li in soup.select(selectors.DETAIL_META_ITEM):
        text = _clean(li.get_text(" ", strip=True))
        if ":" in text:
            key, value = text.split(":", 1)
            metadata[key.strip().lower()] = value.strip()

    due_raw = metadata.get("due") or item.due_hint
    deadline = parse_deadline(due_raw, now=now)
    kind = item.kind
    if kind in ("", "assignment"):
        kind = _kind_from_type(metadata.get("type", "")) or kind
    return Assignment(
        id=item.id,
        course=_clean(course_el.get_text(" ", strip=True)) if course_el else item.course,
        title=_clean(title_el.get_text(" ", strip=True)) if title_el else item.title,
        url=item.url,
        deadline=deadline,
        deadline_raw=due_raw,
        instructions_text=html_to_text(material),
        attachments=parse_attachments(material),
        kind=kind,
        lesson=_clean(lesson_el.get_text(" ", strip=True)) if lesson_el else item.lesson,
    )


def parse_attachments(material: Tag | None) -> list[Attachment]:
    """Return file links from an instructions block, deduplicated by path/name."""

    if material is None:
        return []
    found: list[Attachment] = []
    seen: set[tuple[str, str]] = set()
    for anchor in material.select("a[href]"):
        href = anchor.get("href", "")
        path = urlsplit(href).path
        if "/files/" not in path:
            continue
        name = _clean(anchor.get_text(" ", strip=True)) or unquote(Path(path).name)
        key = (path, name)
        if key in seen:
            continue
        seen.add(key)
        mime = mimetypes.guess_type(name)[0] or mimetypes.guess_type(path)[0]
        found.append(Attachment(name=name, url=urljoin(selectors.BASE_URL, href), mime=mime))
    return found


def parse_deadline(raw: str | None, now: datetime | None = None) -> datetime | None:
    """Parse ELMS date text such as 'Nov 13' or 'Nov 12 9:35 am'.

    A missing time is treated as the end of the day. A date that is far in the
    past with no year is assumed to be next year's occurrence.
    """

    if not raw:
        return None
    text = re.sub(r"^due:\s*", "", raw.strip(), flags=re.IGNORECASE)
    if not text or text.lower() in {"?", "n/a", "none"}:
        return None
    now = now or datetime.now(TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TIMEZONE)
    default = now.replace(hour=23, minute=59, second=0, microsecond=0)
    try:
        parsed = date_parser.parse(text, default=default)
    except (ValueError, OverflowError, date_parser.ParserError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TIMEZONE)
    else:
        parsed = parsed.astimezone(TIMEZONE)
    if not re.search(r"\b(?:19|20)\d{2}\b", text) and parsed < now - timedelta(days=180):
        try:
            parsed = parsed.replace(year=parsed.year + 1)
        except ValueError:
            return None
    return parsed


def html_to_text(node: Tag | None) -> str:
    """Convert an HTML fragment to readable text while keeping list bullets."""

    if node is None:
        return ""
    chunks: list[str] = []

    def walk(current: Tag | NavigableString) -> None:
        if isinstance(current, NavigableString):
            chunks.append(str(current))
            return
        if not isinstance(current, Tag):
            return
        if current.name == "br":
            chunks.append("\n")
            return
        if current.name == "li":
            chunks.append("\n- ")
            for child in current.children:
                walk(child)
            chunks.append("\n")
            return
        if current.name in _BLOCK_TAGS:
            chunks.append("\n")
            for child in current.children:
                walk(child)
            chunks.append("\n")
            return
        for child in current.children:
            walk(child)

    walk(node)
    text = "".join(chunks).replace("\xa0", " ")
    lines = [_clean(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _course_from_detail_page(soup: BeautifulSoup) -> str:
    for heading in soup.find_all("h1"):
        text = _clean(heading.get_text(" ", strip=True))
        if text and "Internet Explorer" not in text:
            return text
    return ""


def _kind_from_path(prefix: str) -> str:
    if "dropbox" in prefix:
        return "dropbox"
    if "offline" in prefix:
        return "offline"
    if "survey" in prefix:
        return "survey"
    if "quiz" in prefix:
        return "quiz"
    cleaned = prefix.removeprefix("student_").removesuffix("_assignment")
    return cleaned or "assignment"


def _kind_from_type(value: str) -> str:
    lowered = value.lower()
    if "dropbox" in lowered:
        return "dropbox"
    if "offline" in lowered:
        return "offline"
    if "survey" in lowered:
        return "survey"
    if "quiz" in lowered:
        return "quiz"
    return "assignment"
