#!/usr/bin/env python3
"""Build and verify mini-moi's four maintained root PDFs."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader, PdfWriter

logging.getLogger("pypdf").setLevel(logging.ERROR)


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
MANIFEST_PATH = SCRIPT_DIR / "key-docs-manifest.json"
RENDERER_FILES = (
    SCRIPT_DIR / "publish_key_docs.py",
    SCRIPT_DIR / "render_key_doc.mjs",
    SCRIPT_DIR / "key-docs.css",
    SCRIPT_DIR / "package.json",
    SCRIPT_DIR / "package-lock.json",
    SCRIPT_DIR / "requirements.txt",
)
DOCUMENTS = (
    ("README.md", "README.pdf", "mini-moi"),
    ("ARCHITECTURE.md", "ARCHITECTURE.pdf", "mini-moi Architecture"),
    ("OPERATIONS.md", "OPERATIONS.pdf", "mini-moi Operations"),
    ("ROADMAP.md", "ROADMAP.pdf", "mini-moi Roadmap"),
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def renderer_hash() -> str:
    digest = hashlib.sha256()
    for path in RENDERER_FILES:
        if not path.exists():
            raise FileNotFoundError(f"Renderer dependency is missing: {path}")
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def mermaid_count(source: Path) -> int:
    return len(re.findall(r"^```mermaid\s*$", source.read_text(encoding="utf-8"), re.M))


def pdf_details(path: Path) -> tuple[dict[str, str], int, int]:
    reader = PdfReader(str(path), strict=True)
    metadata = {str(k): str(v) for k, v in (reader.metadata or {}).items()}
    text_length = sum(len(page.extract_text() or "") for page in reader.pages)
    return metadata, len(reader.pages), text_length


def normalize_pdf(
    raw_path: Path,
    output_path: Path,
    *,
    title: str,
    source_hash: str,
    render_hash: str,
) -> None:
    reader = PdfReader(str(raw_path), strict=True)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer.metadata = {}
    writer.add_metadata(
        {
            "/Title": title,
            "/Author": "Robert van Stedum",
            "/Subject": "mini-moi maintained key document",
            "/Creator": "mini-moi key-document publisher",
            "/Producer": "Playwright Chromium + pypdf",
            "/SourceSHA256": source_hash,
            "/RendererSHA256": render_hash,
        }
    )
    temp_output = output_path.with_suffix(".pdf.pending")
    with temp_output.open("wb") as stream:
        writer.write(stream)
    os.replace(temp_output, output_path)


def node_command() -> str:
    value = os.environ.get("NODE") or shutil.which("node")
    if not value:
        raise RuntimeError("Node.js 20+ is required but was not found.")
    return value


def render_document(
    source: Path,
    output: Path,
    title: str,
    expected_diagrams: int,
    render_hash: str,
) -> dict[str, object]:
    if not (SCRIPT_DIR / "node_modules").is_dir():
        raise RuntimeError(
            "Documentation renderer dependencies are missing. Run: "
            "npm ci --prefix scripts/docs"
        )
    with tempfile.TemporaryDirectory(prefix="minimoi-key-doc-") as directory:
        raw_pdf = Path(directory) / output.name
        command = [
            node_command(),
            str(SCRIPT_DIR / "render_key_doc.mjs"),
            str(source),
            str(raw_pdf),
            title,
            str(REPO_ROOT),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or str(error)).strip()
            if "Executable doesn't exist" in detail:
                detail += (
                    "\nInstall Chromium with: "
                    "npx --prefix scripts/docs playwright install chromium"
                )
            raise RuntimeError(detail) from error
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        result = json.loads(lines[-1]) if lines else {}
        if int(result.get("diagrams", -1)) != expected_diagrams:
            raise RuntimeError(
                f"{source.name}: renderer reported {result.get('diagrams')} diagrams; "
                f"expected {expected_diagrams}"
            )
        source_hash = sha256_file(source)
        normalize_pdf(
            raw_pdf,
            output,
            title=title,
            source_hash=source_hash,
            render_hash=render_hash,
        )

    metadata, pages, text_length = pdf_details(output)
    if pages < 1 or text_length < 100:
        raise RuntimeError(
            f"{output.name}: structural validation failed "
            f"(pages={pages}, extractable_text={text_length})"
        )
    return {
        "source": source.name,
        "pdf": output.name,
        "source_sha256": source_hash,
        "renderer_sha256": render_hash,
        "pdf_sha256": sha256_file(output),
        "mermaid_diagrams": expected_diagrams,
        "pages": pages,
        "extractable_text_characters": text_length,
        "title": metadata.get("/Title", title),
    }


def publish() -> int:
    render_hash = renderer_hash()
    records = []
    for source_name, pdf_name, title in DOCUMENTS:
        source = REPO_ROOT / source_name
        output = REPO_ROOT / pdf_name
        diagrams = mermaid_count(source)
        print(f"Publishing {source_name} -> {pdf_name} ({diagrams} diagrams)")
        records.append(
            render_document(source, output, title, diagrams, render_hash)
        )
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "publisher": "python3 scripts/docs/publish_key_docs.py",
        "documents": records,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    print(f"Published {len(records)} key-document PDFs.")
    return check()


def check() -> int:
    errors: list[str] = []
    try:
        render_hash = renderer_hash()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not MANIFEST_PATH.exists():
        errors.append(f"Missing {MANIFEST_PATH.relative_to(REPO_ROOT)}")
        manifest_records = {}
    else:
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            manifest_records = {
                item["source"]: item for item in manifest.get("documents", [])
            }
        except Exception as error:
            errors.append(f"Invalid PDF manifest: {error}")
            manifest_records = {}

    for source_name, pdf_name, title in DOCUMENTS:
        source = REPO_ROOT / source_name
        pdf = REPO_ROOT / pdf_name
        source_hash = sha256_file(source)
        diagrams = mermaid_count(source)
        record = manifest_records.get(source_name)
        if not pdf.exists():
            errors.append(f"{pdf_name}: missing")
            continue
        try:
            metadata, pages, text_length = pdf_details(pdf)
        except Exception as error:
            errors.append(f"{pdf_name}: unreadable ({error})")
            continue
        if metadata.get("/SourceSHA256") != source_hash:
            errors.append(f"{pdf_name}: stale source hash")
        if metadata.get("/RendererSHA256") != render_hash:
            errors.append(f"{pdf_name}: stale renderer hash")
        if metadata.get("/Title") != title:
            errors.append(f"{pdf_name}: unexpected title metadata")
        if pages < 1 or text_length < 100:
            errors.append(
                f"{pdf_name}: invalid structure "
                f"(pages={pages}, extractable_text={text_length})"
            )
        if not record:
            errors.append(f"{pdf_name}: missing manifest record")
            continue
        expected = {
            "pdf": pdf_name,
            "source_sha256": source_hash,
            "renderer_sha256": render_hash,
            "pdf_sha256": sha256_file(pdf),
            "mermaid_diagrams": diagrams,
            "pages": pages,
            "extractable_text_characters": text_length,
            "title": title,
        }
        for key, value in expected.items():
            if record.get(key) != value:
                errors.append(
                    f"{pdf_name}: manifest {key} is {record.get(key)!r}; "
                    f"expected {value!r}"
                )

    if errors:
        print("Key-document PDF check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print(
            "\nRegenerate with: python3 scripts/docs/publish_key_docs.py",
            file=sys.stderr,
        )
        return 1
    print("Key-document PDFs are present, current, and structurally valid.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify committed PDFs and manifest without writing files.",
    )
    args = parser.parse_args()
    return check() if args.check else publish()


if __name__ == "__main__":
    sys.exit(main())
