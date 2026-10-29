from pathlib import Path
import pytest
from domains.curator import briefing_render


@pytest.mark.parametrize('model', ['mechanical', 'ollama'])
def test_keyword_scoring_is_not_presented_as_an_ollama_model(model):
    from domains.curator.curator_rss_v2 import resolve_scoring_mode
    assert resolve_scoring_mode(model) == 'mechanical'
    html = briefing_render.format_html([], model=model, xai_ranking_model='synthetic')
    assert '<meta name="curator-model" content="mechanical (keyword)">' in html
    assert 'ollama/phi' not in html


def test_actual_provider_labels_are_preserved():
    html = briefing_render.format_html([], model='ollama_chat/llama3.2:3b', xai_ranking_model='synthetic')
    assert 'content="ollama_chat/llama3.2:3b"' in html


def test_archive_labels_both_date_formats_without_rewriting_history(tmp_path, monkeypatch):
    from domains.curator.curator_rss_v2 import generate_index_page
    monkeypatch.chdir(tmp_path)
    archive = tmp_path / 'archive'; archive.mkdir()
    payload = '<meta name="curator-model" content="mechanical">'
    for name in ['curator_2026-10-28.html', 'curator_2026-10-29-0900.html']:
        (archive / name).write_text(payload)
    generate_index_page(str(archive))
    assert (tmp_path / 'curator_index.html').read_text().count('mechanical (keyword)') == 2
    assert all(p.read_text() == payload for p in archive.iterdir())
