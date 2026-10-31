"""Write the exported resume INTO the owner's own resume (.docx) so it keeps their layout.

The template is the resume the workbook was imported from. Kept verbatim: everything before the first section heading
(name, contact line, links) and everything from EDUCATION to the end. Replaced: the summary, current work and
experience in between, written by cloning the template's own paragraph formats (section heading, role line, dates,
body text, bullet). A bullet "Label: text" gets the label in bold when the template's bullets do that.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from .resume_import import SECTIONS


class TemplateUnknown(ValueError):
    pass


class ResumeWriter:
    def __init__(self, template: Path):
        self.doc = Document(template)
        body = self.doc.element.body
        paras = self.doc.paragraphs
        texts = [p.text.strip() for p in paras]
        heads = [i for i, t in enumerate(texts) if t in SECTIONS]
        if not heads or "EDUCATION" not in texts:
            raise TemplateUnknown("template needs section headings and an EDUCATION section")
        first, edu = heads[0], texts.index("EDUCATION")
        exp = texts.index("PROFESSIONAL EXPERIENCE") if "PROFESSIONAL EXPERIENCE" in texts else first

        def sample(pred, start=0):
            for i in range(start, len(paras)):
                if pred(i, paras[i]):
                    return copy.deepcopy(paras[i]._p)
            raise TemplateUnknown("template lacks a paragraph to copy the format from")

        self.samples = {
            "heading": copy.deepcopy(paras[first]._p),
            "role": sample(lambda i, p: "|" in texts[i] and not p.style.name.startswith("List"), exp),
            "body": sample(lambda i, p: texts[i] and i > first and texts[i] not in SECTIONS and "|" not in texts[i]
                           and not p.style.name.startswith("List")),
            "bullet": sample(lambda i, p: p.style.name.startswith("List") and texts[i], exp),
            "bullet_label": None,
        }
        try:
            self.samples["spacer"] = sample(lambda i, p: not texts[i] and i > 0)
        except TemplateUnknown:                                # a template without blank spacer lines
            self.samples["spacer"] = None
        self.samples["dates"] = sample(lambda i, p: texts[i - 1:i] and "|" in texts[i - 1] and texts[i], exp + 1)
        labelled = [p for p in paras[exp:edu] if p.style.name.startswith("List") and len(p.runs) >= 2 and p.runs[0].bold]
        self.bold_labels = bool(labelled)
        if labelled:
            self.samples["bullet_label"] = copy.deepcopy(labelled[0]._p)
        # cut the middle: from the first section heading up to (not including) EDUCATION's spacer/heading
        keep_tail_from = paras[edu]._p
        prev = keep_tail_from.getprevious()
        if prev is not None and prev.tag == qn("w:p") and not "".join(prev.itertext()).strip():
            keep_tail_from = prev                                  # keep the spacer before EDUCATION
        el = paras[first]._p
        while el is not None and el is not keep_tail_from:
            nxt = el.getnext()
            body.remove(el)
            el = nxt
        self.anchor = keep_tail_from

    def _add(self, kind: str, chunks: list[str]):
        el = copy.deepcopy(self.samples[kind])
        runs = [r for r in el if r.tag == qn("w:r")]
        for child in list(el):
            if child.tag in (qn("w:r"), qn("w:hyperlink")):
                el.remove(child)
        for i, text in enumerate(chunks):
            run = copy.deepcopy(runs[min(i, len(runs) - 1)]) if runs else el.makeelement(qn("w:r"), {})
            for child in list(run):
                if child.tag != qn("w:rPr"):
                    run.remove(child)
            t = run.makeelement(qn("w:t"), {})
            t.text = text
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            run.append(t)
            el.append(run)
        self.anchor.addprevious(el)

    def heading(self, text):
        self._add("heading", [text])

    def spacer(self):
        if self.samples["spacer"] is not None:
            self._add("spacer", [])

    def role(self, title, employer, place):
        # the kit's resume format: "Title " (bold) · " |" (bold) · "   Employer" · "   Place" (smaller)
        self._add("role", [f"{title} ", " |", f"   {employer}"] + ([f"   {place}"] if place else []))

    def dates(self, text):
        self._add("dates", [text])

    def body(self, text):
        self._add("body", [text])

    def bullet(self, text):
        m = re.match(r"^([^:]{2,48}):\s+(.+)$", text)
        if self.bold_labels and m:
            self._add("bullet_label", [m.group(1) + ": ", m.group(2)])
        else:
            self._add("bullet", [text])

    def save(self, out: Path):
        self.doc.save(out)
