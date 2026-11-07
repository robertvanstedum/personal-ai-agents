"""A model's own <details> tags fold like a Details section; they never show as text (Robert's phone, 7 Nov)."""
from minimoi_portal.guild_ui.markdown_render import render_markdown


def _folded(html):
    assert '<details class="mc-detail"><summary>Details</summary>' in html
    assert "&lt;" not in html


def test_model_details_tags_become_the_fold():
    html = render_markdown("The short answer.\n\n<details>\n<summary>More</summary>\n\nThe long part.\n\n</details>")
    _folded(html)
    assert "The short answer." in html and "The long part." in html


def test_summary_on_the_opening_line():
    _folded(render_markdown("Short.\n<details><summary>Why</summary>\nLong.\n</details>"))


def test_tags_in_a_code_block_stay_as_code():
    html = render_markdown("Example:\n\n```\n<details>\nx\n</details>\n```")
    assert "<details" not in html and "&lt;details&gt;" in html


def test_an_unclosed_tag_changes_nothing():
    html = render_markdown("Short.\n<details>\nLong.")
    assert "<details" not in html and "&lt;details&gt;" in html


def test_a_model_cannot_inject_attributes_or_script():
    html = render_markdown('Short.\n<details open onclick="x()">\n<script>alert(1)</script>\n</details>')
    assert "onclick" not in html and "<script" not in html


def test_a_plain_details_line_still_folds():
    _folded(render_markdown("Short.\n\nDetails\n\nLong."))
