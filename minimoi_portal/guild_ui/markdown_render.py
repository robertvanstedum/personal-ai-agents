"""Safe Markdown for the Shop floor thread (MC's replies and Robert's notes).

The stored text stays the original Markdown; this renders it at display time,
on the server, so the page needs no client-side Markdown library and its CSP
stays strict. Two layers:

1. Python-Markdown with raw HTML switched off (the html_block preprocessor and
   the inline html pattern are removed, so ``<script>`` is text, not a tag).
2. nh3 (ammonia), an allow-list sanitiser: only the tags below survive; links
   keep only http(s)/mailto URLs and get rel="noopener noreferrer" and
   target="_blank"; no images, no style, no event handlers, no classes beyond
   the code block's language.
"""
from __future__ import annotations

import functools
import re

import markdown
import nh3

TAGS = {"p", "br", "strong", "em", "b", "i", "del", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
        "code", "pre", "blockquote", "hr", "a", "table", "thead", "tbody", "tr", "th", "td"}
ATTRIBUTES = {"a": {"href", "title"}, "ol": {"start"}, "th": {"align"}, "td": {"align"}}
URL_SCHEMES = {"http", "https", "mailto"}
MAX_CHARS = 40_000

_LIST_LINE = re.compile(r"^( +)([-*+]|\d{1,3}[.)])\s")


def _normalize_list_indent(text: str) -> str:
    """Models nest lists with 2 or 3 spaces; Python-Markdown needs 4. Map each
    list line's indent to 4 spaces per level, using the smallest indent seen."""
    lines = text.split("\n")
    indents = [len(m.group(1)) for m in map(_LIST_LINE.match, lines) if m]
    if not indents:
        return text
    unit = min(indents)
    if unit >= 4:
        return text
    out = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _LIST_LINE.match(line)
        if m:
            level = max(1, round(len(m.group(1)) / unit))
            line = " " * (4 * level) + line[len(m.group(1)):]
        out.append(line)
    return "\n".join(out)


_ANY_LIST = re.compile(r"^\s*([-*+]|\d{1,3}[.)])\s")


def _separate_blocks(text: str) -> str:
    """Models often start a list, heading, quote, table or code fence right
    under a line of text; Python-Markdown needs a blank line first. Add one
    (outside code fences), and one after a table ends."""
    out: list[str] = []
    in_fence = False
    prev = ""
    for line in text.split("\n"):
        stripped = line.lstrip()
        fence = stripped.startswith("```")
        if not in_fence and prev.strip():
            p = prev.lstrip()
            starts = (
                (fence and not p.startswith("```"))
                or (_ANY_LIST.match(line) and not _ANY_LIST.match(prev) and not prev.startswith(" "))
                or (stripped.startswith("#") and re.match(r"#{1,6}\s", stripped))
                or (stripped.startswith(">") and not p.startswith(">"))
                or (stripped.startswith("|") and not p.startswith("|"))
                or (stripped and not stripped.startswith("|") and p.startswith("|"))
            )
            if starts:
                out.append("")
        if fence:
            in_fence = not in_fence
        out.append(line)
        prev = line
    return "\n".join(out)


def _md() -> markdown.Markdown:
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "nl2br"], output_format="html")
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    return md


@functools.lru_cache(maxsize=512)
def render_markdown(text: str) -> str:
    """Sanitised HTML for one note's Markdown (never raises; plain escaped text on error)."""
    source = (text or "")[:MAX_CHARS]
    try:
        html = _md().convert(_normalize_list_indent(_separate_blocks(source)))
    except Exception:
        html = "<p>" + nh3.clean_text(source) + "</p>"
    return nh3.clean(html, tags=TAGS, attributes=ATTRIBUTES, url_schemes=URL_SCHEMES,
                     link_rel="noopener noreferrer", set_tag_attribute_values={"a": {"target": "_blank"}},
                     strip_comments=True)


def with_html(notes):
    """Add ``html`` (rendered, sanitised) to each note dict; the text is kept."""
    for n in notes or ():
        if isinstance(n, dict) and isinstance(n.get("text"), str):
            n["html"] = render_markdown(n["text"])
    return notes


__all__ = ["render_markdown", "with_html"]
