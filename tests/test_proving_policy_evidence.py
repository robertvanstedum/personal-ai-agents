from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/staging/mc_probe'))
import proving_model_guard as guard

def test_only_instruction_roles_prove_policy_delivery(tmp_path,monkeypatch):
    policy=guard.POLICY_MARKER+'\nPrivate instructions.\n'
    p=tmp_path/'policy';p.write_text(policy);monkeypatch.setattr(guard,'POLICY_FILE',p)
    for role in ['user','assistant','tool']:
        evidence=guard.policy_evidence([{'role':role,'content':policy}])
        assert not evidence['policy_in_system'] and not evidence['policy_marker_in_system']
    for role in ['system','developer']:
        evidence=guard.policy_evidence([{'role':role,'content':[{'type':'text','text':'prefix\n'+policy+'\nsuffix'}]}])
        assert evidence['policy_in_system'] and evidence['policy_marker_in_system']
        assert 'Private instructions' not in str(evidence)

def test_truncated_policy_is_distinct_from_full_delivery(tmp_path,monkeypatch):
    p=tmp_path/'policy';p.write_text(guard.POLICY_MARKER+'\nwhole policy');monkeypatch.setattr(guard,'POLICY_FILE',p)
    evidence=guard.policy_evidence([{'role':'system','content':guard.POLICY_MARKER}])
    assert evidence['policy_marker_in_system'] and not evidence['policy_in_system']

def test_missing_or_oversize_policy_does_not_log_content(tmp_path,monkeypatch):
    p=tmp_path/'policy';monkeypatch.setattr(guard,'POLICY_FILE',p)
    assert guard.policy_evidence([])=={'policy_expected':False}
    p.write_text('secret'*10000)
    assert guard.policy_evidence([])=={'policy_expected':False}


def test_whitespace_normalization_and_active_prefix_are_distinct(tmp_path,monkeypatch):
    policy=guard.POLICY_MARKER+'\nAnswer briefly.\n---\nHandbook text.'
    p=tmp_path/'policy';p.write_text(policy);monkeypatch.setattr(guard,'POLICY_FILE',p)
    e=guard.policy_evidence([{'role':'system','content':policy.replace('\n','\r\n')}])
    assert not e['policy_in_system'] and e['policy_normalized_in_system']
    assert e['policy_prefix_normalized_in_system']
    e=guard.policy_evidence([{'role':'system','content':policy.split('\n---\n')[0]+'\n[truncated]'}])
    assert e['policy_prefix_in_system'] and not e['policy_normalized_in_system']
