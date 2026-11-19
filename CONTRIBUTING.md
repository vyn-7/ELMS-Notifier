# Contributing

Thanks for helping improve ELMS Notifier. This project is intentionally small and read-only; please keep changes focused.

## Before you start

- Read [README.md](README.md) and [MAINTAIN.md](MAINTAIN.md).
- Open an issue before a large change, especially one that adds a dependency or changes the LMS interaction.
- Do **not** propose features that write to ELMS (submission, editing, deletion, enrollment, auto-grading) or that automate Microsoft sign-in, MFA, or captcha bypasses.

## Development setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
pytest -q
```

For parser work you do not need a live ELMS session: use or extend the sanitized fixtures in `tests/fixtures/`.

## Code guidelines

- Keep all ELMS URLs and selectors in `src/selectors.py`. Layout fixes belong there first.
- Keep `src/main.py` orchestration-only; parsing belongs in `src/scraper.py`.
- Add type hints to public functions and a short docstring describing what is returned and what can be raised.
- Handle missing deadlines and missing attachments gracefully.
- Do not log page HTML, instructions, cookies, bot tokens, or full attachment URLs.
- Keep dependencies minimal. Discuss a new dependency before adding it.
- Use `pytest`; add a regression test for every parser or state change.

## Fixture rules

Fixtures must be sanitized before they enter the repository:

- Replace student names, emails, IDs, course codes, and assignment titles with fake values.
- Remove `lmsauth` query strings and any token-like parameters.
- Remove scripts, inline analytics, and unrelated page chrome.
- Never commit a raw capture from `recon/` or a browser storage-state file.

If you are unsure, keep the fixture minimal rather than copying a real page.

## Pull request checklist

- [ ] `pytest -q` passes.
- [ ] `python -m compileall -q src scripts` passes.
- [ ] `python src/main.py --dry-run` still works (if you have a session).
- [ ] No `.env`, cookies, storage state, or real student data is included.
- [ ] No new `pull_request_target` workflow trigger.
- [ ] README or MAINTAIN updated if behavior, configuration, or the schedule changed.

## Reporting bugs

Use the issue tracker for parser or delivery bugs. Include the error type and a sanitized snippet of the relevant HTML structure, never a full page or a credential. Security issues should follow [SECURITY.md](SECURITY.md), not the public issue tracker.
