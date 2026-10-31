"""Operator probe boundaries; all dispatch and usage are synthetic."""
from types import SimpleNamespace
import pytest
from minimoi_portal.guild_ui import api
from minimoi_portal.guild_ui.mc import cost_probe, stream_probe

BAD_COSTS = [float('nan'), float('inf'), -float('inf'), -0.1, True, None, '0.1', 10 ** 400]
BAD_CAPS = [float('nan'), float('inf'), -float('inf'), -1, 0, True, None, 10 ** 400]


@pytest.mark.parametrize('bad', BAD_COSTS)
def test_plain_probe_stops_after_invalid_usage(tmp_path, monkeypatch, bad):
    sent = []
    floor = SimpleNamespace(floor='synthetic', add_note=lambda *a, **k: SimpleNamespace(outcome='kept', value={}))
    conversations = SimpleNamespace(get=lambda *a: {'id': 'thread'}, create=lambda *a, **k: ({'id': 'fresh'}, None), rename=lambda *a: None)
    def dispatch(*a, **k):
        sent.append(k)
        return {'status': 'answered'}, 200
    monkeypatch.setattr(api, 'run_mc_turn', dispatch)
    monkeypatch.setattr(cost_probe, 'usage_in_window', lambda *a: [{'record_id': str(len(sent)), 'cost_usd': bad}])
    result = cost_probe.run(SimpleNamespace(floor=floor), conversations, principal='synthetic', label='Synthetic',
                            usage_dir=str(tmp_path), out=lambda *a: None)
    assert len(sent) == 1
    assert result['stopped'] == 'usage_unknown'
    assert result['rows'][0]['cost_usd'] is None
    assert result['spent'] == 0


@pytest.mark.parametrize('bad', BAD_COSTS)
def test_stream_probe_stops_after_invalid_usage(tmp_path, monkeypatch, bad):
    sent = []
    def turn(*a, **k):
        sent.append(k)
        return {'status': 'answered', 'cost_usd': bad}
    monkeypatch.setattr(stream_probe, 'one_turn', turn)
    result = stream_probe.run(None, None, principal='synthetic', label='Synthetic', stop_only=False,
                              cap=1, usage_dir=str(tmp_path), out=lambda *a: None)
    assert len(sent) == 1 and result['stopped'] == 'usage_unknown' and result['spent'] == 0


@pytest.mark.parametrize('bad', BAD_CAPS)
@pytest.mark.parametrize('mode', ['plain', 'stream'])
def test_invalid_cap_refused_before_work(tmp_path, monkeypatch, bad, mode):
    monkeypatch.setattr(api, 'run_mc_turn', lambda *a, **k: pytest.fail('dispatch'))
    monkeypatch.setattr(stream_probe, 'one_turn', lambda *a, **k: pytest.fail('dispatch'))
    kwargs = dict(principal='synthetic', label='Synthetic', cap=bad, usage_dir=str(tmp_path))
    with pytest.raises(ValueError, match='cap'):
        if mode == 'plain':
            cost_probe.run(None, None, **kwargs)
        else:
            stream_probe.run(None, None, stop_only=False, **kwargs)


@pytest.mark.parametrize('cap', ['nan', 'inf', '-1', '0'])
def test_cli_refuses_invalid_cap(cap, capsys):
    assert cost_probe.main(['--yes-spend', '--cap', cap], services=None) == 2
    assert 'cap' in capsys.readouterr().out


def test_zero_cost_allows_both_stream_turns(tmp_path, monkeypatch):
    sent = []
    def turn(*a, **k):
        sent.append(k)
        return {'status': 'answered', 'cost_usd': 0.0}
    monkeypatch.setattr(stream_probe, 'one_turn', turn)
    result = stream_probe.run(None, None, principal='synthetic', label='Synthetic', stop_only=False,
                              cap=1, usage_dir=str(tmp_path), out=lambda *a: None)
    assert len(sent) == 2 and result['stopped'] is None and result['spent'] == 0

@pytest.mark.parametrize('values', [[1e308, 1e308], [10 ** 400], [0.1, float('nan')], [None], []])
def test_cost_total_refuses_invalid_or_overflowed_evidence(values):
    from minimoi_portal.guild_ui.mc.probe_metrics import total
    assert total(values, require_all=True) is None


def test_valid_totals_keep_precision_and_partial_token_behavior():
    from minimoi_portal.guild_ui.mc.probe_metrics import total, probe_cap
    assert total([0, 0.1, 0.2], require_all=True) == 0.1 + 0.2
    assert total([2, None, -1, float('nan'), 3]) == 5
    assert total([0], require_all=True) == 0
    assert probe_cap(5, 1) == 1
    assert probe_cap('0.5', 1) == 0.5
