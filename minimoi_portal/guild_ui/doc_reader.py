"""Reading an uploaded file into plain text for Master Craftsman (Guild 1.1, upload reading).

Master Craftsman takes text only (the pinned relay carries a plain string), so a file reaches it as extracted
text or not at all. This module decides what can be read and reads it, and says plainly what cannot:

* text and code (checked by content, UTF-8, no binary), HTML (text only), PDF (its text layer; a scan with no text is
  "no readable text", never invented), DOCX (the body);
* images are NOT read here: they are kept as pictures and reported "not read";
* anything else is refused with the reason.

Untrusted bytes are parsed in a child process with a memory limit, a CPU limit and a wall-clock timeout, never in the
web worker. This file is self-contained (standard library, plus pypdf and bs4 when those formats are read) so the
child can run it as a script without importing the portal.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import zipfile

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_STORED_CHARS = 30_000          # what is kept per file, and the most that can be sent for it
MAX_PDF_PAGES = 100
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
MAX_DOCX_RATIO = 200
CHILD_TIMEOUT_S = 30
CHILD_MEMORY_BYTES = 1024 * 1024 * 1024

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".log", ".ini", ".toml",
            ".cfg", ".py", ".js", ".ts", ".sh", ".sql"}
HTML_EXT = {".html", ".htm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp", ".tif", ".tiff", ".svg"}
ACCEPT = sorted(TEXT_EXT | HTML_EXT | {".pdf", ".docx"})

KIND_WORDS = {"text": "text", "html": "web page", "pdf": "PDF", "docx": "Word document", "image": "image"}


class Unreadable(Exception):
    """The file cannot be read as text. ``code`` is stable; ``message`` is one plain sentence for the owner."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def extension(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()


def kind_of(name: str) -> str | None:
    """The kind a file name claims: text, html, pdf, docx, image, or None when it is not a readable kind."""
    ext = extension(name)
    if ext in TEXT_EXT:
        return "text"
    if ext in HTML_EXT:
        return "html"
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in IMAGE_EXT:
        return "image"
    return None


