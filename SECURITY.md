# Security policy

## Supported versions

The `main` branch is the supported version. Fixes are applied there; older copies are not maintained.

## Reporting a vulnerability

Please **do not open a public issue** for a vulnerability or a leaked credential.

Use GitHub's private vulnerability reporting on the repository's **Security** tab if it is available. Include:

- what is affected (Discord token handling, cookie storage, parsing, workflow, dependencies);
- steps to reproduce, with secrets and student data removed;
- the impact you believe it has;
- a suggested fix, if you have one.

Do not include real cookies, bot tokens, student names, assignment content, or full ELMS HTML in a report.

## Secrets this project handles

| Secret | Where it lives | Rotation |
|---|---|---|
| `DISCORD_BOT_TOKEN` | Local `.env` or GitHub repository secret | Reset in the Discord Developer Portal; update `.env` and the secret |
| `ELMS_COOKIES` / `ELMS_COOKIES_JSON` | GitHub repository secret; local `.session/cookies.json` | Sign out the ELMS session, sign in again, re-export, update the secret |
| `ELMS_SSO_COOKIES` | GitHub repository secret only | Sign in to Microsoft again, re-export the Microsoft cookies, update the secret |
| `.session/storage-state.json` | Local gitignored file | Re-run `scripts/refresh_session.py` |

The bot token can post as the bot. The cookie jar can read the student's ELMS account. `ELMS_SSO_COOKIES` can re-enter that Microsoft account's SSO session. Treat all three as passwords.

## Security boundaries

- The notifier is **read-only** on ELMS. It must never submit, edit, delete, enroll, or click anything that changes data.
- It must never enter Microsoft credentials, handle MFA, solve captchas, or bypass access controls. Optional headless renewal reuses an existing Microsoft SSO cookie; it stops if Microsoft asks for a password or MFA.
- `.env`, `.session/`, `state.json`, `downloads/`, and `recon/` are gitignored. Do not remove those rules.
- The repository may be public. Do not commit raw HTML, screenshots, assignment content, or fixtures with real names, IDs, emails, or course codes.
- Workflows use `permissions: contents: read`. Do not add `pull_request_target`.
- If a secret is committed, assume it is compromised. Rotate it first, then remove it from history.

## Dependency security

Dependabot watches pip and GitHub Actions weekly. Review updates and run the test suite before merging. Do not add a dependency that requires a paid account or a payment card.
