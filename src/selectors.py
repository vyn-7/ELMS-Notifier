"""All ELMS selectors and endpoint paths in one place."""

from __future__ import annotations

BASE_URL = "https://elms.sti.edu"
DASHBOARD_PATH = "/user_dashboard"
TODO_WIDGET_PATH = "/user_to_do_widget/tasks"

TODO_COURSE_LINK = "a.title_and_count"
TODO_COURSE_COUNT = "span.small_number_block"

DUE_TABLE = "table.assignmentsTable"
DUE_ASSIGNMENT_CELL = "td.assignment"
DUE_ASSIGNMENT_LINK = "td.assignment > a[href]"
DUE_LESSON_LINK = "td.assignment span a"

DETAIL_COURSE = "div.sectionTitle h1"
DETAIL_TITLE = "#fixedSectionHeader h2"
DETAIL_LESSON = "#fixedSectionHeader h1.centerHeading"
DETAIL_INSTRUCTIONS = "div.leftColumn div.materialStyle"
DETAIL_META_ITEM = "aside.rightColumn div.grading_fixed_col div.block ul li"
DETAIL_FILE_LINK = "div.materialStyle a[href]"

LOGGED_IN_BODY_CLASS = "ctr_user_dashboard"
SESSION_EXPIRED_URL_FRAGMENTS = ("/log_in", "not_logged_in")
