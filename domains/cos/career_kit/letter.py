"""Assemble a cover letter from parts: the posting-side opening, the owner's fixed middle, an optional extra
paragraph, and a short close. The owner writes every part; this only assembles and checks.

    python -m domains.cos.career_kit.letter PARTS.json OUTPUT.docx

PARTS.json: {"greeting": "Hello,", "opening": "...", "middle": ["...", "..."], "extra": "..." (optional),
"close": "...", "signoff": "Regards,", "name": "..."}. The opening must be written for the posting; a placeholder
(text in [brackets], or the word "placeholder") is refused. Never overwrites OUTPUT. Writes OUTPUT and a .md copy.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from docx import Document
from docx.shared import Pt

REQUIRED = ("greeting", "opening", "middle", "close", "signoff", "name")


class PartsInvalid(ValueError):
    pass


def assemble(parts: dict) -> list[str]:
    missing = [k for k in REQUIRED if not parts.get(k)]
    if missing:
        raise PartsInvalid("missing: " + ", ".join(missing))
    if "[" in parts["opening"] or "placeholder" in parts["opening"].lower():
        raise PartsInvalid("the opening is still a placeholder; write it from the posting's side")
    if not isinstance(parts["middle"], list) or not all(isinstance(p, str) and p.strip() for p in parts["middle"]):
        raise PartsInvalid("middle must be a list of paragraphs")
    body = [parts["opening"], *parts["middle"]] + ([parts["extra"]] if parts.get("extra") else []) + [parts["close"]]
    return [parts["greeting"], *[p.strip() for p in body], parts["signoff"], parts["name"]]


def write(parts: dict, out: Path) -> Path:
    if out.exists() or out.with_suffix(".md").exists():
        raise FileExistsError(f"{out} exists; write a new versioned name instead")
    paragraphs = assemble(parts)
    doc = Document()
    doc.styles["Normal"].font.name, doc.styles["Normal"].font.size = "Calibri", Pt(11)
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(out)
    out.with_suffix(".md").write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python -m domains.cos.career_kit.letter PARTS.json OUTPUT.docx", file=sys.stderr)
        return 2
    try:
        print(write(json.loads(Path(argv[0]).read_text(encoding="utf-8")), Path(argv[1])))
    except (PartsInvalid, FileExistsError, json.JSONDecodeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
