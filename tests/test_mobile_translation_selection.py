from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
READING_TEMPLATES = (
    ROOT / "domains" / "portuguese" / "templates" / "portuguese_leitura.html",
    ROOT / "domains" / "german" / "templates" / "german_lesen.html",
)


@pytest.mark.parametrize("template_path", READING_TEMPLATES)
def test_reading_translation_supports_mobile_text_selection(template_path):
    template = template_path.read_text()

    assert "function selectedReadingRange()" in template
    assert "function scheduleReadingSelection(" in template
    assert "document.addEventListener('mouseup'" in template
    assert "document.addEventListener('touchend'" in template
    assert "document.addEventListener('selectionchange'" in template
    assert "readingTextEl.contains(range.commonAncestorContainer)" in template
    assert "activeSelection.removeAllRanges()" in template
