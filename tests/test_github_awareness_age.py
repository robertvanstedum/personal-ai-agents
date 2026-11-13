"""#561 (13 Nov 2026): MC's GitHub reads were rate-limited, so it used the cached awareness snapshot, whose last poll still
showed deploy run 43370360518 in progress, and told Robert the deploy was "still on the production health check, about two
minutes in", 45 minutes after it had finished. Every cached fact now carries when it was observed; a cached in-progress run
observed longer ago than the threshold (10 minutes by default) is said to be unconfirmed, never current. Fixtures only."""
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

P = Path(__file__).parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


C = module(P / 'core/github_awareness.py', 'core.github_awareness')
H = module(P / 'minimoi_portal/guild_ui/github_awareness.py', 'awareness_host_age')
OBSERVED = datetime(2026, 11, 13, 5, 17, tzinfo=timezone.utc)       # the snapshot's last poll of runs
NOW = OBSERVED + timedelta(minutes=46)                               # Robert's question
RUN = 43370360518
ENV = {'MINIMOI_MC_GITHUB_AWARENESS': '1', 'MINIMOI_MC_GITHUB_AWARENESS_OWNER': 'robert'}
CONV = {'principal': 'robert', 'record_mode': 'on_record'}


def snapshot(*, status='in_progress', conclusion=None, observed=OBSERVED):
    doc = {'format': 'minimoi.github-observations/1', 'repo': C.REPO, 'writer_domain': 'fixture', 'generation': 7,
           'last_attempt': C.iso(NOW - timedelta(minutes=1)), 'streams': {}}
    for name in C.STREAMS:
        doc['streams'][name] = {'cursor': C.iso(observed), 'last_success': C.iso(observed), 'last_attempt': C.iso(NOW),
                                'health': 'rate_limited' if name == 'runs' else 'ok', 'retry_at': None, 'items': [],
                                'evicted': 0, 'gap': 'initial_window',
                                'coverage': 'rolling_window' if name in {'events', 'runs', 'deployments'} else 'updated_since_cursor'}
    run = {'id': RUN, 'name': 'Deploy to production', 'status': status, 'conclusion': conclusion, 'head_sha': 'a' * 40,
           'head_branch': 'main', 'updated_at': C.iso(OBSERVED - timedelta(minutes=2)),
           'html_url': f'https://github.com/{C.REPO}/actions/runs/{RUN}'}
    doc['streams']['runs']['items'] = [C.project('runs', run, observed)]
    return C.validate(doc)


def block(doc, now=NOW, env=ENV):
    return H.block('robert', CONV, environ=env, now=now, loader=lambda root: doc)


def payload(text):
    return json.loads(re.search(r'\nBEGIN [0-9a-f]+\n(.*)\nEND [0-9a-f]+$', text, re.S).group(1))


def test_as_of_says_the_time_and_the_age():
    assert C.as_of(C.iso(OBSERVED), NOW) == 'as of 05:17 UTC, 46 min ago'
    assert C.as_of(C.iso(OBSERVED), OBSERVED + timedelta(seconds=20)) == 'as of 05:17 UTC, just now'
    assert C.as_of(C.iso(OBSERVED), OBSERVED + timedelta(hours=2, minutes=5)) == 'as of 05:17 UTC, 2 h 5 min ago'
    assert C.as_of(C.iso(OBSERVED), OBSERVED + timedelta(days=3)) == 'as of 05:17 UTC on 13 Nov, 3 days ago'
    assert C.as_of(None, NOW) == 'never observed'


def test_the_threshold_is_ten_minutes_by_default_and_configurable_within_bounds():
    assert C.stale_running_after({}) == 600 == C.STALE_RUNNING_S
    assert C.stale_running_after({'MINIMOI_MC_GITHUB_STALE_RUNNING_SECONDS': '300'}) == 300
    assert C.stale_running_after({'MINIMOI_MC_GITHUB_STALE_RUNNING_SECONDS': '5'}) == 60
    assert C.stale_running_after({'MINIMOI_MC_GITHUB_STALE_RUNNING_SECONDS': 'soon'}) == 600


def test_the_incident_a_46_minute_old_in_progress_run_is_never_presented_as_running():
    text = block(snapshot())
    head, order, plain = text.split('\n')[:3]
    assert head == H.HEAD and order == H.ORDER
    assert plain == (f'runs:{RUN} was last seen in_progress as of 05:17 UTC, 46 min ago; this cache cannot confirm whether '
                     'it is still running, so never say it is running now.')
    run = payload(text)['recent_observations'][0]
    assert run['identity'] == f'runs:{RUN}' and run['observed'] == 'as of 05:17 UTC, 46 min ago'
    assert run['live_status'].startswith('unconfirmed: last seen in_progress as of 05:17 UTC, 46 min ago')
    assert payload(text)['stream_status']['runs']['observed'] == 'as of 05:17 UTC, 46 min ago'


def test_the_block_names_the_delivery_record_first_and_the_cache_last():
    order = H.ORDER
    first, live, last = (order.index(w) for w in ('/minimoi/docs/delivery/PRODUCTION.md', 'minimoi_github_get',
                                                   'this cache only as a last resort'))
    assert first < live < last and 'Never present a cached in-progress run as current.' in order


def test_a_fresh_in_progress_run_carries_its_age_but_is_not_flagged():
    text = block(snapshot(), now=OBSERVED + timedelta(minutes=3))
    assert 'cannot confirm' not in text
    run = payload(text)['recent_observations'][0]
    assert run['observed'] == 'as of 05:17 UTC, 3 min ago' and 'live_status' not in run and run['state'] == 'in_progress'


def test_the_threshold_setting_moves_the_line():
    env = {**ENV, 'MINIMOI_MC_GITHUB_STALE_RUNNING_SECONDS': '120'}
    assert 'cannot confirm' in block(snapshot(), now=OBSERVED + timedelta(minutes=3), env=env)


def test_a_finished_run_is_never_flagged_and_still_says_how_old_it_is():
    text = block(snapshot(status='completed', conclusion='success'))
    assert 'cannot confirm' not in text
    run = payload(text)['recent_observations'][0]
    assert run['state'] == 'success' and run['observed'] == 'as of 05:17 UTC, 46 min ago' and 'live_status' not in run


def test_a_stream_never_synced_makes_any_in_progress_item_unconfirmed():
    doc = snapshot()
    doc['streams']['runs']['last_success'] = None
    text = block(doc)
    assert f'runs:{RUN} was last seen in_progress never observed' in text
    assert payload(text)['stream_status']['runs']['observed'] == 'never observed'


def test_queued_and_pending_deployments_count_as_in_progress_issues_never_do():
    assert C.unconfirmed_running({'kind': 'runs', 'state': 'queued'}, C.iso(OBSERVED), NOW)
    assert C.unconfirmed_running({'kind': 'deployments', 'state': 'pending'}, C.iso(OBSERVED), NOW)
    assert not C.unconfirmed_running({'kind': 'issues', 'state': 'open'}, C.iso(OBSERVED), NOW)
    assert not C.unconfirmed_running({'kind': 'runs', 'state': 'in_progress'}, C.iso(NOW - timedelta(minutes=9)), NOW)


def test_an_unavailable_cache_still_says_nothing_was_fetched():
    text = H.block('robert', CONV, environ=ENV, now=NOW, loader=lambda root: (_ for _ in ()).throw(OSError('gone')))
    assert text.endswith('No live fetch was attempted.')
