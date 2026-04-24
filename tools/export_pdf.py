#!/usr/bin/env python3
"""Export markdown files to PDF. Supports single files and named bundles.

Usage:
  python tools/export_pdf.py README.md
  python tools/export_pdf.py ARCHITECTURE.md --out ~/Desktop/ARCHITECTURE.pdf
  python tools/export_pdf.py --bundle curator
  python tools/export_pdf.py --bundle german
  python tools/export_pdf.py --list-bundles
  python tools/export_pdf.py --test
"""
import argparse
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
)

BUNDLES = {
    "curator": [
        ("README.md",        "README.pdf"),
        ("ARCHITECTURE.md",  "ARCHITECTURE.pdf"),
        ("OPERATIONS.md",    "OPERATIONS.pdf"),
        ("ROADMAP.md",       "ROADMAP.pdf"),
        ("VISION.md",        "VISION.pdf"),
    ],
    "german": [
        ("_NewDomains/language-german/GERMAN_USER_GUIDE.md", "GERMAN_USER_GUIDE.pdf"),
        ("_NewDomains/language-german/SPEC.md",              "GERMAN_SPEC.pdf"),
    ],
}


def _find_repo_root() -> Path:
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / ".git").exists():
            return p
        p = p.parent
    return Path(__file__).resolve().parent.parent


def _output_dir() -> Path:
    """Resolve default output directory.

    Priority: tools_config.json export_pdf_output_dir → script's own directory.
    Creates the directory if it doesn't exist.
    """
    import json
    config_path = Path(__file__).parent / "tools_config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            raw = cfg.get("export_pdf_output_dir", "")
            if raw:
                d = Path(raw).expanduser().resolve()
                d.mkdir(parents=True, exist_ok=True)
                return d
        except Exception:
            pass
    # Fallback: same directory as this script
    d = Path(__file__).parent.resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _styles() -> dict:
    base = getSampleStyleSheet()
    # GitHub-flavored styling: dark charcoal text, light gray code bg, Helvetica sans-serif
    text_color = colors.HexColor("#1f2328")
    muted_color = colors.HexColor("#636c76")
    code_bg = colors.HexColor("#f6f8fa")
    border_color = colors.HexColor("#d0d7de")

    body = ParagraphStyle("Body", parent=base["Normal"], fontSize=10,
                          fontName="Helvetica", spaceAfter=6, leading=15,
                          textColor=text_color)
    return {
        "h1": ParagraphStyle("H1", parent=base["Heading1"], fontSize=18,
                             fontName="Helvetica-Bold", spaceAfter=10,
                             spaceBefore=4, textColor=text_color),
        "h2": ParagraphStyle("H2", parent=base["Heading2"], fontSize=14,
                             fontName="Helvetica-Bold", spaceAfter=8,
                             spaceBefore=18, textColor=text_color),
        "h3": ParagraphStyle("H3", parent=base["Heading3"], fontSize=12,
                             fontName="Helvetica-Bold", spaceAfter=6,
                             spaceBefore=14, textColor=text_color),
        "body": body,
        "bullet": ParagraphStyle("Bullet", parent=body, leftIndent=20,
                                 spaceAfter=3),
        "code": ParagraphStyle("Code", parent=base["Code"], fontSize=8.5,
                               leading=13, backColor=code_bg,
                               borderPadding=8, leftIndent=10, rightIndent=10,
                               spaceAfter=10, spaceBefore=6,
                               fontName="Courier", textColor=text_color),
        "meta": ParagraphStyle("Meta", parent=body, fontSize=9,
                               textColor=muted_color),
        "bq": ParagraphStyle("BQ", parent=body, fontSize=10, leftIndent=16,
                             textColor=muted_color),
    }


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _apply_inline(text: str) -> str:
    while "**" in text:
        text = text.replace("**", "<b>", 1).replace("**", "</b>", 1)
    while "`" in text:
        text = text.replace("`", "<font name='Courier' size='9'>", 1).replace("`", "</font>", 1)
    return text


