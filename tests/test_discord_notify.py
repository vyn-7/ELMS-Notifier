from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src import discord_notify
from src.config import TIMEZONE
from src.models import Assignment


def make_assignment(**overrides) -> Assignment:
    values = {
        "id": "dropbox:2001",
        "course": "Test Course 101",
        "title": "Sample Essay",
        "url": "https://elms.sti.edu/student_dropbox_assignment/show/2001",
        "deadline": datetime(2026, 11, 22, 23, 58, tzinfo=TIMEZONE),
        "deadline_raw": "Nov 22",
        "instructions_text": "Write a short essay.",
        "kind": "dropbox",
    }
    values.update(overrides)
    return Assignment(**values)


def test_chunk_text_keeps_all_words() -> None:
    text = "word " * 1000
    chunks = discord_notify.chunk_text(text, limit=100)
    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_build_embed_has_required_fields() -> None:
    embed = discord_notify.build_embed(make_assignment())
    assert "description" not in embed
    assert embed["title"].startswith("📚 New assignment:")
    assert [field["name"] for field in embed["fields"]] == ["Course", "Deadline", "Link"]
    assert "<t:" in embed["fields"][1]["value"]
    assert "Open in ELMS" in embed["fields"][2]["value"]
    assert "dropbox:2001" in embed["footer"]["text"]


def test_send_assignment_sends_embed_pngs_and_original(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_post(payload, files=None):
        calls.append((payload, files))

    monkeypatch.setattr(discord_notify, "_post_message", fake_post)
    png_one = tmp_path / "page-01.png"
    png_two = tmp_path / "page-02.png"
    png_one.write_bytes(b"png-one")
    png_two.write_bytes(b"png-two")
    original = tmp_path / "sample.pdf"
    original.write_bytes(b"pdf")

    discord_notify.send_assignment(
        make_assignment(),
        png_groups=[("sample.pdf", [png_one, png_two])],
        extra_files=[original],
    )

    assert len(calls) == 3
    assert calls[0][0]["content"] == "@everyone"
    assert calls[0][0]["allowed_mentions"] == {"parse": ["everyone"]}
    assert "embeds" in calls[0][0]
    assert calls[1][1] is not None
    assert "pages 1–2 of 2" in calls[1][0]["content"]
    assert "Original file" in calls[2][0]["content"]


def test_post_message_uses_bot_api(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        status_code = 200

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(discord_notify.httpx, "post", fake_post)
    monkeypatch.setattr(discord_notify, "DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setattr(discord_notify, "DISCORD_CHANNEL_ID", "123")

    discord_notify._post_message({"content": "hello"})

    assert captured["url"] == "https://discord.com/api/v10/channels/123/messages"
    assert captured["headers"]["Authorization"] == "Bot test-token"
    assert captured["json"] == {"content": "hello"}


def test_post_message_requires_bot_token(monkeypatch) -> None:
    monkeypatch.setattr(discord_notify, "DISCORD_BOT_TOKEN", "")
    with pytest.raises(discord_notify.DiscordError):
        discord_notify._post_message({"content": "hello"})
