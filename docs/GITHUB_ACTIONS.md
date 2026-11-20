# Zero-card scheduled checks with GitHub Actions

This is the no-card, no-trial option. GitHub Actions runs the notifier **three times a day** in GitHub's cloud. It is not a continuously running server; the Discord bot is offline between runs, which is fine because it only sends messages and has no commands.

It works because the bot does not need a browser in CI. `ELMS_COOKIES_JSON` gives it the three `elms.sti.edu` cookies over plain HTTPS, and PyMuPDF still converts PDFs to PNGs on the runner. The browser is only used on your laptop to create the session.

## Schedule

The workflow cron is `31 2,6,14 * * *` (UTC), which is **10:31, 13:31, and 22:31 Asia/Manila**. The 31-minute offset avoids GitHub's busy top-of-hour scheduling window. Edit `.github/workflows/elms-notifier.yml` if you want different hours.

## Tradeoffs you must accept

- GitHub may delay scheduled runs by several minutes, and occasionally longer under load. Notifications are not real-time.
- In a public repository, GitHub disables scheduled workflows after **60 days without repository activity**. Make a trivial commit or run the workflow manually once a month.
- A private repository also works; three runs a day is far below the free Actions quota. The repository must be public only if you want unlimited minutes for more frequent runs.
- GitHub's terms expect Actions to be used for the repository's software project. This bot is the repository, so it is a reasonable use, but it is not a general-purpose hosting service.
- The ELMS session can still expire. You refresh it on your laptop and update one secret.
- **Unverified until the first run:** GitHub runners use different IP addresses. Microsoft may invalidate the session from a new IP. The first workflow run is the real test. If it reports `Session expired`, refresh the cookies and retry; if Microsoft consistently rejects runner IPs, host the notifier on a small always-on machine instead.

## 1. Create the repository

Create a GitHub repository (public is simplest), then push this project. Before pushing:

```bash
git status --short
```

`.env`, `.session/`, `state.json`, `recon/`, `downloads/`, and `.venv/` must not appear. They are gitignored.

## 2. Create the bot and set the secrets

Follow the bot setup in the README: create a Discord application, reset the bot token, invite it with **View Channels**, **Send Messages**, **Embed Links**, **Attach Files**, and **Mention Everyone**, then copy the target channel ID.

Install and authenticate the GitHub CLI if needed (`gh auth login`). Then:

```bash
cd ~/Documents/Projects/STI/assignment-notifier

# Paste the bot token only when gh prompts; never commit it or paste it in chat.
gh secret set DISCORD_BOT_TOKEN

# The channel ID is not a secret, but keeping it here is convenient.
gh secret set DISCORD_CHANNEL_ID

# Export only the three elms.sti.edu cookies. This writes .session/cookies.json.
python scripts/export_cookies.py
gh secret set ELMS_COOKIES < .session/cookies.json
```

Keep `.session/cookies.json` private; it is a credential. Do not print it.

## 3. First run

```bash
gh workflow run elms-notifier.yml
gh run watch
```

The first run has no cached state, so it creates a **silent baseline**: it records the current items and posts nothing. That is expected.

Check the log for:

```text
Baseline created with N items; no notifications sent.
```

If you see `ELMS_COOKIES_JSON` or `Session expired`, the secret is missing or the cookies are dead.

## 4. Normal runs

The workflow runs three times a day. New items produce one bot message each, starting with `@everyone`, with PDF pages as PNGs and the original PDF. The state is saved in the GitHub Actions cache so the next run knows what was already delivered.

The workflow uses `concurrency` to prevent overlapping runs. State is saved even when the notifier exits non-zero, so a failed item can be retried.

## 5. Refresh an expired session

When the bot posts a session-expired alert:

```bash
cd ~/Documents/Projects/STI/assignment-notifier
source .venv/bin/activate
python scripts/refresh_session.py
```

The helper opens a dedicated browser profile. If the session is still valid, it exports immediately; otherwise sign in manually. It prints the signed-in account name, asks for confirmation, then writes the cookie jar and updates `ELMS_COOKIES`. Use `--no-secret` to skip the GitHub update.

The next scheduled run uses the new cookies. You do not need to change any code.

## 6. If you stop using it

Disable the workflow in the repository's **Actions** tab. Delete the `ELMS_COOKIES`, `DISCORD_BOT_TOKEN`, and `DISCORD_CHANNEL_ID` secrets. Remove the bot from the server if you no longer want it.

## Troubleshooting

| Log / symptom | Cause | Fix |
|---|---|---|
| `DISCORD_BOT_TOKEN is not set` | Secret missing | `gh secret set DISCORD_BOT_TOKEN` |
| `DISCORD_CHANNEL_ID is not set` | Secret missing | `gh secret set DISCORD_CHANNEL_ID` |
| `Discord returned HTTP 401` | Bad/revoked bot token | Reset the token and update the secret |
| `Discord returned HTTP 403` | Bot lacks Send Messages / Embed Links / Attach Files | Fix the bot's channel permissions |
| `Discord returned HTTP 404` | Wrong channel ID or bot not in the server | Copy the channel ID again; re-invite the bot |
| Message posts but `@everyone` doesn't ping | Bot lacks Mention Everyone | Grant that permission in the channel |
| `No ELMS cookies; set ELMS_COOKIES_JSON` | Secret missing | Export and set `ELMS_COOKIES` |
| `Session expired` | Cookies dead or IP rejected | Re-login, export, update secret |
| `Found 0 new item(s)` | Normal | No action |
| No `schedule` run at the expected time | GitHub did not register the schedule (often after a disable/re-enable) | Push a commit that changes the cron line, then check `gh run list --event schedule` |
| `Baseline created` on a later run | Actions cache was evicted | Check ELMS manually for items added during the gap |
| Workflow not running | Schedule disabled after 60 days of inactivity | Make a commit or run it manually |

## Why this is free

Standard GitHub-hosted runners in **public repositories** are free and do not consume the account's included compute usage. Three runs a day is also comfortably inside the free quota for a private repository. No card is required. The workflow uses only GitHub-hosted runners, Actions cache, and repository secrets.
