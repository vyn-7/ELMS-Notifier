#!/usr/bin/env python3
"""Export the minimal ELMS cookie jar for headless/CI runs."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".session" / "storage-state.json"
OUT_PATH = ROOT / ".session" / "cookies.json"


def main() -> int:
    if not STATE_PATH.exists():
        print(f"Missing {STATE_PATH}; run scripts/recon.py --login-only first.")
        return 1
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    cookies = [
        cookie
        for cookie in state.get("cookies", [])
        if (cookie.get("domain") or "").lstrip(".") == "elms.sti.edu"
    ]
    if not cookies:
        print("No elms.sti.edu cookies in the saved storage state; re-login first.")
        return 1
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"cookies": cookies}, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(OUT_PATH, 0o600)
    print(f"Wrote {len(cookies)} ELMS cookie(s) to {OUT_PATH}")
    print("Keep it private. Set the GitHub secret with:")
    print("  gh secret set ELMS_COOKIES < .session/cookies.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
