"""Page count of a .docx, rendered headless with LibreOffice (no dialogs, nothing left open). None when LibreOffice
is missing or rendering fails; callers say "not checked" rather than guess. Word may paginate slightly differently."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

CANDIDATES = ("soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice")


def soffice() -> str | None:
    for c in CANDIDATES:
        found = shutil.which(c) or (c if Path(c).is_file() else None)
        if found:
            return found
    return None


def page_count(docx: Path, timeout: float = 120) -> int | None:
    exe = soffice()
    if exe is None:
        return None
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp) / "profile"                    # own profile: never touches a running LibreOffice
        try:
            subprocess.run([exe, f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", "pdf",
                            "--outdir", tmp, str(docx)], check=True, capture_output=True, timeout=timeout)
            pdf = Path(tmp) / (docx.stem + ".pdf")
            return len(PdfReader(str(pdf)).pages)
        except (subprocess.SubprocessError, OSError, ValueError):
            return None
