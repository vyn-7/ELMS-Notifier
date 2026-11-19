"""Environment, paths, and constants.

The Discord bot token is read from the environment (or the gitignored .env
file) and is never logged. Session cookies live only in
.session/storage-state.json or an encrypted repository secret.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from . import selectors

ROOT = Path(__file__).resolve().parent.parent
TIMEZONE = ZoneInfo("Asia/Manila")

DASHBOARD_URL = f"{selectors.BASE_URL}{selectors.DASHBOARD_PATH}"

STORAGE_STATE_PATH = ROOT / ".session" / "storage-state.json"
STATE_PATH = Path(os.getenv("ELMS_STATE_PATH", str(ROOT / "state.json"))).expanduser()
DOWNLOAD_DIR = Path(os.getenv("ELMS_DOWNLOAD_DIR", str(ROOT / "downloads"))).expanduser()

REQUEST_TIMEOUT_MS = 30_000

# Discord's documented attachment limits are currently 10 attachments and 10 MB
# per message. Staying below 8 MB leaves room for the embed payload.
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
PDF_DPI = 130
PDF_DPI_FALLBACKS = (130, 100, 80)


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE lines without adding a dependency."""

    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "").strip()

# Optional for headless/CI runs: a minimal JSON cookie jar for elms.sti.edu.
# When set, no browser is launched and the saved storage state is not needed.
ELMS_COOKIES_JSON = os.getenv("ELMS_COOKIES_JSON", "").strip()


def require_discord() -> None:
    """Raise a safe error if the bot token or channel is missing."""

    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set")
    if not DISCORD_CHANNEL_ID:
        raise RuntimeError("DISCORD_CHANNEL_ID is not set")
