"""File intake: receive -> validate -> normalise to a canonical PDF.

Everything that can be printed becomes a PDF on disk under a random name. The
original filename is kept only for display, never for paths. The SHA-256 of the
canonical PDF is what the print authorisation is bound to.
"""
from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..config import Settings


class FileRejected(ValueError):
    """Raised with a customer-friendly reason."""


OFFICE_EXT = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods", ".rtf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg"}
TEXT_EXT = {".txt"}
PDF_EXT = {".pdf"}


@dataclass
class IngestResult:
    stored_name: str
    sha256: str
    size: int
    pages: int
    display_name: str


def safe_display_name(name: str) -> str:
    base = Path((name or "document").replace("\\", "/")).name
    cleaned = "".join(c for c in base if c.isprintable() and c not in '<>:"|?*')
    return (cleaned or "document")[:120]


def read_limited(stream: BinaryIO, limit_bytes: int) -> bytes:
    buf = io.BytesIO()
    while chunk := stream.read(1024 * 1024):
        buf.write(chunk)
        if buf.tell() > limit_bytes:
            raise FileRejected(f"File is larger than the {limit_bytes // (1024 * 1024)} MB limit")
    data = buf.getvalue()
    if not data:
        raise FileRejected("The file is empty")
    return data


def _sniff(data: bytes, ext: str) -> str:
    """Trust magic bytes, not the extension."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"PK\x03\x04" and ext in OFFICE_EXT:
        return "office"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" and ext in OFFICE_EXT:
        return "office"
    if data[:5] == b"{\\rtf" and ext in OFFICE_EXT:
        return "office"
    if ext in TEXT_EXT:
        try:
            data[:4096].decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            pass
    raise FileRejected("Unsupported or corrupted file. Use PDF, PNG, JPG, TXT or Office documents.")


def _image_to_pdf(data: bytes) -> bytes:
    from PIL import Image
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise FileRejected("The image could not be read") from e
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    pw, ph = A4
    if img.width > img.height:               # landscape photo -> landscape page
        pw, ph = ph, pw
    margin = 28
    scale = min((pw - 2 * margin) / img.width, (ph - 2 * margin) / img.height)
    w, h = img.width * scale, img.height * scale
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=(pw, ph))
    c.drawImage(ImageReader(img), (pw - w) / 2, (ph - h) / 2, w, h)
    c.save()
    return out.getvalue()


def _text_to_pdf(data: bytes) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    text = data.decode("utf-8", errors="replace").replace("\t", "    ")
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=A4)
    width, height = A4
    left, top, leading, size = 50, height - 60, 13, 10
    max_chars = 92
    y = top
    c.setFont("Courier", size)
    for raw in text.splitlines() or [""]:
        parts = [raw[i:i + max_chars] for i in range(0, len(raw), max_chars)] or [""]
        for part in parts:
            if y < 55:
                c.showPage()
                c.setFont("Courier", size)
                y = top
            c.drawString(left, y, part.encode("latin-1", "replace").decode("latin-1"))
            y -= leading
    c.save()
    return out.getvalue()


def _office_to_pdf(data: bytes, ext: str, enabled: bool) -> bytes:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not enabled or not soffice:
        raise FileRejected("Office documents are not supported on this server. Please export to PDF first.")
    with tempfile.TemporaryDirectory(prefix="sp-conv-") as tmp:
        src = Path(tmp) / f"in{ext}"
        src.write_bytes(data)
        try:
            subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                           check=True, capture_output=True, timeout=90,
                           env={"HOME": tmp, "PATH": "/usr/bin:/bin:/usr/local/bin"})
        except (subprocess.SubprocessError, OSError) as e:
            raise FileRejected("The document could not be converted to PDF") from e
        out = Path(tmp) / "in.pdf"
        if not out.exists():
            raise FileRejected("The document could not be converted to PDF")
        return out.read_bytes()


def _validate_pdf(pdf: bytes, max_pages: int) -> int:
    try:
        reader = PdfReader(io.BytesIO(pdf))
        if reader.is_encrypted:
            raise FileRejected("Password-protected PDFs cannot be printed. Remove the password first.")
        n = len(reader.pages)
    except FileRejected:
        raise
    except (PdfReadError, Exception) as e:  # noqa: BLE001
        raise FileRejected("The PDF is damaged or unreadable") from e
    if n < 1:
        raise FileRejected("The document has no pages")
    if n > max_pages:
        raise FileRejected(f"The document has {n} pages; the limit is {max_pages}")
    return n


def ingest(stream: BinaryIO, filename: str, settings: Settings) -> IngestResult:
    display = safe_display_name(filename)
    ext = Path(display).suffix.lower()
    data = read_limited(stream, settings.max_upload_mb * 1024 * 1024)
    kind = _sniff(data, ext)
    if kind == "pdf":
        pdf = data
    elif kind in ("png", "jpeg"):
        pdf = _image_to_pdf(data)
    elif kind == "text":
        pdf = _text_to_pdf(data)
    else:
        pdf = _office_to_pdf(data, ext, settings.convert_office_docs)
    pages = _validate_pdf(pdf, settings.max_pages)

    settings.ensure_dirs()
    stored = f"{uuid.uuid4().hex}.pdf"
    path = settings.files_dir / stored
    tmp = path.with_suffix(".part")
    tmp.write_bytes(pdf)
    tmp.replace(path)                        # atomic publish
    return IngestResult(stored, hashlib.sha256(pdf).hexdigest(), len(pdf), pages, display)


def file_path(settings: Settings, stored_name: str) -> Path:
    p = (settings.files_dir / stored_name).resolve()
    if p.parent != settings.files_dir.resolve():
        raise ValueError("path escape")
    return p


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def render_preview_png(path: Path, page: int = 0, width: int = 520) -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        pg = pdf[page]
        scale = width / pg.get_width()
        bmp = pg.render(scale=scale)
        out = io.BytesIO()
        bmp.to_pil().save(out, format="PNG")
        return out.getvalue()
    finally:
        pdf.close()
