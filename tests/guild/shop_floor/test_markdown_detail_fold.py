"""A short answer with its detail folded underneath (Robert, 26 Oct). The fold is written by the renderer at a plain
"Details" line; the model never supplies HTML."""
import pytest

from minimoi_portal.guild_ui.markdown_render import render_markdown


def test_a_details_line_folds_everything_after_it():
    html = render_markdown("**Short answer.**\n\nDetails\n\n- evidence one\n- evidence two")
    head, _, rest = html.partition('<details class="mc-detail">')
    assert "<strong>Short answer.</strong>" in head and "evidence" not in head
    assert rest.startswith("<summary>Details</summary>") and "evidence two" in rest and rest.endswith("</details>")


@pytest.mark.parametrize("marker", ["Details", "**Details**", "Details:", "details"])
def test_the_marker_forms(marker):
    assert "<details" in render_markdown(f"answer\n\n{marker}\n\nmore")


@pytest.mark.parametrize("text", [
    "no fold here",
    "```\nDetails\n```\nafter",                    # inside a code block
    "Details\n\nonly detail, no answer",            # nothing before it: no fold
    "answer\n\nDetails",                            # nothing after it: no fold
    "The details are below.\n\nmore",               # a word in a sentence is not the marker
])
def test_no_fold_without_a_real_answer_and_detail(text):
    assert "<details" not in render_markdown(text)


def test_model_written_html_is_still_escaped():
    html = render_markdown('answer\n\n<details open><summary onclick="x()">x</summary>y</details>')
    assert "<details" not in html and "<summary" not in html and "&lt;details" in html     # shown as text, not markup