def render_md_to_pdf(src: Path, out: Path, title: str = "") -> None:
    if not title:
        title = src.stem.replace("_", " ").replace("-", " ").title()

    st = _styles()
    doc = SimpleDocTemplate(
        str(out), pagesize=letter,
        leftMargin=inch, rightMargin=inch,
        topMargin=inch, bottomMargin=inch,
        title=title, author="Robert van Stedum",
    )

    lines = src.read_text(encoding="utf-8").splitlines()
    story = []
    i = 0
    in_code = False
    code_buf = []

    while i < len(lines):
        line = lines[i]

        if line.strip().startswith("```"):
            if not in_code:
                in_code = True
                code_buf = []
            else:
                in_code = False
                story.append(Preformatted("\n".join(code_buf), st["code"]))
            i += 1
            continue

        if in_code:
            code_buf.append(line)
            i += 1
            continue

        if line.strip() == "---":
            story.append(HRFlowable(width="100%", thickness=0.5,
                                    color=colors.HexColor("#d0d7de"),
                                    spaceAfter=8, spaceBefore=8))
            i += 1
            continue

        if line.startswith("# ") and not line.startswith("## "):
            story.append(Paragraph(_escape(line[2:]), st["h1"]))
            i += 1
            continue
        if line.startswith("## "):
            story.append(Paragraph(_escape(line[3:]), st["h2"]))
            i += 1
            continue
        if line.startswith("### "):
            story.append(Paragraph(_escape(line[4:]), st["h3"]))
            i += 1
            continue

        if line.startswith("**") and line.count("**") >= 2:
            text = _escape(line).replace("**", "<b>", 1).replace("**", "</b>", 1)
            story.append(Paragraph(text, st["meta"]))
            i += 1
            continue

        if line.startswith("> "):
            story.append(Paragraph(_escape(line[2:]), st["bq"]))
            i += 1
            continue

        if line.startswith("- "):
            text = _apply_inline(_escape(line[2:]))
            story.append(Paragraph("• " + text, st["bullet"]))
            i += 1
            continue

        if len(line) > 2 and line[0].isdigit() and line[1] in ".)":
            text = _apply_inline(_escape(line[2:].strip()))
            story.append(Paragraph(line[0] + ". " + text, st["bullet"]))
            i += 1
            continue

        if line.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].startswith("|"):
                if not all(c in "|-: " for c in lines[i]):
                    table_lines.append(lines[i])
                i += 1
            if table_lines:
                story.append(Preformatted("\n".join(table_lines), st["code"]))
            continue

        if line.strip() == "":
            story.append(Spacer(1, 4))
            i += 1
            continue

        story.append(Paragraph(_apply_inline(_escape(line)), st["body"]))
        i += 1

    doc.build(story)


def _run_tests() -> None:
    repo_root = _find_repo_root()
    out_dir = _output_dir()
    test_out = out_dir / "export_pdf_test.pdf"
    results = []

    # Test 1: Bundle resolution — at least one bundle has files that exist on disk
    try:
        found = False
        for bundle_name in ("curator", "german"):
            entries = BUNDLES.get(bundle_name, [])
            if any((repo_root / src_rel).exists() for src_rel, _ in entries):
                found = True
                break
        results.append(("Test 1 — Bundle resolution", found, None))
    except Exception as e:
        results.append(("Test 1 — Bundle resolution", False, str(e)))

    # Test 2: Single file conversion — README.md converts without error
    readme = repo_root / "README.md"
    converted = False
    try:
        if not readme.exists():
            raise FileNotFoundError(f"README.md not found at {readme}")
        render_md_to_pdf(readme, test_out)
        converted = True
        results.append(("Test 2 — Single file conversion", True, None))
    except Exception as e:
        results.append(("Test 2 — Single file conversion", False, str(e)))

    # Test 3: Output validation — file exists in output dir and is non-zero bytes
    try:
        if not converted:
            raise RuntimeError("Skipped — conversion failed in Test 2")
        size = test_out.stat().st_size if test_out.exists() else 0
        ok = test_out.exists() and size > 0
        results.append(("Test 3 — Output non-zero bytes", ok,
                        None if ok else f"size={size}, exists={test_out.exists()}"))
    except Exception as e:
        results.append(("Test 3 — Output non-zero bytes", False, str(e)))
    finally:
        if test_out.exists():
            test_out.unlink()

    print()
    all_pass = True
    for label, passed, detail in results:
        status = "PASS" if passed else "FAIL"
        print(f"{label + ':':<42} {status}")
        if not passed and detail:
            print(f"         {detail}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("All tests passed.")
    else:
        print("One or more tests FAILED.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export markdown files to PDF.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("file", nargs="?", help="Markdown file to export")
    parser.add_argument("--out", help="Output PDF path (overrides configured output dir)")
    parser.add_argument("--bundle", choices=list(BUNDLES.keys()),
                        help="Export a predefined bundle of documents")
    parser.add_argument("--list-bundles", action="store_true",
                        help="List available bundles and their contents")
    parser.add_argument("--test", action="store_true",
                        help="Run self-tests and report results")
    args = parser.parse_args()

    if args.test:
        _run_tests()
        return

    out_dir = _output_dir()
    repo_root = _find_repo_root()

    if args.list_bundles:
        for name, entries in BUNDLES.items():
            print(f"\n  {name}:")
            for src_rel, out_name in entries:
                print(f"    {src_rel}  →  {out_dir}/{out_name}")
        print()
        return

    if args.bundle:
        entries = BUNDLES[args.bundle]
        print(f"\nExporting bundle '{args.bundle}' ({len(entries)} files)…")
        for src_rel, out_name in entries:
            src = repo_root / src_rel
            out = out_dir / out_name
            if not src.exists():
                print(f"  ⚠️  Not found, skipping: {src_rel}")
                continue
            render_md_to_pdf(src, out)
            print(f"  ✅ {out_name}")
        print()
        return

    if not args.file:
        parser.print_help()
        sys.exit(1)

    src = Path(args.file)
    if not src.is_absolute():
        src = Path.cwd() / src
    if not src.exists():
        print(f"Error: file not found: {src}", file=sys.stderr)
        sys.exit(1)

    if args.out:
        out = Path(args.out).expanduser()
    else:
        out = out_dir / (src.stem + ".pdf")

    render_md_to_pdf(src, out)
    print(f"✅ PDF saved: {out}")


if __name__ == "__main__":
    main()
