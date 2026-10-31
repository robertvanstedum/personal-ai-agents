"""Offline contracts for the feed-to-score boundary and real scorer adapters."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from domains.curator import scoring_prompts, scoring_responses, scoring_rules
from domains.curator.feed_records import normalize_feed_entries

NOW = datetime(2026, 1, 10, tzinfo=timezone.utc)


def entries():
    return [dict(title=f"Article {i}", summary="synthetic", source="Fixture", published=None,
                 link=f"https://example.invalid/{i}") for i in range(3)]


@pytest.mark.parametrize("builder", ["haiku_prompt", "prefilter_prompt", "sonnet_prompt", "xai_prompt"])
@pytest.mark.parametrize("case", ["empty", "articles"])
def test_outbound_prompts_match_pre_refactor_bytes(builder, case):
    frozen = json.loads((Path(__file__).parent / 'fixtures/curator_scoring_prompts.json').read_text())
    batch = [] if case == 'empty' else frozen['entries']
    actual = getattr(scoring_prompts, builder)(batch, frozen['profile'])
    assert hashlib.sha256(actual.encode()).hexdigest() == frozen['sha256'][builder][case]


@pytest.mark.parametrize('value', ['NaN', 'inf', '-inf', '-0.1', '10.1', 'nonsense'])
def test_invalid_scores_are_missing_not_ranking_values(value):
    assert scoring_responses.category_scores(f'0|fiscal|{value}', 1) == {}
    assert scoring_responses.ranking_scores(f'0|{value}', 1) == {}


@pytest.mark.parametrize('text', [
    '0|fiscal|3\n0|fiscal|8', '0|fiscal|3\n0|fiscal|8\n0|fiscal|9',
    '0|unknown|3\n0|fiscal|8', '0|fiscal|nan\n0|fiscal|8',
    '0|fiscal|3|extra\n0|fiscal|8', '0|invalid\n0|fiscal|8',
])
def test_duplicate_claims_permanently_invalidate_the_index(text):
    assert scoring_responses.category_scores(text, 1) == {}


def test_indices_are_bounded_and_rows_do_not_acquire_positional_identity():
    result = scoring_responses.category_scores('2|fiscal|10\n-1|fiscal|9\n99|fiscal|9\n0|monetary|0\n1.0|fiscal|8\nnonsense', 3)
    assert result == {2: {'category': 'fiscal', 'score': 10.0}, 0: {'category': 'monetary', 'score': 0.0}}
    assert scoring_responses.category_scores('0|FISCAL|8', 1) == {}
    assert scoring_responses.category_scores('0|FISCAL|8', 1, lowercase=True)[0]['category'] == 'fiscal'
    assert scoring_responses.ranking_scores('2|8\n0|3', 3) == {2: 8.0, 0: 3.0}
    assert scoring_responses.ranking_scores('0|3\n0|bad\n0|4', 1) == {}
    assert scoring_responses.category_scores('0|fiscal|8', 0) == {}


@pytest.fixture
def runtime(monkeypatch):
    from domains.curator import curator_rss_v2 as rss
    monkeypatch.setattr(rss, 'get_xai_api_key', lambda: 'fixture')
    monkeypatch.setattr(rss, 'get_anthropic_api_key', lambda: 'fixture')
    monkeypatch.setattr(rss, 'log_curator_cost', lambda *args: None)
    monkeypatch.setattr(rss, 'log_error', lambda *args, **kwargs: None)
    monkeypatch.setattr(rss, '_is_production', lambda: False)
    return rss


def fake_provider(monkeypatch, text, error=None):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if error:
            raise error
        return SimpleNamespace(content=[SimpleNamespace(text=text)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, prompt_tokens=10, completion_tokens=5),
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))], model='fixture-effective-model')
    client = SimpleNamespace(messages=SimpleNamespace(create=create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setitem(sys.modules, 'anthropic', SimpleNamespace(Anthropic=lambda **kwargs: client))
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=lambda **kwargs: client))
    return calls


def test_xai_shuffled_sparse_rows_keep_input_identity(runtime, monkeypatch):
    batch = entries()
    calls = fake_provider(monkeypatch, '2|FISCAL|9\n0|monetary|2')
    result = runtime.score_entries_xai(batch)
    assert [r['score'] for r in result] == [2, 5, 9]
    assert [r['category'] for r in result] == ['monetary', 'other', 'fiscal']
    assert all(r['raw_score'] == r['score'] and r['method'] == 'xai' for r in result)
    assert len(calls) == 1 and calls[0]['model'] == runtime.XAI_RANKING_MODEL
    assert calls[0]['temperature'] == 0 and calls[0]['max_tokens'] == 4096
    assert all('score' not in row for row in batch)


def test_xai_invalid_rows_do_not_shift_valid_articles(runtime, monkeypatch):
    calls = fake_provider(monkeypatch, '2|fiscal|9\n0|fiscal|nan\n0|fiscal|7\n1|other|3|extra')
    assert [r['score'] for r in runtime.score_entries_xai(entries())] == [5, 5, 9]
    assert len(calls) == 1


def test_haiku_missing_score_uses_its_mechanical_fallback(runtime, monkeypatch):
    fake_provider(monkeypatch, '2|fiscal|8\n0|fiscal|nan')
    fallback = []
    def mechanical(entry):
        fallback.append(entry['title'])
        return {'category': 'other', 'score': 1, 'method': 'mechanical'}
    monkeypatch.setattr(runtime, 'score_entry_mechanical', mechanical)
    result = runtime.score_entries_haiku(entries())
    assert fallback == ['Article 0', 'Article 1']
    assert [r['method'] for r in result] == ['mechanical', 'mechanical', 'haiku']


def test_prefilter_retains_mutation_sorting_and_tie_order(runtime, monkeypatch):
    fake_provider(monkeypatch, '2|fiscal|8\n0|fiscal|8\n1|invalid|9')
    batch = entries(); originals = list(batch)
    selected = runtime.score_entries_haiku_prefilter(batch, top_n=2)
    assert batch == [originals[0], originals[2], originals[1]]
    assert selected[0] is originals[0] and selected[1] is originals[2]
    assert originals[1]['score'] == 0 and originals[1]['category'] == 'other'
    assert all(x['method'] == 'haiku-prefilter' for x in batch)


def test_sonnet_keeps_prior_scores_for_invalid_or_missing_rows(runtime, monkeypatch):
    fake_provider(monkeypatch, '2|9\n0|inf\n1|4\n1|6')
    batch = [dict(e, score=2, method='haiku-prefilter') for e in entries()]
    result = runtime.score_entries_sonnet_ranking(batch)
    assert result is batch
    assert [e['score'] for e in batch] == [2, 2, 9]
    assert [e['method'] for e in batch] == ['haiku-prefilter', 'haiku-prefilter', 'sonnet-ranking']


@pytest.mark.parametrize('method', ['score_entries_xai', 'score_entries_haiku', 'score_entries_haiku_prefilter', 'score_entries_sonnet_ranking'])
def test_transport_failure_never_retries_and_preserves_opt_in_fallback(runtime, monkeypatch, method):
    calls = fake_provider(monkeypatch, '', error=RuntimeError('fixture connection failed'))
    batch = entries()
    with pytest.raises(RuntimeError):
        getattr(runtime, method)(batch)
    assert len(calls) == 1
    result = getattr(runtime, method)(batch, fallback_on_error=True)
    assert len(calls) == 2
    if method in ['score_entries_haiku_prefilter', 'score_entries_sonnet_ranking']:
        assert result is batch
    else:
        assert all(r['method'] == 'mechanical' for r in result)


@pytest.mark.parametrize('days,fast,slow', [(0,1,1),(3,1,1),(7,.85,1),(14,.65,1),(30,.4,1),(60,.05,.85),(90,.05,.65),(100,.05,.4),(-1,1,1)])
def test_source_age_thresholds_preserved(days, fast, slow):
    row = {'published': NOW-timedelta(days=days), 'link': 'https://example.invalid'}
    assert scoring_rules.compute_age_multiplier(row, now=NOW) == fast
    row['link'] = 'https://crisisgroup.org/report'
    assert scoring_rules.compute_age_multiplier(row, now=NOW) == slow


def test_mechanical_policy_and_runtime_hooks(runtime, monkeypatch):
    row = dict(entries()[0], title='gold debt', source='Treasury MSPD')
    score = scoring_rules.score_entry_mechanical(row, now=NOW)
    assert score['raw_score'] == 78 and score['score'] == pytest.approx(3.9)
    monkeypatch.setattr(runtime, 'normalize_score', lambda value: 7)
    monkeypatch.setattr(runtime, 'assign_category', lambda entry: 'fiscal')
    assert runtime.score_entry_mechanical(row)['score'] == 7
    assert runtime.score_entry_mechanical(row)['category'] == 'fiscal'


class FeedEntry(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error


def test_feed_caps_dates_defaults_hash_and_original_identity():
    original = [FeedEntry(link=f'https://example.invalid/{i}') for i in range(55)]
    original[0].update(published_parsed=(2026,1,1,2,3,4), updated_parsed=(2026,1,2,2,3,4))
    original[1].update(updated_parsed=(2026,1,2,2,3,4))
    result = normalize_feed_entries('Fixture', original)
    assert len(result) == 50 and len(normalize_feed_entries('arXiv q-fin', original)) == 15
    assert result[0]['raw_entry'] is original[0]
    assert result[0]['published'] == datetime(2026,1,1,2,3,4,tzinfo=timezone.utc)
    assert result[1]['published'].day == 2
    assert result[0]['hash_id'] == hashlib.md5(original[0]['link'].encode()).hexdigest()[:5]
    assert normalize_feed_entries('Fixture', [FeedEntry()])[0]['hash_id'] is None
    assert result[0]['title'] == 'No title'


def test_feed_normalization_failure_still_refuses_whole_feed(runtime, monkeypatch):
    raw = [FeedEntry(title='ok'), FeedEntry(published_parsed=(2026,99,1,0,0,0))]
    monkeypatch.setattr(runtime.requests, 'get', lambda *a, **k: SimpleNamespace(content=b'fixture', raise_for_status=lambda: None))
    monkeypatch.setattr(runtime.feedparser, 'parse', lambda b: SimpleNamespace(entries=raw))
    assert runtime.fetch_feed('Fixture', 'https://example.invalid/feed') == []


@pytest.mark.parametrize('method', ['score_entries_xai', 'score_entries_haiku', 'score_entries_haiku_prefilter', 'score_entries_sonnet_ranking'])
def test_empty_reply_retains_stage_specific_defaults(runtime, monkeypatch, method):
    calls = fake_provider(monkeypatch, '')
    batch = [dict(e, score=3, method='prior') for e in entries()]
    result = getattr(runtime, method)(batch)
    expected = {'score_entries_xai': 5, 'score_entries_haiku_prefilter': 0, 'score_entries_sonnet_ranking': 3}
    if method in expected:
        assert [r['score'] for r in result] == [expected[method]] * 3
    else:
        assert all(r['method'] == 'mechanical' for r in result)
    assert len(calls) == 1


def test_feed_scores_briefing_round_trip_does_not_mislabel_articles(runtime, monkeypatch):
    from domains.curator.briefing_text import parse_briefing_text
    raw = [FeedEntry(title=f'Fixture {i}', link=f'https://example.invalid/{i}') for i in range(3)]
    batch = normalize_feed_entries('Fixture feed', raw)
    fake_provider(monkeypatch, '2|technology|9\n0|fiscal|1\n1|monetary|6')
    scores = runtime.score_entries_xai(batch)
    for article, score in zip(batch, scores):
        article.update(score)
    batch.sort(key=lambda article: article['score'], reverse=True)
    parsed = parse_briefing_text(runtime.format_output(batch))
    assert [(a['title'], a['url'], a['category'], a['score']) for a in parsed] == [
        ('Fixture 2', 'https://example.invalid/2', 'technology', '9.0'),
        ('Fixture 1', 'https://example.invalid/1', 'monetary', '6.0'),
        ('Fixture 0', 'https://example.invalid/0', 'fiscal', '1.0'),
    ]


def test_archive_index_compatibility_and_untrusted_metadata(runtime, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    archive = tmp_path / 'archive'
    archive.mkdir()
    (archive/'curator_2026-01-01.html').write_text('<meta name="curator-model" content="mechanical">')
    (archive/'curator_2026-01-02-1200.html').write_text(
        '<meta name="curator-model" content="<script>bad()</script>">'
        '<meta name="curator-articles" content="<img src=x onerror=bad()>">')
    (archive/'curator_invalid.html').write_text('invalid filename ignored')
    runtime.generate_index_page('archive')
    html = (tmp_path/'curator_index.html').read_text()
    assert html.index('Jan 02, 2026') < html.index('Jan 01, 2026')
    assert 'mechanical (keyword)' in html and 'curator_invalid' not in html
    assert '<script>bad()' not in html and '<img src=x' not in html
    assert '&lt;script&gt;bad()&lt;/script&gt;' in html
    assert 'href="archive/curator_2026-01-02-1200.html"' in html


@pytest.mark.parametrize('days,accepted', [(30, True), (31, False), (60, False), (61, False)])
def test_preference_profile_preserves_decay_thresholds(days, accepted):
    from domains.curator.preference_profile import format_user_profile
    prefs = {'learned_patterns': {'sample_size': 3, 'last_updated': (NOW-timedelta(days=days)).isoformat(),
        'preferred_themes': {'borderline': 2}, 'preferred_content_types': {'descriptive': 9}}}
    result = format_user_profile(prefs, active_domain='Fixture', now=NOW)
    assert ('borderline' in result) is accepted
    assert 'descriptive' not in result


def test_preference_profile_domain_boundary_and_read_only_behavior(runtime, tmp_path, monkeypatch):
    from domains.curator.preference_profile import format_user_profile
    prefs = {'learned_patterns': {'sample_size': 3, 'preferred_sources': {'A': 3, 'B': -3},
        'domain_signals': {'Fixture': {'wanted.invalid': 4}, 'Other': {'private.invalid': 9}},
        'content_domains': {'liked.invalid': {'like': 2}, 'avoided.invalid': {'dislike': 2}}}}
    original = json.dumps(prefs)
    result = format_user_profile(prefs, active_domain='Fixture', now=NOW)
    assert 'wanted.invalid(+4)' in result and 'private.invalid' not in result
    assert 'Preferred sources: A' in result and 'Penalize sources: B' in result
    assert 'liked.invalid(+4)' in result and 'Penalize content domains: avoided.invalid' in result
    assert json.dumps(prefs) == original
    monkeypatch.setattr(runtime, '_DATA_DIR', tmp_path)
    assert runtime.load_user_profile() == ''
    path = tmp_path/'curator_preferences.json'; path.write_text(original)
    monkeypatch.setattr(runtime, 'ACTIVE_DOMAIN', 'Fixture')
    assert runtime.load_user_profile() == result
    assert path.read_text() == original


@pytest.mark.parametrize('method', ['score_entries_xai', 'score_entries_haiku', 'score_entries_haiku_prefilter', 'score_entries_sonnet_ranking'])
def test_empty_batch_does_not_accept_unsolicited_scores(runtime, monkeypatch, method):
    calls = fake_provider(monkeypatch, '0|fiscal|9\n0|9')
    assert getattr(runtime, method)([]) == []
    # Preserve current request behavior rather than adding a new transport policy.
    assert len(calls) == 1


@pytest.mark.parametrize('method,reply', [
    ('score_entries_haiku_prefilter', '2|fiscal|9\n0|fiscal|3\n1|fiscal|5'),
    ('score_entries_sonnet_ranking', '2|9\n0|3\n1|5'),
])
def test_late_cost_failure_retains_existing_mutation_without_another_call(runtime, monkeypatch, method, reply):
    calls = fake_provider(monkeypatch, reply)
    batch = [dict(e, score=1, method='prior') for e in entries()]
    def unavailable_log(*args):
        raise RuntimeError('fixture cost store unavailable')
    monkeypatch.setattr(runtime, 'log_curator_cost', unavailable_log)
    result = getattr(runtime, method)(batch, fallback_on_error=True)
    assert result is batch and len(calls) == 1
    # This is an inherited adapter boundary: fallback does not undo mutations.
    assert {e['title']: e['score'] for e in result} == {'Article 0': 3, 'Article 1': 5, 'Article 2': 9}
