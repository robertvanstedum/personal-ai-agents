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


def _numbered(rule_name: str):
    default = _MD.renderer.rules[rule_name]

    def rule(tokens, idx, options, env):
        out = default(tokens, idx, options, env)
        blk = tokens[idx].attrGet("data-block")
        return out.replace("<pre", f'<pre data-block="{int(blk)}"', 1) if blk is not None else out
    _MD.renderer.rules[rule_name] = rule


for _name in ("fence", "code_block"):          # a code block keeps its number too (markdown-it drops token attrs on these)
    _numbered(_name)

_BLOCK_OPEN = {"paragraph_open", "heading_open", "bullet_list_open", "ordered_list_open", "blockquote_open", "table_open", "hr"}
_BLOCK_ATTRS = {t: {"data-block"} for t in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote", "table", "hr", "pre")}


def render_blocks(text: str) -> tuple[str, int]:
    """(sanitised HTML, number of top-level blocks) for a topic document: each top-level block carries ``data-block="<n>"`` so a
    comment can point at it ("paragraph 3"). The same sanitiser and allow-list as every note; the only attribute added is
    data-block, with a number the server wrote. Not cached (documents are edited as new revisions), not cut at MAX_CHARS."""
    source = text or ""
    try:
        tokens = _MD.parse(_separate_blocks(source), {})
    except Exception:
        return "<p data-block=\"0\">" + nh3.clean_text(source) + "</p>", 1
    n = 0
    for tok in tokens:
        if tok.level == 0 and (tok.type in _BLOCK_OPEN or tok.type in ("fence", "code_block")):
            tok.attrSet("data-block", str(n))
            n += 1
    html = _MD.renderer.render(tokens, _MD.options, {})
    attrs = {**ATTRIBUTES, **{k: ATTRIBUTES.get(k, set()) | v for k, v in _BLOCK_ATTRS.items()}}
    clean = nh3.clean(html.strip(), tags=TAGS, attributes=attrs, url_schemes=URL_SCHEMES, link_rel="noopener noreferrer",
                      set_tag_attribute_values={"a": {"target": "_blank"}}, attribute_filter=_attribute_filter, strip_comments=True)
    return clean, n


__all__ = ["render_markdown", "render_blocks", "with_html", "MAX_CHARS"]


def render_document(text: str, resolve_link=None) -> str:
    """Render a bounded repository document in full, using the note sanitizer."""
    source = text or ""
    def document_attribute(tag, attr, value):
        if tag == "a" and attr == "href" and not _SAFE_HREF.match(value):
            return resolve_link(value) if resolve_link else None
        return _attribute_filter(tag, attr, value)
    html = _MD.render(source)
    return nh3.clean(html, tags=TAGS, attributes=ATTRIBUTES, url_schemes=URL_SCHEMES,
                     link_rel="noopener noreferrer", set_tag_attribute_values={"a": {"target": "_blank"}},
                     attribute_filter=document_attribute, strip_comments=True)
