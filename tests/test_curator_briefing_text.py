import pytest
from domains.curator.briefing_text import parse_briefing_text


def entry(rank=1):
    return f"#{rank} [Synthetic] 🏷️ fiscal (mechanical)\n ID: abc\n Résumé title\n https://example.invalid/{rank}\n Published: today\n Scores: 7.0/10 (raw: 7)\n snippet\n"


@pytest.mark.parametrize("prefix", ["", "\n", "\n\n", "Daily briefing\n"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_first_article_and_following_articles(prefix, newline):
    rows = parse_briefing_text((prefix + entry(1) + '\n' + entry(2)).replace('\n', newline))
    assert [r['num'] for r in rows] == ['1', '2']
    assert rows[0] == dict(num='1', title='Résumé title', url='https://example.invalid/1', source='Synthetic', category='fiscal', score='7.0')


def test_malformed_short_sections_and_missing_optional_metadata():
    assert parse_briefing_text('#1 too short\n') == []
    assert parse_briefing_text('#1 Header\n Title\n https://example.invalid\n') == [
        dict(num='1', title='Title', url='https://example.invalid', source='Unknown', category='other', score='?')]


def test_compatibility_file_wrapper(tmp_path):
    from core.telegram.telegram_bot import parse_curator_output
    path = tmp_path / 'briefing.txt'; path.write_text(entry())
    assert parse_curator_output(path) == parse_briefing_text(entry())
