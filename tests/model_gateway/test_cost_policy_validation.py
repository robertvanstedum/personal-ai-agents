"""Reject invalid policy values at load time, before warning evaluation."""
from datetime import datetime, timezone
import json

import pytest

from services.model_gateway.cost_reporting import load_cost_policy, build_cost_checkpoint


@pytest.mark.parametrize('field', ['daily_warn_usd', 'monthly_warn_usd', 'fallback_rate_warn'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), 10**400],
                         ids=['nan', 'infinity', 'negative-infinity', 'float-overflow'])
def test_nonfinite_warning_thresholds_are_rejected(tmp_path, field, value):
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps({field: value}))
    with pytest.raises(ValueError, match=field):
        load_cost_policy(path)


def test_minimum_sample_size_cannot_be_null(tmp_path):
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps({'fallback_rate_warn': 0.25,
                                'minimum_requests_for_fallback_rate': None}))
    with pytest.raises(ValueError, match='minimum_requests_for_fallback_rate'):
        load_cost_policy(path)


def test_null_disables_only_optional_warnings(tmp_path):
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps({'daily_warn_usd': None, 'monthly_warn_usd': None,
                                'fallback_rate_warn': None, 'unpriced_request_warn': None}))
    policy = load_cost_policy(path)
    result = build_cost_checkpoint([], policy, now=datetime(2026, 10, 28, tzinfo=timezone.utc))
    assert policy.minimum_requests_for_fallback_rate == 10
    assert result['alerts'] == []
    assert result['budget_thresholds_configured'] is False


@pytest.mark.parametrize('value', [True, -1, 1.5, '10'])
@pytest.mark.parametrize('field', ['minimum_requests_for_fallback_rate', 'unpriced_request_warn'])
def test_count_thresholds_require_nonnegative_integers(tmp_path, field, value):
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps({field: value}))
    with pytest.raises(ValueError, match=field):
        load_cost_policy(path)


def test_zero_and_finite_values_retain_warning_behavior(tmp_path):
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps({'daily_warn_usd': 0, 'monthly_warn_usd': 1.25,
                                'fallback_rate_warn': 0, 'minimum_requests_for_fallback_rate': 0,
                                'unpriced_request_warn': 0}))
    result = build_cost_checkpoint([], load_cost_policy(path),
                                   now=datetime(2026, 10, 28, tzinfo=timezone.utc))
    assert {a['type'] for a in result['alerts']} == {'daily_spend', 'fallback_rate', 'unpriced_requests'}
