"""Markdown in the Shop floor thread (Robert, September 29: "comparable
formatting" to OpenClaw, Codex and Grok): rendered on the server, sanitised
with an allow-list, the stored text unchanged."""
from __future__ import annotations

import re

import pytest

from minimoi_portal.guild_ui.markdown_render import render_markdown, with_html

MC_REPLY = """Here is where things stand:
## Build queue
**Three items** need you, and *one* is blocked.
1. **#12** waits on review
   - the relay change
   - the portal change
2. **#14** is in build
### Next
- check `verify.sh`
- run the CoS question

```bash
scripts/staging/verify.sh
```
| Item | Stage |
|---|---|
| 12 | review |
> Nothing was changed.
See [the runbook](https://github.com/robertvanstedum/personal-ai-agents)."""


def test_a_model_style_reply_renders_as_structure_not_asterisks():
    html = render_markdown(MC_REPLY)
    assert "**" not in html and "<strong>Three items</strong>" in html and "<em>one</em>" in html
    assert "<h2>Build queue</h2>" in html and "<h3>Next</h3>" in html
    assert re.search(r"<ol>\s*<li><strong>#12</strong> waits on review\s*<ul>\s*<li>the relay change</li>", html)
    assert "<li>check <code>verify.sh</code></li>" in html
    assert "<pre><code>scripts/staging/verify.sh\n</code></pre>" in html
    assert "<table>" in html and "<th>Item</th>" in html and "<td>review</td>" in html
    assert "<blockquote>" in html
    assert '<a href="https://github.com/robertvanstedum/personal-ai-agents" target="_blank" rel="noopener noreferrer">the runbook</a>' in html


def test_line_breaks_and_paragraphs():
    html = render_markdown("line one\nline two\n\nsecond paragraph")
    assert html == "<p>line one<br>\nline two</p>\n<p>second paragraph</p>"


@pytest.mark.parametrize("hostile", [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "<a href=\"javascript:alert(1)\">x</a>",
    "[click](javascript:alert(1))",
    "[click](JaVaScRiPt:alert(1))",
    "[click](data:text/html;base64,PHNjcmlwdD4=)",
    "[click](vbscript:msgbox(1))",
    "![pixel](https://tracker.example/p.png)",
    "<iframe src=https://evil.example></iframe>",
    "<div style=\"position:fixed\" onclick=\"x()\">hi</div>",
    "<svg><script>alert(1)</script></svg>",
    "<style>body{display:none}</style>",
    "**bold <b onmouseover=alert(1)>x</b>**",
    "[x](https://ok.example \"t\" onclick=alert(1))",
    "<!-- comment --><form action=https://evil.example><input name=p></form>",
    "```\n</code></pre><script>alert(1)</script>\n```",
])
def test_hostile_markdown_and_html_never_survive(hostile):
    """Parsed as a browser would: only allow-listed tags and attributes, and
    only http(s)/mailto links. Hostile text may remain, escaped, as text."""
    from html.parser import HTMLParser
    from minimoi_portal.guild_ui.markdown_render import ATTRIBUTES, TAGS
    html = render_markdown(hostile)
    seen = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            seen.append((tag, dict(attrs)))

        def handle_comment(self, data):
            seen.append(("!--", {}))
    P().feed(html)
    for tag, attrs in seen:
        assert tag in TAGS, (hostile, html)
        allowed = ATTRIBUTES.get(tag, set()) | ({"target", "rel"} if tag == "a" else set())
        assert set(attrs) <= allowed, (hostile, html)
        if "href" in attrs:
            assert attrs["href"].lower().startswith(("http://", "https://", "mailto:")), (hostile, html)
    assert "<script" not in html.lower() and "<img" not in html.lower()


@pytest.mark.parametrize("href", ["/guild-next/api/v1/notes", "//evil.example/p", "\\javascript:alert(1)",
                                  "relative/path", "#frag", "HTTPS:/missing-slash", "http:\\\\evil.example",
                                  "java\tscript:alert(1)", " javascript:alert(1)"])
def test_only_absolute_http_https_or_mailto_links_keep_their_href(href):
    """#261 review finding 2: nh3's url_schemes lets relative, protocol-relative
    and backslash URLs through; the attribute filter drops them."""
    html = render_markdown(f"[x]({href})")
    assert "href=" not in html, html
    assert "x" in html


def test_the_render_is_linear_on_pathological_input_at_the_maximum_size():
    """#261 review finding 1: Python-Markdown took 17.5 s on 2,000 backticks.
    Every input here, at the maximum rendered size, must stay well under a
    second (target about 200 ms on the workshop Mac)."""
    import time
    from minimoi_portal.guild_ui.markdown_render import MAX_CHARS
    n = MAX_CHARS
    cases = {
        "backticks": "`" * n, "open brackets": "[" * n, "tick a": "`a " * (n // 3), "link opens": "[a](" * (n // 4),
        "brackets then closes": "[" * (n // 8) + "](x)" * (n // 8), "stars": "*" * n, "underscore a": "_a" * (n // 2),
        "quotes": "> " * (n // 2), "list markers": "1. " * (n // 3), "image opens": "![x](" * (n // 5),
        "bold a": "**a" * (n // 3), "table": "|a" * (n // 2) + "\n" + "|-" * (n // 2), "mixed": "[`*_" * (n // 4),
        "angle a": "<a" * (n // 2), "entities": "&#" * (n // 2),
    }
    slow = {}
    for name, text in cases.items():
        render_markdown.cache_clear()
        t0 = time.perf_counter()
        render_markdown(text + " ")
        took = time.perf_counter() - t0
        if took > 0.5:
            slow[name] = round(took, 3)
    assert not slow, slow


def test_a_long_reply_says_what_was_not_shown():
    from minimoi_portal.guild_ui.markdown_render import MAX_CHARS
    html = render_markdown("a" * (MAX_CHARS + 1234))
    assert "1,234 more characters not shown" in html


def test_rendering_is_cached_per_message():
    render_markdown.cache_clear()
    render_markdown("**cached**")
    render_markdown("**cached**")
    assert render_markdown.cache_info().hits >= 1


def test_links_are_http_https_or_mailto_and_open_safely():
    html = render_markdown("[a](https://a.example) [b](http://b.example) [c](mailto:x@y.example) [d](ftp://d.example)")
    assert html.count('rel="noopener noreferrer"') == 4 and html.count('target="_blank"') == 4
    assert 'href="ftp://' not in html and 'href="mailto:x@y.example"' in html


def test_the_text_is_kept_and_html_added():
    notes = [{"text": "**hi**"}, {"text": None}, "not a note"]
    with_html(notes)
    assert notes[0] == {"text": "**hi**", "html": "<p><strong>hi</strong></p>"} and "html" not in notes[1]


def test_very_long_input_is_bounded_and_never_raises():
    html = render_markdown("x" * 100_000)
    assert len(html) < 20_000
