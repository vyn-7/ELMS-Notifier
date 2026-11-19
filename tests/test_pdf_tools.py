from __future__ import annotations

from pathlib import Path

import pymupdf

from src.pdf_tools import (
    attachment_url_is_safe,
    plan_batches,
    render_pdf_for_discord,
    render_pdf_pages,
    safe_filename,
)


def make_pdf(path: Path, pages: int = 3) -> Path:
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"Test page {index + 1}")
    document.save(path)
    document.close()
    return path


def test_render_multipage_pdf_to_png(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "sample.pdf", pages=3)
    pages = render_pdf_pages(pdf, tmp_path / "png")
    assert len(pages) == 3
    assert all(path.exists() and path.stat().st_size > 0 for path in pages)
    assert pages[0].name == "page-01.png"


def test_render_for_discord_returns_pages(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "sample.pdf", pages=2)
    pages = render_pdf_for_discord(pdf, tmp_path / "out")
    assert len(pages) == 2
    assert all(path.stat().st_size > 0 for path in pages)


def test_plan_batches_respects_count_and_size(tmp_path) -> None:
    files = []
    for index in range(3):
        path = tmp_path / f"{index}.bin"
        path.write_bytes(b"x" * 4)
        files.append(path)
    batches = plan_batches(files, max_files=2, max_bytes=10)
    assert [len(batch) for batch in batches] == [2, 1]


def test_safe_filename_and_same_site_check() -> None:
    assert safe_filename("../../My File (1).pdf") == "My_File_1_.pdf"
    assert attachment_url_is_safe("https://elms.sti.edu/files/1/a.pdf")
    assert not attachment_url_is_safe("https://example.com/a.pdf")
