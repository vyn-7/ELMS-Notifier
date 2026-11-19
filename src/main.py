"""Orchestration: session, to-do diff, detail fetch, attachments, Discord."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    # Running `python src/main.py` puts src/ on sys.path, where selectors.py
    # would shadow the stdlib module used by asyncio/subprocess. Import the
    # package from the project root instead.
    script_dir = Path(__file__).resolve().parent
    sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != script_dir]
    sys.path.insert(0, str(script_dir.parent))

from src import auth, config, discord_notify, pdf_tools, scraper, selectors, state
from src.models import Assignment, TodoItem

log = logging.getLogger("elms")


def _alert(text: str) -> None:
    try:
        discord_notify.send_alert(text)
    except Exception as exc:  # alerts must not mask the original failure
        log.error("Could not send Discord alert: %s", type(exc).__name__)


def _prepare_attachments(
    client: object,
    assignment: Assignment,
    temp_root: Path,
) -> tuple[list[tuple[str, list[Path]]], list[Path]]:
    png_groups: list[tuple[str, list[Path]]] = []
    extra_files: list[Path] = []
    for index, attachment in enumerate(assignment.attachments):
        destination = temp_root / f"{index:02d}-{pdf_tools.safe_filename(attachment.name)}"
        pdf_tools.download_attachment(client, attachment.url, destination, assignment.url)
        if attachment.mime == "application/pdf" or destination.suffix.lower() == ".pdf":
            pages = pdf_tools.render_pdf_for_discord(destination, temp_root / f"pdf-{index}")
            if pages:
                png_groups.append((attachment.name, pages))
            extra_files.append(destination)
        else:
            extra_files.append(destination)
    return png_groups, extra_files


def _print_dry_run(assignment: Assignment) -> None:
    print(f"NEW {assignment.kind}: {assignment.title}")
    print(f"  course: {assignment.course}")
    print(f"  deadline: {assignment.deadline_raw or 'not specified'}")
    print(f"  attachments: {len(assignment.attachments)}")
    print(f"  link: {assignment.url}")


def run(dry_run: bool = False) -> int:
    now = datetime.now(config.TIMEZONE)
    try:
        state_doc = state.load_state(config.STATE_PATH)
    except state.StateError:
        backup = config.STATE_PATH.with_name(
            f"state.corrupt-{now.strftime('%Y%m%d-%H%M%S')}.json"
        )
        try:
            shutil.copy2(config.STATE_PATH, backup)
        except OSError:
            pass
        state_doc = state.new_state()
        if not dry_run:
            state.save_state(config.STATE_PATH, state_doc)
            _alert("State file was corrupt; created a new silent baseline.")

    if not dry_run:
        try:
            config.require_discord()
        except RuntimeError as exc:
            log.error("%s", exc)
            return 1

    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with auth.session() as client:
            client.check_session()
            courses = scraper.parse_dashboard(
                client.fetch_html(f"{selectors.BASE_URL}{selectors.TODO_WIDGET_PATH}")
            )
            if not courses:
                if state.empty_alert_due(state_doc, now):
                    if not dry_run:
                        _alert("ELMS to-do list was empty or unparseable; check the session/layout.")
                        state.set_empty_alert(state_doc, now)
                        state.save_state(config.STATE_PATH, state_doc)
                return 1

            items: list[TodoItem] = []
            course_errors = 0
            for course in courses:
                try:
                    html = client.fetch_html(course.url)
                    parsed = scraper.parse_due_page(html, course.course_id, course.course)
                    if not parsed and course.count:
                        course_errors += 1
                    items.extend(parsed)
                except auth.SessionExpired:
                    raise
                except Exception as exc:
                    course_errors += 1
                    log.error("Course %s failed: %s", course.course_id, type(exc).__name__)
                time.sleep(0.4)

            if course_errors and not dry_run:
                _alert(f"Could not parse {course_errors} course page(s); other items were processed.")

            if not items:
                if state.empty_alert_due(state_doc, now):
                    if not dry_run:
                        _alert("ELMS to-do list parsed to zero items; check the session/layout.")
                        state.set_empty_alert(state_doc, now)
                        state.save_state(config.STATE_PATH, state_doc)
                return 1

            if not state_doc.get("baseline_done"):
                if dry_run:
                    print(f"Would create a silent baseline with {len(items)} item(s).")
                    return 0
                state.baseline(state_doc, items, now)
                state.save_state(config.STATE_PATH, state_doc)
                log.info("Baseline created with %d items; no notifications sent.", len(items))
                return 0

            def sort_key(item: TodoItem) -> tuple[bool, datetime, str]:
                deadline = scraper.parse_deadline(item.due_hint, now)
                return (deadline is None, deadline or now, item.title)

            new_items = sorted(state.diff_new_items(state_doc, items), key=sort_key)
            log.info("Found %d new item(s) out of %d.", len(new_items), len(items))

            for item in new_items:
                if state.is_failed(state_doc, item.id):
                    continue
                try:
                    html = client.fetch_html(item.url)
                    assignment = scraper.parse_detail(html, item, now)
                    if dry_run:
                        _print_dry_run(assignment)
                        continue
                    with tempfile.TemporaryDirectory(dir=config.DOWNLOAD_DIR) as temp_dir:
                        png_groups, extra_files = _prepare_attachments(
                            client, assignment, Path(temp_dir)
                        )
                        discord_notify.send_assignment(assignment, png_groups, extra_files)
                    state.mark_seen(state_doc, assignment, now)
                    state.save_state(config.STATE_PATH, state_doc)
                    log.info("Delivered item %s", item.id)
                except auth.SessionExpired:
                    raise
                except Exception as exc:
                    if dry_run:
                        print(f"ERROR {item.id}: {type(exc).__name__}")
                        continue
                    failures = state.mark_failure(state_doc, item, type(exc).__name__, now)
                    state.save_state(config.STATE_PATH, state_doc)
                    if failures >= state.MAX_CONSECUTIVE_FAILURES:
                        _alert(f"Gave up on {item.title} after {failures} failed attempts.")
                    else:
                        _alert(f"Failed to notify {item.title}; will retry next run.")
            return 0

    except auth.SessionExpired:
        message = "Session expired, please re-login: python scripts/recon.py --login-only"
        if dry_run:
            print(message)
        else:
            _alert(message)
        return 2
    except Exception as exc:
        log.error("Run failed: %s", type(exc).__name__)
        if not dry_run:
            _alert(f"ELMS notifier failed: {type(exc).__name__}")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ELMS assignment notifier")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="scrape and print; do not post")
    group.add_argument("--test-discord", action="store_true", help="send one test bot message")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    # httpx logs request URLs at INFO; keep logs quiet so channel IDs stay out.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if args.test_discord:
        try:
            discord_notify.send_test()
        except Exception as exc:
            print(f"Discord test failed: {type(exc).__name__}: {exc}")
            return 1
        print("Discord test message sent.")
        return 0
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