def refusal_for(name: str) -> tuple[str, str] | None:
    """(code, sentence) when a file of this name cannot be read at all, else None."""
    kind = kind_of(name)
    if kind == "image":
        return "image", "Images are kept as pictures; Master Craftsman can't see images yet."
    if kind is None:
        ext = extension(name) or "this kind"
        return "unsupported", (f"{ext} files can't be read yet. Text, code, Markdown, CSV, JSON, HTML, PDF (with text) and "
                               "Word (.docx) files can.")
    return None


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _CONTROL.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def _decode_text(raw: bytes) -> str:
    if b"\x00" in raw:
        raise Unreadable("binary", "This file looks like binary data, not text.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Unreadable("not_utf8", "This file is not UTF-8 text, so it can't be read reliably.") from None
    if text and len(_CONTROL.findall(text)) > max(8, len(text) // 20):
        raise Unreadable("binary", "This file looks like binary data, not text.")
    return text


def _html_text(raw: bytes) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_decode_text(raw), "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "head"]):
        tag.decompose()
    return soup.get_text("\n")


def _pdf_text(raw: bytes) -> tuple[str, int, int]:
    """(text, pages read, pages in the file)."""
    if not raw.lstrip().startswith(b"%PDF-"):
        raise Unreadable("corrupt", "This does not look like a PDF.")
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise Unreadable("encrypted", "This PDF is password-protected, so it can't be read.")
        total = len(reader.pages)
        read, parts, size = 0, [], 0
        for page in list(reader.pages)[:MAX_PDF_PAGES]:
            piece = page.extract_text() or ""
            read += 1
            parts.append(piece)
            size += len(piece)
            if size > MAX_STORED_CHARS * 20:           # far beyond anything that can be sent; stop reading
                break
    except Unreadable:
        raise
    except Exception:
        raise Unreadable("corrupt", "This PDF could not be read; it may be damaged.") from None
    return "\n\n".join(parts), read, total


def _docx_text(raw: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise Unreadable("corrupt", "This does not look like a Word (.docx) file.") from None
    infos = archive.infolist()
    if len(infos) > 2000:
        raise Unreadable("corrupt", "This Word file is not a normal document.")
    try:
        info = archive.getinfo("word/document.xml")
    except KeyError:
        raise Unreadable("corrupt", "This does not look like a Word (.docx) file.") from None
    if info.file_size > MAX_DOCX_XML_BYTES or info.file_size > max(1, info.compress_size) * MAX_DOCX_RATIO + 1_000_000:
        raise Unreadable("too_large", "This Word file expands to far more than a normal document, so it was not opened.")
    xml = archive.read("word/document.xml")
    if re.search(rb"<!(?:DOCTYPE|ENTITY)", xml, re.I):
        raise Unreadable("corrupt", "This Word file contains something a normal document does not, so it was not opened.")
    import xml.etree.ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    out: list[str] = []
    try:
        for _event, node in ET.iterparse(io.BytesIO(xml), events=("end",)):
            tag = node.tag
            if tag == ns + "t":
                out.append(node.text or "")
            elif tag == ns + "tab":
                out.append("\t")
            elif tag == ns + "br":
                out.append("\n")
            elif tag == ns + "p":
                out.append("\n")
            elif tag == ns + "tc":
                out.append("\t")
            if tag in (ns + "p", ns + "tr"):
                node.clear()
    except ET.ParseError:
        raise Unreadable("corrupt", "This Word file could not be read; it may be damaged.") from None
    return "".join(out)


def extract(raw: bytes, name: str) -> dict:
    """Read ``raw`` as text. Returns {text, kind, pages_read, pages_total, chars_total, truncated}; ``text`` holds at
    most MAX_STORED_CHARS and ``chars_total`` counts what the file had (a lower bound when a huge file stopped early).
    Raises Unreadable. Call extract_safely() from the portal: this runs in the current process."""
    refusal = refusal_for(name)
    if refusal:
        raise Unreadable(*refusal)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise Unreadable("too_large", f"This file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    kind = kind_of(name)
    pages_read = pages_total = None
    if kind == "text":
        text = _decode_text(raw)
    elif kind == "html":
        text = _html_text(raw)
    elif kind == "pdf":
        text, pages_read, pages_total = _pdf_text(raw)
    else:
        text = _docx_text(raw)
    text = _tidy(text)
    if not text.strip():
        if kind == "pdf":
            raise Unreadable("no_text", "This PDF has no text layer (is it a scan?), so there is nothing to read.")
        raise Unreadable("empty", "This file has no text in it.")
    total = len(text)
    truncated = total > MAX_STORED_CHARS or bool(pages_total and pages_read < pages_total)
    return {"text": text[:MAX_STORED_CHARS], "kind": kind, "pages_read": pages_read, "pages_total": pages_total,
            "chars_total": total, "truncated": truncated}


def extract_safely(raw: bytes, name: str) -> dict:
    """extract() in a child process: memory-limited, CPU-limited, with a wall-clock timeout. Same result or Unreadable."""
    refusal = refusal_for(name)
    if refusal:
        raise Unreadable(*refusal)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise Unreadable("too_large", f"This file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
    try:
        done = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "--child", name], input=raw,
                              capture_output=True, timeout=CHILD_TIMEOUT_S, env=env, cwd="/")
    except subprocess.TimeoutExpired:
        raise Unreadable("timeout", "Reading this file took too long, so it was stopped.") from None
    except OSError:
        raise Unreadable("unavailable", "The file reader is unavailable right now.") from None
    try:
        answer = json.loads(done.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Unreadable("failed", "This file could not be read.") from None
    if not isinstance(answer, dict):
        raise Unreadable("failed", "This file could not be read.")
    if not answer.get("ok"):
        raise Unreadable(str(answer.get("code") or "failed"), str(answer.get("message") or "This file could not be read."))
    answer.pop("ok", None)
    return answer


def _child() -> None:
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (20, 25))
        try:
            resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY_BYTES, CHILD_MEMORY_BYTES))
        except (ValueError, OSError):
            pass
    except ImportError:
        pass
    name = sys.argv[2] if len(sys.argv) > 2 else ""
    raw = sys.stdin.buffer.read(MAX_UPLOAD_BYTES + 1)
    try:
        answer = {"ok": True, **extract(raw, name)}
    except Unreadable as exc:
        answer = {"ok": False, "code": exc.code, "message": exc.message}
    except MemoryError:
        answer = {"ok": False, "code": "too_large", "message": "This file needs more memory to read than is allowed."}
    except Exception:
        answer = {"ok": False, "code": "failed", "message": "This file could not be read."}
    sys.stdout.write(json.dumps(answer, ensure_ascii=False))


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--child":
    _child()
