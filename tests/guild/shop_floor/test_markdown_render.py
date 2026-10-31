"""Markdown in the Shop floor thread (Robert, September 29: "comparable
formatting" to OpenClaw, Codex and Grok): rendered on the server, sanitised
with an allow-list, the stored text unchanged."""
from __future__ import annotations

import re
import statistics
import time

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


def _assert_render_cpu_budget(renderer, cases, *, clock=time.process_time, samples=3, budget=0.5):
    """Bound CPU work, not scheduler delay; repeated cold samples reduce noise.

    This is an absolute-work guard, not a proof of asymptotic linearity. It
    retains the original half-second budget and catches the historical
    multi-second Python-Markdown CPU regression without charging descheduling.
    """
    measurements = {}
    for name, text in cases.items():
        values = []
        for _ in range(samples):
            start = clock()
            renderer(text)
            values.append(clock() - start)
        measurements[name] = values
    slow = {name: values for name, values in measurements.items() if statistics.median(values) > budget}
    assert not slow, slow
    return measurements


def test_render_cpu_work_is_bounded_on_pathological_input_at_the_maximum_size():
    from minimoi_portal.guild_ui.markdown_render import MAX_CHARS
    n = MAX_CHARS
    cases = {
        "backticks": "`" * n, "open brackets": "[" * n, "tick a": "`a " * (n // 3), "link opens": "[a](" * (n // 4),
        "brackets then closes": "[" * (n // 8) + "](x)" * (n // 8), "stars": "*" * n, "underscore a": "_a" * (n // 2),
        "quotes": "> " * (n // 2), "list markers": "1. " * (n // 3), "image opens": "![x](" * (n // 5),
        "bold a": "**a" * (n // 3), "table": "|a" * (n // 2) + "\n" + "|-" * (n // 2), "mixed": "[`*_" * (n // 4),
        "angle a": "<a" * (n // 2), "entities": "&#" * (n // 2),
    }
    def cold_render(text):
        render_markdown.cache_clear()
        return render_markdown(text + " ")
    _assert_render_cpu_budget(cold_render, cases)


def test_cpu_guard_rejects_consistently_expensive_work():
    ticks = iter([0.0, 0.6, 1.0, 1.6, 2.0, 2.6])
    with pytest.raises(AssertionError, match="slow"):
        _assert_render_cpu_budget(lambda text: None, {"slow": "synthetic"}, clock=lambda: next(ticks))


def test_cpu_guard_takes_independent_samples_not_cached_success():
    calls = []
    ticks = iter([0.0, 0.1, 1.0, 1.1, 2.0, 2.1])
    measured = _assert_render_cpu_budget(calls.append, {"input": "synthetic"}, clock=lambda: next(ticks))
    assert calls == ["synthetic"] * 3
    assert measured["input"] == pytest.approx([0.1, 0.1, 0.1])


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


@pytest.mark.parametrize("note_id", ["1", "90210", "9223372036854775807"])
def test_board_receipt_links_keep_the_exact_read_only_destination(note_id):
    href = f"/guild-next/guild/board?note={note_id}"
    html = render_markdown(f"Gespeichert. [Notiz]({href})")
    assert f'href="{href}"' in html
    assert 'rel="noopener noreferrer"' in html


@pytest.mark.parametrize("href", [
    "/guild-next/guild/board?note=0", "/guild-next/guild/board?note=-1",
    "/guild-next/guild/board?note=1&delete=1", "/guild-next/guild/board?note=1#evil",
    "/guild-next/guild/board?note=1%0A", "/guild-next/guild/board?note=12345678901234567890",
    "//guild-next/guild/board?note=1", "/guild-next/guild/../admin?note=1",
    "/guild-next/guild/board?note=1/../../logout", "/logout",
    "/guild-next/api/v1/board/operations/1", "/guild-next/guild/board?note=1%26next=https://evil.test",
])
def test_board_link_exception_does_not_admit_other_relative_actions(href):
    assert 'href=' not in render_markdown(f"[note]({href})")
