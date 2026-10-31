"""Characterization of the extracted feedback contracts, without live services."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
import pytest
from domains.curator.feedback_articles import parse_briefing
from domains.curator.feedback_learning import deterministic_metadata, update_patterns
from domains.curator.feedback_prompts import metadata_prompt

FIXTURE = Path(__file__).parent / 'fixtures/research/batch4_parent_outputs.json'


def test_prompt_bytes_match_parent():
    for case in json.loads(FIXTURE.read_text())['prompt_cases']:
        assert hashlib.sha256(metadata_prompt(**case['args']).encode()).hexdigest() == case['sha256']


def test_briefing_duplicate_rank_and_reset_contract():
    text = '#1 [Source] 🏷️ fiscal\nOld title\nhttps://old.example\nScores: 4\nignored\n#1 [New] 🏷️ monetary\nID: abc12\nNew café title\nhttps://new.example\nScores: 8\nhttps://ignored.example\n'
    assert parse_briefing(text) == {1: {'rank': 1, 'source': 'New', 'category': 'monetary',
        'title': 'New café title', 'url': 'https://new.example', 'scores': 'Scores: 8', 'hash_id': 'abc12'}}


def test_briefing_missing_title_retains_legacy_url_handling():
    # The URL before a title is currently ignored, not repaired by extraction.
    article = parse_briefing('#1 [Source] 🏷️ fiscal\nhttps://example.com\n')[1]
    assert article['title'] is None and article['url'] is None
    assert parse_briefing('# malformed\nTitle\n') == {}


@pytest.mark.parametrize('action,weight,key', [('liked',2,'like'),('saved',1,'save'),('disliked',-1,'dislike'),('unknown',1,'save')])
def test_learning_mutates_existing_counts_and_uses_explicit_dependencies(action,weight,key):
    prefs = {'learned_patterns': {'preferred_content_types': {'analysis':3}, 'preferred_themes':{},
        'preferred_sources':{}, 'avoid_patterns':{}, 'sample_size':4}}
    patterns = prefs['learned_patterns']
    metadata = {'content_type':['analysis'],'themes':['policy'],'source':'Example','url':'https://example.com', 'signals':['thin']}
    result = update_patterns(prefs, metadata, action, now=lambda:datetime(2026,10,29), classify_url=lambda url:('example.com','news'))
    assert result is None and prefs['learned_patterns'] is patterns
    assert patterns['preferred_content_types']['analysis'] == 3+weight
    assert patterns['preferred_themes']['policy'] == weight
    assert patterns['preferred_sources']['Example'] == weight
    assert patterns['content_domains']['example.com'][key] == 1
    assert patterns['source_types']['news'][key] == 1
    assert patterns['sample_size'] == 5 and patterns['last_updated'] == '2026-10-29T00:00:00'
    assert patterns['avoid_patterns'] == ({'thin':1} if action=='disliked' else {})


def test_learning_classifier_failure_preserves_prior_updates():
    prefs = {'learned_patterns': {'preferred_content_types':{},'preferred_themes':{},'preferred_sources':{},'avoid_patterns':{}}}
    def broken(url): raise RuntimeError('synthetic')
    update_patterns(prefs, {'source':'X','url':'https://example.com'}, 'liked', now=lambda:datetime(2026,10,29), classify_url=broken)
    assert prefs['learned_patterns']['preferred_sources'] == {'X':2}
    assert prefs['learned_patterns']['sample_size'] == 1
    assert 'content_domains' not in prefs['learned_patterns']
    assert deterministic_metadata({'source':'X','url':'u'})['themes'] == []


def test_adapter_keeps_missing_file_exit(tmp_path,monkeypatch):
    from domains.curator import curator_feedback as adapter
    monkeypatch.setattr(adapter,'CURATOR_OUTPUT',tmp_path/'missing')
    with pytest.raises(SystemExit) as exc: adapter.parse_curator_output()
    assert exc.value.code == 1
