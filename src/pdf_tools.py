"""Download attachments and render PDF pages as PNGs."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

import pymupdf

from .config import MAX_ATTACHMENT_BYTES, PDF_DPI, PDF_DPI_FALLBACKS

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str, fallback: str = "attachment") -> str:
    """Return a filesystem-safe basename, preserving its extension."""

    base = Path(name).name.strip() or fallback
    cleaned = _SAFE_NAME.sub("_", base).strip("._")
    return cleaned or fallback


def guess_mime(name: str, url: str = "") -> str | None:
    """Guess a MIME type from a filename or URL path."""

    return mimetypes.guess_type(name)[0] or mimetypes.guess_type(urlsplit(url).path)[0]


def download_attachment(session: object, url: str, destination: Path, referer: str) -> Path:
    """Download a same-site attachment through the authenticated session."""

    host = (urlsplit(url).hostname or "").lower()
    if host != "elms.sti.edu" and not host.endswith(".elms.sti.edu"):
        raise ValueError("refusing to download from a non-ELMS host")
    body = session.download(url, referer)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    if destination.stat().st_size == 0:
        raise RuntimeError("attachment download was empty")
    return destination


def render_pdf_pages(pdf_path: Path, out_dir: Path, dpi: int = PDF_DPI) -> list[Path]:
    """Render every PDF page to PNG at the requested DPI."""

    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    document = pymupdf.open(pdf_path)
    try:
        zoom = dpi / 72
        matrix = pymupdf.Matrix(zoom, zoom)
        for index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            destination = out_dir / f"page-{index:02d}.png"
            pixmap.save(destination)
            pages.append(destination)
    finally:
        document.close()
    return pages


def render_pdf_for_discord(
    pdf_path: Path,
    out_dir: Path,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> list[Path]:
    """Render a PDF, lowering DPI until each page fits the attachment budget."""

    last: list[Path] = []
    for dpi in PDF_DPI_FALLBACKS:
        last = render_pdf_pages(pdf_path, out_dir / f"dpi-{dpi}", dpi)
        if all(path.stat().st_size <= max_bytes for path in last):
            return last
    return last


def plan_batches(
    files: list[Path],
    max_files: int = 10,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> list[list[Path]]:
    """Group files so each batch respects Discord's count and size limits."""

    batches: list[list[Path]] = []
    current: list[Path] = []
    total = 0
    for path in files:
        size = path.stat().st_size
        if current and (len(current) >= max_files or total + size > max_bytes):
            batches.append(current)
            current = []
            total = 0
        current.append(path)
        total += size
    if current:
        batches.append(current)
    return batches


def attachment_url_is_safe(url: str) -> bool:
    """Return True for same-site ELMS file URLs."""

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme in {"https", "http"} and (host == "elms.sti.edu" or host.endswith(".elms.sti.edu"))
