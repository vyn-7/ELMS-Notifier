# Maintaining ELMS Notifier

This document is for people changing the code, not for first-time setup. Start with the [README](README.md) if you only want to run the notifier.

## What this project is

A single-user, read-only bridge from **STI eLMS** (`https://elms.sti.edu`) to a **Discord bot**. It polls the eLMS to-do widget three times a day, scrapes newly added items, converts PDF attachments to PNG previews, and posts one Discord message per new item.

It deliberately does **not**:

- submit, edit, delete, enroll, or otherwise write to the LMS;
- automate Microsoft sign-in, MFA, captcha, or any access control;
- store passwords;
- support multiple users or multiple ELMS accounts;
- add grades, dashboards, AI summaries, or auto-submission.

Keep those boundaries unless the project owner explicitly changes the scope.

## Architecture

```text
GitHub Actions (or a local cron/systemd job)
        │
        ▼
src/main.py ── auth.session() ──┬── HttpSession   (ELMS_COOKIES_JSON, CI)
                               └── BrowserSession (Playwright, local)
        │
        ├── GET /user_to_do_widget/tasks  → scraper.parse_dashboard()
        ├── GET /student_assignments/due/<course_id> → scraper.parse_due_page()
        ├── GET assignment detail → scraper.parse_detail()
        ├── pdf_tools.download_attachment() + render_pdf_for_discord()
        └── discord_notify.send_assignment() → Discord REST API
```

| File | Responsibility |
|---|---|
| `src/main.py` | Orchestration only: session, diff, per-item retries, alerts. No parsing logic. |
| `src/auth.py` | `HttpSession` for CI, `BrowserSession` for local, session-expiry detection. |
| `src/scraper.py` | BeautifulSoup parsers for the to-do fragment, due pages, detail pages, deadlines. |
| `src/selectors.py` | Every ELMS URL, path, CSS selector, and body-class marker. Change layout fixes here first. |
| `src/pdf_tools.py` | Attachment download, PDF validation, PNG rendering, batch planning. |
| `src/discord_notify.py` | Bot API delivery, embeds, text chunking, attachment messages, alerts. |
| `src/state.py` | Seen map, baseline, retry counters, empty-alert rate limit. |
| `src/models.py` | Small dataclasses shared by the parsers and sender. |

## Data flow and state

`state.json` is a JSON document with:

```json
{
  "version": 1,
  "baseline_done": true,
  "seen": {
    "dropbox:123456": {
      "first_seen": "2026-01-01T10:00:00+08:00",
      "title": "Example assignment",
      "deadline": "2026-01-05T23:59:00+08:00"
    }
  },
  "failures": {},
  "last_empty_alert": null
}
```

Rules that must not regress:

1. **First run creates a silent baseline.** No old items are posted.
2. **Mark an item seen only after Discord accepted every message.** A duplicate is better than a missed assignment.
3. **One bad item must not stop the rest.** The loop catches per-item exceptions, records a failure, and continues.
4. **Retry failures on later runs.** After `MAX_CONSECUTIVE_FAILURES` (3), the item is marked `failed` and an alert is sent.
5. **Lost state creates a new baseline.** It must not spam old assignments.
6. **An empty or unparseable to-do list raises an alert**, rate-limited to once every six hours.
7. **State writes are atomic** (`.tmp` + `os.replace`, mode `0600`).

The GitHub Actions workflow stores `state.json` with `actions/cache`. Cache eviction is expected occasionally; the next run establishes a new baseline.

## ELMS structure reference

All selectors live in `src/selectors.py`. The important endpoints are:

| Purpose | Path |
|---|---|
| Login form | `/log_in/form` |
| Dashboard (session marker) | `/user_dashboard` |
| To-do widget fragment | `/user_to_do_widget/tasks` |
| Course due page | `/student_assignments/due/<course_id>` |
| Assignment detail | `/student_dropbox_assignment/show/<id>` and sibling types |

The CI path uses **`/user_to_do_widget/tasks`**, not the full dashboard. It is a server-rendered HTML fragment containing `a.title_and_count` links. Each link has the course name in the first span and the item count in `span.small_number_block`.

The due page contains `table.assignmentsTable`. The parser reads the header cells to map the optional `Start`, `Due`, and `Score` columns instead of assuming fixed positions. Assignment IDs are the final numeric path segment; the stable key is `<kind>:<id>` (for example `dropbox:123456`).

Detail pages share one structure across dropbox, offline, and survey items:

- course: `div.sectionTitle h1`
- title: `#fixedSectionHeader h2`
- lesson: `#fixedSectionHeader h1.centerHeading`
- instructions: `div.leftColumn div.materialStyle`
- metadata: `aside.rightColumn div.grading_fixed_col div.block ul li`
- attachments: links whose path contains `/files/`

Deadlines are parsed by `scraper.parse_deadline()`:

- a missing time means **23:59 Asia/Manila** on that date;
- a date with no year that is more than 180 days in the past is treated as next year;
- unparseable deadlines return `None` and the raw text is shown instead.

## Session lifecycle

