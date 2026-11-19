"""Discord bot delivery: embeds, instruction chunks, PNG batches, alerts."""

from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx

from .config import (
    DISCORD_BOT_TOKEN,
    DISCORD_CHANNEL_ID,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    MAX_MESSAGE_BYTES,
)
from .models import Assignment
from .pdf_tools import plan_batches

EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_LIMIT = 1024
EMBED_TITLE_LIMIT = 256
MESSAGE_CONTENT_LIMIT = 2000


class DiscordError(RuntimeError):
    """Raised when Discord rejects a message."""


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def chunk_text(text: str, limit: int = 1900) -> list[str]:
    """Split long text into Discord-sized chunks without dropping content."""

    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return chunks


def _post_message(payload: dict[str, Any], files: dict[str, tuple[str, bytes, str]] | None = None) -> None:
    """POST one bot message, honoring 429 retry_after."""

    if not DISCORD_BOT_TOKEN:
        raise DiscordError("DISCORD_BOT_TOKEN is not set")
    if not DISCORD_CHANNEL_ID:
        raise DiscordError("DISCORD_CHANNEL_ID is not set")
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    for _ in range(4):
        if files:
            response = httpx.post(
                url,
                headers=headers,
                data={"payload_json": json.dumps(payload)},
                files=files,
                timeout=30,
            )
        else:
            response = httpx.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code == 429:
            try:
                retry_after = float(response.json().get("retry_after", 2))
            except (ValueError, TypeError):
                retry_after = 2
            time.sleep(min(max(retry_after, 0.5), 15))
            continue
        if response.status_code >= 300:
            raise DiscordError(f"Discord returned HTTP {response.status_code}")
        return
    raise DiscordError("Discord rate limit retries exhausted")


def build_embed(assignment: Assignment) -> dict[str, Any]:
    """Build the primary Discord embed for an assignment."""

    label = {
        "dropbox": "assignment",
        "offline": "offline assignment",
        "survey": "survey",
        "quiz": "quiz",
    }.get(assignment.kind, "item")
    title = _truncate(f"📚 New {label}: {assignment.title}", EMBED_TITLE_LIMIT)
    description = _truncate(
        assignment.instructions_text or "No instructions provided.",
        EMBED_DESCRIPTION_LIMIT,
    )
    if assignment.deadline is not None:
        timestamp = int(assignment.deadline.timestamp())
        deadline = f"<t:{timestamp}:F> (<t:{timestamp}:R>)"
        if assignment.deadline_raw:
            deadline += f"\n{_truncate(assignment.deadline_raw, 200)}"
    else:
        deadline = _truncate(assignment.deadline_raw or "Not specified", EMBED_FIELD_LIMIT)
    fields = [
        {"name": "Course", "value": _truncate(assignment.course, EMBED_FIELD_LIMIT), "inline": False},
        {"name": "Deadline", "value": _truncate(deadline, EMBED_FIELD_LIMIT), "inline": False},
        {"name": "Link", "value": f"[Open in ELMS]({assignment.url})"[:EMBED_FIELD_LIMIT], "inline": False},
    ]
    return {
        "title": title,
        "description": description,
        "color": 0x2F80ED,
        "fields": fields,
        "footer": {"text": _truncate(f"ID: {assignment.id}", 2048)},
    }


def send_alert(text: str) -> None:
    """Send a short operational alert; never include secrets or raw content."""

    payload = {
        "content": _truncate(f"⚠️ {text}", MESSAGE_CONTENT_LIMIT),
        "allowed_mentions": {"parse": []},
    }
    _post_message(payload)


def send_test() -> None:
    """Send one harmless test message."""

    _post_message(
        {
            "content": "✅ ELMS assignment notifier test message. Everything is wired up.",
            "allowed_mentions": {"parse": []},
        }
    )


def _files_payload(paths: list[Path]) -> dict[str, tuple[str, bytes, str]]:
    payload: dict[str, tuple[str, bytes, str]] = {}
    for index, path in enumerate(paths):
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        payload[f"files[{index}]"] = (path.name, path.read_bytes(), mime)
    return payload


def send_assignment(
    assignment: Assignment,
    png_groups: list[tuple[str, list[Path]]] | None = None,
    extra_files: list[Path] | None = None,
) -> None:
    """Send the embed, continuation chunks, PNG pages, and original files.

    Raises DiscordError if any message is rejected. The caller must mark the
    item seen only after this function returns successfully.
    """

    instructions = assignment.instructions_text.strip()
    _post_message(
        {
            "content": "@everyone",
            "embeds": [build_embed(assignment)],
            "allowed_mentions": {"parse": ["everyone"]},
        }
    )

    if len(instructions) > EMBED_DESCRIPTION_LIMIT:
        remainder = instructions[EMBED_DESCRIPTION_LIMIT:]
        for chunk in chunk_text(remainder):
            _post_message(
                {
                    "content": f"**{_truncate(assignment.title, 150)} — instructions (continued)**\n{chunk}",
                    "allowed_mentions": {"parse": []},
                }
            )

    for group_name, files in png_groups or []:
        batches = plan_batches(files, max_files=MAX_ATTACHMENTS, max_bytes=MAX_MESSAGE_BYTES)
        for batch in batches:
            valid = [path for path in batch if path.stat().st_size <= MAX_ATTACHMENT_BYTES]
            skipped = len(batch) - len(valid)
            if not valid:
                send_alert(f"PNG pages for {assignment.title} were too large to upload; original PDF attached.")
                continue
            first = files.index(valid[0]) + 1
            last = files.index(valid[-1]) + 1
            content = f"📄 {_truncate(assignment.title, 150)} — {group_name}, pages {first}–{last} of {len(files)}"
            if skipped:
                content += f" ({skipped} page(s) skipped: too large)"
            _post_message(
                {"content": content, "allowed_mentions": {"parse": []}},
                files=_files_payload(valid),
            )

    originals = extra_files or []
    for batch in plan_batches(originals, max_files=MAX_ATTACHMENTS, max_bytes=MAX_MESSAGE_BYTES):
        valid = [path for path in batch if path.stat().st_size <= MAX_ATTACHMENT_BYTES]
        for path in batch:
            if path not in valid:
                _post_message(
                    {
                        "content": f"📎 Original file {path.name} is too large to upload.",
                        "allowed_mentions": {"parse": []},
                    }
                )
        if valid:
            names = ", ".join(path.name for path in valid)
            _post_message(
                {
                    "content": f"📎 Original file(s) — {_truncate(names, 300)}",
                    "allowed_mentions": {"parse": []},
                },
                files=_files_payload(valid),
            )
