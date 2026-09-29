"""Safe Markdown for the Shop floor thread (MC's replies and Robert's notes).

The stored text stays the original Markdown; this renders it at display time,
on the server, so the page needs no client-side Markdown library and its CSP
stays strict. Two layers:

1. markdown-it-py, CommonMark (linear on pathological input; the #261 review
   measured Python-Markdown at 17.5 s for 2,000 backticks), with raw HTML off
   (``<script>`` is text, not a tag), tables and strikethrough on, linkify
   off, and single newlines kept as line breaks.
2. nh3 (ammonia), an allow-list sanitiser: only the tags below survive; a link
   keeps its href only when it is an absolute http, https or mailto URL
   (relative, protocol-relative and backslash URLs are dropped), and gets
   rel="noopener noreferrer" and target="_blank"; no images, no style, no
   event handlers, no classes.

Rendering is cached per message text, so the floor's polling never re-renders
a note it has already rendered.
"""
from __future__ import annotations

import functools
import re

import nh3
from markdown_it import MarkdownIt

TAGS = {"p", "br", "strong", "em", "s", "del", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
        "code", "pre", "blockquote", "hr", "a", "table", "thead", "tbody", "tr", "th", "td"}
ATTRIBUTES = {"a": {"href", "title"}, "ol": {"start"}, "th": {"style"}, "td": {"style"}}
URL_SCHEMES = {"http", "https", "mailto"}
MAX_CHARS = 16_000          # rendered length; the stored text is never cut (#261 review, finding 1)
_SAFE_HREF = re.compile(r"^(https?://[^\s/\\]|mailto:[^\s])", re.IGNORECASE)
_SAFE_ALIGN = re.compile(r"^text-align:(left|right|center)$")

_ANY_LIST = re.compile(r"^\s*([-*+]|\d{1,3}[.)])\s")
_HEADING = re.compile(r"#{1,6}\s")


def _separate_blocks(text: str) -> str:
    """Models often start a heading, quote, table or code fence right under a
    line of text; give it the blank line CommonMark needs (outside code
    fences), and one after a table ends. Linear in the text."""
    out: list[str] = []
    in_fence = False
    prev = ""
    for line in text.split("\n"):
        stripped = line.lstrip()
        fence = stripped.startswith("```") or stripped.startswith("~~~")
        if not in_fence and prev.strip():
            p = prev.lstrip()
            if ((fence and not (p.startswith("```") or p.startswith("~~~")))
                    or (_ANY_LIST.match(line) and not _ANY_LIST.match(prev) and not prev.startswith(" "))
                    or _HEADING.match(stripped)
                    or (stripped.startswith(">") and not p.startswith(">"))
                    or (stripped.startswith("|") and not p.startswith("|"))
                    or (stripped and not stripped.startswith("|") and p.startswith("|"))):
                out.append("")
        if fence:
            in_fence = not in_fence
        out.append(line)
        prev = line
    return "\n".join(out)


_MD = (MarkdownIt("commonmark", {"html": False, "breaks": True, "linkify": False, "typographer": False})
       .enable("table").enable("strikethrough"))


def _attribute_filter(tag: str, attr: str, value: str):
    if tag == "a" and attr == "href":
        return value if _SAFE_HREF.match(value.strip()) else None
    if tag in ("th", "td") and attr == "style":
        return value if _SAFE_ALIGN.match(value.replace(" ", "")) else None
    return value


@functools.lru_cache(maxsize=2048)
def render_markdown(text: str) -> str:
    """Sanitised HTML for one note's Markdown (never raises; escaped text on error)."""
    source = text or ""
    cut = max(0, len(source) - MAX_CHARS)
    source = source[:MAX_CHARS]
    try:
        html = _MD.render(_separate_blocks(source))
    except Exception:
        html = "<p>" + nh3.clean_text(source) + "</p>"
    if cut:
        html += f"<p><em>… ({cut:,} more characters not shown)</em></p>"
    return nh3.clean(html.strip(), tags=TAGS, attributes=ATTRIBUTES, url_schemes=URL_SCHEMES,
                     link_rel="noopener noreferrer", set_tag_attribute_values={"a": {"target": "_blank"}},
                     attribute_filter=_attribute_filter, strip_comments=True)


def with_html(notes):
    """Add ``html`` (rendered, sanitised) to each note dict; the text is kept."""
    for n in notes or ():
        if isinstance(n, dict) and isinstance(n.get("text"), str):
            n["html"] = render_markdown(n["text"])
    return notes


__all__ = ["render_markdown", "with_html", "MAX_CHARS"]