- Local runs use `.session/storage-state.json`, created by `scripts/refresh_session.py` or `scripts/recon.py --login-only`.
- CI runs use `ELMS_COOKIES_JSON`, created by `scripts/refresh_session.py` (or `scripts/export_cookies.py`) and stored as the `ELMS_COOKIES` repository secret.
- Before scraping, CI runs `scripts/refresh_session.py --headless`. It reuses the Microsoft SSO cookies in `ELMS_SSO_COOKIES`, writes fresh ELMS cookies to `.session/cookies.json`, and falls back to `ELMS_COOKIES` if renewal fails.
- When `APP_CLIENT_ID` and `APP_PRIVATE_KEY` are configured, CI mints a one-hour GitHub App token and uses it to update both `ELMS_COOKIES` and `ELMS_SSO_COOKIES`. This slides the persistent Microsoft session forward instead of letting a static jar age out. The App needs only **Secrets: Read and write** and is installed on this repository.
- There is no non-expiring Microsoft cookie. `ESTSAUTHPERSISTENT` is valid for up to 90 days of inactivity; a successful renewal every few hours renews that window. A revoked session or a new MFA prompt still requires a manual sign-in.
- `HttpSession.check_session()` fetches `/user_dashboard` and requires the `ctr_user_dashboard` body class. A login redirect or missing marker raises `SessionExpired`.
- The notifier never enters Microsoft credentials or handles MFA. If SSO requires either, headless renewal stops and the notifier sends the normal re-login alert.
- Refresh procedure: run the login helper on a graphical machine, export cookies, update the secret, then run the workflow manually to confirm.

Session cookies are credentials. Never print them, commit them, or attach them to an issue.

## Discord delivery

- Endpoint: `POST https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages`
- Auth header: `Authorization: Bot <token>`
- The bot needs no gateway connection because it only sends messages. It can stay offline between runs.
- The first message of an assignment contains `@everyone` and `allowed_mentions: {"parse": ["everyone"]}`.
- Alerts and file batches set `allowed_mentions: {"parse": []}` so they never ping.
- The bot needs **View Channels**, **Send Messages**, **Embed Links**, **Attach Files**, and **Mention Everyone**.
- HTTP 429 responses honor `retry_after` with a bounded sleep and at most four attempts.
- Attachments are capped at **10 per message** and **8 MiB per file/message** to leave room for the embed payload.

## Scheduled runs

`.github/workflows/elms-notifier.yml` runs at:

```cron
31 2,5,14 * * *
```

That is **10:31, 13:31, and 22:31 Asia/Manila**. GitHub cron is UTC. The 31-minute offset avoids the top-of-hour window where scheduled runs are most likely to be delayed or skipped. The schedule can still be delayed under load; do not describe this as real-time monitoring.

The workflow uses `concurrency` to prevent overlapping runs, `contents: read` permissions, a five-minute timeout, and restores/saves state through the Actions cache. Do not add `pull_request` or `pull_request_target` triggers to the notifier workflow.

## Testing

```bash
source .venv/bin/activate
pytest -q
python -m compileall -q src scripts
```

Tests use sanitized fixtures in `tests/fixtures/`. When adding a fixture:

- replace names, course codes, emails, IDs, and tokens with fake values;
- remove query strings that contain `lmsauth` or other tokens;
- never copy a raw capture from `recon/` into the repository;
- keep only the structure needed by the parser.

Coverage expectations:

- parsing with and without deadlines;
- attachments, including no attachments;
- PDF rendering and batch splitting;
- state baseline, diff, repeat run, corrupt state, retries;
- session expiry and HTTP errors;
- Discord payloads, including the bot API URL/auth header and `@everyone`.

## Dependency updates

Dependabot checks pip and GitHub Actions weekly. When updating dependencies:

```bash
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
python src/main.py --dry-run
```

Keep `requirements.txt` and `requirements-cloud.txt` in sync for packages used in CI. The cloud requirements intentionally omit Playwright.

## Repository metadata

When creating the public GitHub repository, use:

**Description**

> Free STI eLMS assignment notifier for Discord. Scheduled on GitHub Actions, with PDF previews and deadline alerts.

**Topics**

`sti`, `elms`, `discord-bot`, `assignment-notifier`, `github-actions`, `python`, `playwright`, `pymupdf`, `student-tools`

Upload `assets/social-preview.png` in **Settings → General → Social preview**. The README banner is `assets/banner.png`; the bot avatar can use `assets/logo.png`. Add the description and topics above in the same settings page.

## Pre-push checklist

Run from a clean checkout with the real `.env` present, but never stage it:

```bash
source .venv/bin/activate
pytest -q
python -m compileall -q src scripts
python src/main.py --dry-run
git status --short
git check-ignore .env .session state.json downloads recon
```

Confirm:

- [ ] `.env`, `.session/`, `state.json`, `downloads/`, and `recon/` are ignored;
- [ ] no secret appears in `README.md`, docs, tests, workflows, or assets;
- [ ] the workflow cron is still `31 2,5,14 * * *`;
- [ ] the Discord test still works;
- [ ] the social preview and banner are up to date;
- [ ] `README.md` links resolve.

Useful secret scan (prints file names only, never values):

```bash
git grep -nE 'discord(app)?\.com/api/webhooks|DISCORD_BOT_TOKEN=[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,}|Bot [A-Za-z0-9._-]{20,}' -- . ':!*.example' || true
```

## Incident: leaked bot token or cookies

1. Reset the bot token in the Discord Developer Portal.
2. Update the `DISCORD_BOT_TOKEN` secret and the local `.env`.
3. If cookies leaked, sign out the ELMS session, run the login helper again, export cookies, and update `ELMS_COOKIES`.
4. If a secret was committed, assume it is public. Rotate first, then remove it from history. Removing the file alone is not enough.

## Known limitations

- ELMS is a third-party site; a layout change can break the parsers.
- GitHub runner IPs may be rejected by Microsoft. The first workflow run is the real test.
- Scheduled workflows are not real-time and can be delayed.
- GitHub disables scheduled workflows in public repositories after 60 days without repository activity.
- The bot has no commands and no gateway; adding slash commands would require a persistent host.
- Only one ELMS account is supported.

## When to change the schedule

Edit the cron in `.github/workflows/elms-notifier.yml` and the table in `README.md` and `docs/GITHUB_ACTIONS.md`. Remember GitHub cron is UTC; the project displays schedules in `Asia/Manila` (UTC+8).
