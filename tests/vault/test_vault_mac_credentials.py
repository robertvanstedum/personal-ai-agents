"""The Mac Vault jobs reach S3 only as their own scoped principal, never the deploy user.

Keys live in the login Keychain and reach the AWS CLI through ``credential_process``. A pinned runtime that is configured for
the principal runs every ``aws`` command with an isolated environment (its own config file, no shared credentials file, no
ambient keys, no instance metadata) and refuses to touch S3 unless ``sts get-caller-identity`` names exactly that principal.
No network, Keychain or AWS account is used: ``security`` and ``aws`` are injected fakes.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import types

import pytest

from core.memory_backup import backup as B

REPO = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _load('aws_keychain_credentials', 'scripts/vault/aws_keychain_credentials.py')
R = _load('runtime_install', 'scripts/vault/runtime_install.py')
CHECK = _load('check_s3_access', 'scripts/vault/check_s3_access.py')

KEY_ID = 'AKIA' + 'Q' * 16
SECRET = 'S' * 39 + '/'
ARN = 'arn:aws:iam::123456789012:user/minimoi-vault-mac'
DEPLOY = 'arn:aws:iam::123456789012:user/minimoi-deploy'


def done(rc=0, out='', err=''):
    return subprocess.CompletedProcess([], rc, out, err)


# ── Keychain credential_process helper ────────────────────────────────────────

def keychain(values, calls=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append((cmd, kw))
        account = cmd[cmd.index('-a') + 1]
        value = values.get(account)
        return done(0, value + '\n') if value is not None else done(44, '', 'SecKeychainSearchCopyNext: The specified item could not be found')
    return run


def test_helper_prints_the_credential_process_document(capsys):
    calls = []
    rc = H.main(['--service', 'minimoi-vault-mac'], run=keychain({'access-key-id': KEY_ID, 'secret-access-key': SECRET}, calls))
    out, err = capsys.readouterr()
    assert rc == 0 and err == ''
    assert json.loads(out) == {'Version': 1, 'AccessKeyId': KEY_ID, 'SecretAccessKey': SECRET}
    assert all(c[0][0] == '/usr/bin/security' and c[0][1] == 'find-generic-password' and '-w' in c[0] for c in calls)
    assert all(c[1].get('shell') in (None, False) for c in calls)
    assert {c[0][c[0].index('-s') + 1] for c in calls} == {'minimoi-vault-mac'}


@pytest.mark.parametrize('values,code', [
    ({}, 'keychain_item_missing'),
    ({'access-key-id': KEY_ID}, 'keychain_item_missing'),
    ({'access-key-id': 'not-a-key', 'secret-access-key': SECRET}, 'credential_malformed'),
    ({'access-key-id': KEY_ID, 'secret-access-key': 'short'}, 'credential_malformed'),
])
def test_helper_fails_with_a_code_and_never_prints_a_value(values, code, capsys):
    rc = H.main(['--service', 'minimoi-vault-mac'], run=keychain(values))
    out, err = capsys.readouterr()
    assert rc != 0 and out == '' and err.strip() == code
    assert KEY_ID not in err and SECRET not in err


def test_helper_refuses_an_unsafe_service_name(capsys):
    assert H.main(['--service', 'x; rm -rf /'], run=keychain({})) != 0
    assert capsys.readouterr()[1].strip() == 'service_invalid'


# ── Pinned principal: isolated environment and identity check ─────────────────

@pytest.fixture
def pinned(tmp_path):
    conf = tmp_path / 'aws_config'
    conf.write_text('[profile minimoi-vault-mac]\nregion = us-east-1\ncredential_process = x\n')
    return {'profile': 'minimoi-vault-mac', 'principal_arn': ARN, 'config_file': str(conf)}


def aws(identity=ARN, *, sts_rc=0, record=None):
    def run(cmd, **kw):
        if record is not None:
            record.append((list(cmd), kw))
        if cmd[1:3] == ['sts', 'get-caller-identity']:
            return done(sts_rc, json.dumps({'Arn': identity, 'Account': '123456789012'}) if sts_rc == 0 else '', 'denied')
        return done(0, json.dumps({'Contents': []}))
    return run


def test_pinned_dest_checks_identity_once_then_runs_isolated(pinned, monkeypatch):
    for name in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_DEFAULT_PROFILE', 'AWS_PROFILE'):
        monkeypatch.setenv(name, 'ambient-deploy-value')
    calls = []
    d = B.Dest('s3://bucket/vault', run=aws(record=calls), aws=pinned)
    d.list_keys('sync/acks')
    d.list_keys('sync/mc-export')
    assert [c[0][1:3] for c in calls].count(['sts', 'get-caller-identity']) == 1
    assert calls[0][0][1:3] == ['sts', 'get-caller-identity']
    for cmd, kw in calls:
        env = kw['env']
        assert env['AWS_PROFILE'] == 'minimoi-vault-mac' and env['AWS_CONFIG_FILE'] == pinned['config_file']
        assert env['AWS_SHARED_CREDENTIALS_FILE'] == os.devnull and env['AWS_EC2_METADATA_DISABLED'] == 'true'
        assert not {'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_DEFAULT_PROFILE'} & set(env)
        assert 'ambient-deploy-value' not in env.values()


@pytest.mark.parametrize('identity,rc,code', [(DEPLOY, 0, 'aws_principal_mismatch'), (ARN, 255, 'aws_identity_unavailable')])
def test_pinned_dest_refuses_before_any_s3_call(pinned, identity, rc, code, tmp_path):
    calls = []
    d = B.Dest('s3://bucket/vault', run=aws(identity, sts_rc=rc, record=calls), aws=pinned)
    local = tmp_path / 'f'; local.write_text('x')
    for op in (lambda: d.list_keys('sync/acks'), lambda: d.put(local, 'sync/batches/b/x'), lambda: d.get_optional('sync/acks/i.json', local)):
        with pytest.raises(B.BackupError, match=code):
            op()
    assert all(c[0][1] == 'sts' for c in calls)


def test_unpinned_dest_keeps_todays_behaviour(monkeypatch):
    monkeypatch.setattr(B, 'pinned_aws', lambda root=None: None)
    calls = []
    d = B.Dest('s3://bucket/vault', run=aws(record=calls))
    d.list_keys('sync/acks')
    assert [c[0][1:3] for c in calls] == [['s3api', 'list-objects-v2']]
    assert 'env' not in calls[0][1]


def test_file_dest_never_asks_for_an_identity(pinned, tmp_path):
    calls = []
    d = B.Dest(f'file://{tmp_path}', run=aws(record=calls), aws=pinned)
    assert d.list_keys('sync/acks') == [] and calls == []


def test_declared_aws_settings_without_their_config_fail_closed(tmp_path):
    config = tmp_path / 'config'; config.mkdir()
    (config / 'runtime.json').write_text(json.dumps({'python': 'x', 'aws_profile': 'minimoi-vault-mac', 'aws_principal_arn': ARN,
                                                     'aws_keychain_service': 'minimoi-vault-mac'}))
    with pytest.raises(B.BackupError, match='aws_pin_invalid'):          # aws_config deleted: never fall back to ambient credentials
        B.pinned_aws(tmp_path)
    (config / 'runtime.json').write_text('{not json')
    with pytest.raises(B.BackupError, match='aws_pin_invalid'):          # an unreadable pinned profile is not "no AWS settings"
        B.pinned_aws(tmp_path)
    (config / 'runtime.json').write_text(json.dumps({'python': 'x'}))
    assert B.pinned_aws(tmp_path) is None                               # a pin that never declared a principal keeps today's chain


def test_a_dest_in_a_half_configured_pin_cannot_be_built(monkeypatch):
    def broken(root=None):
        raise B.BackupError('aws_pin_invalid')
    monkeypatch.setattr(B, 'pinned_aws', broken)
    with pytest.raises(B.BackupError, match='aws_pin_invalid'):
        B.Dest('s3://bucket/vault', run=aws())


def test_pinned_aws_reads_the_runtime_beside_the_code(tmp_path):
    config = tmp_path / 'config'; config.mkdir()
    assert B.pinned_aws(tmp_path) is None                              # a checkout: no aws_config, today's behaviour
    (config / 'aws_config').write_text('[profile minimoi-vault-mac]\n')
    with pytest.raises(B.BackupError, match='aws_pin_invalid'):         # half-configured pin: fail closed
        B.pinned_aws(tmp_path)
    (config / 'runtime.json').write_text(json.dumps({'aws_profile': 'minimoi-vault-mac', 'aws_principal_arn': ARN,
                                                     'aws_keychain_service': 'minimoi-vault-mac'}))
    assert B.pinned_aws(tmp_path) == {'profile': 'minimoi-vault-mac', 'principal_arn': ARN,
                                       'config_file': str(config / 'aws_config')}


def test_the_default_dest_uses_the_pin_it_was_loaded_from(monkeypatch, pinned):
    monkeypatch.setattr(B, 'pinned_aws', lambda root=None: pinned)
    calls = []
    B.Dest('s3://bucket/vault', run=aws(record=calls)).list_keys('sync/acks')
    assert calls[0][0][1:3] == ['sts', 'get-caller-identity']


# ── Runtime profile and pinned AWS config ─────────────────────────────────────

def profile_doc(tmp_path, **extra):
    doc = json.loads((REPO / 'config/vault_runtime.example.json').read_text())
    doc['public_recipient'] = 'age1' + 'a' * 58
    for key in ['shelf_root', 'inbox_root', 'jobs_root', 'logs_root', 'ask_registry', 'launch_agents']:
        doc[key] = str(tmp_path / key)
    doc.update(extra)
    p = tmp_path / 'profile.json'; p.write_text(json.dumps(doc))
    return p


AWS_KEYS = {'aws_profile': 'minimoi-vault-mac', 'aws_principal_arn': ARN, 'aws_keychain_service': 'minimoi-vault-mac'}


def test_profile_accepts_the_three_aws_keys_together(tmp_path):
    cfg = R.profile(profile_doc(tmp_path, **AWS_KEYS))
    assert {k: cfg[k] for k in AWS_KEYS} == AWS_KEYS


@pytest.mark.parametrize('extra', [
    {'aws_profile': 'minimoi-vault-mac'},
    {'aws_profile': 'minimoi-vault-mac', 'aws_principal_arn': ARN},
    dict(AWS_KEYS, aws_principal_arn='minimoi-vault-mac'),
    dict(AWS_KEYS, aws_principal_arn=DEPLOY.replace('minimoi-deploy', 'x y')),
    dict(AWS_KEYS, aws_profile='bad profile'),
    dict(AWS_KEYS, aws_keychain_service=KEY_ID),
    dict(AWS_KEYS, aws_profile=KEY_ID),
])
def test_profile_refuses_partial_or_secret_looking_aws_settings(tmp_path, extra):
    with pytest.raises(ValueError):
        R.profile(profile_doc(tmp_path, **extra))


def test_prepare_pins_the_helper_and_an_aws_config_without_secrets(tmp_path, monkeypatch):
    cfg = R.profile(profile_doc(tmp_path, **AWS_KEYS))
    monkeypatch.setattr(R, 'dependency_receipt', lambda py: {'python': 'test', 'packages': {}})
    result = R.prepare(REPO, cfg, tmp_path / 'prepared')
    pin = Path(result['runtime'])
    try:
        conf = pin / 'config/aws_config'
        text = conf.read_text()
        assert oct(conf.stat().st_mode & 0o777) == '0o400'
        assert text.startswith('[profile minimoi-vault-mac]\n') and 'region = us-east-1' in text
        process = next(line for line in text.splitlines() if line.startswith('credential_process = '))
        assert f"{cfg['python']} -I -B {pin / 'scripts/vault/aws_keychain_credentials.py'} --service minimoi-vault-mac" in process
        assert 'AKIA' not in text and 'aws_secret' not in text.lower()
        for script in ('aws_keychain_credentials.py', 'check_s3_access.py'):
            assert result['runtime_file_hashes']['scripts/vault/' + script]
        assert B.pinned_aws(pin) == {'profile': 'minimoi-vault-mac', 'principal_arn': ARN, 'config_file': str(conf)}
        assert any('minimoi-vault-mac' in line for line in result['limitations'])
    finally:
        for directory in [pin] + [p for p in pin.rglob('*') if p.is_dir()]:
            directory.chmod(0o700)


def test_prepare_without_aws_keys_writes_no_aws_config(tmp_path, monkeypatch):
    cfg = R.profile(profile_doc(tmp_path))
    monkeypatch.setattr(R, 'dependency_receipt', lambda py: {'python': 'test', 'packages': {}})
    result = R.prepare(REPO, cfg, tmp_path / 'prepared')
    pin = Path(result['runtime'])
    try:
        assert not (pin / 'config/aws_config').exists() and B.pinned_aws(pin) is None
    finally:
        for directory in [pin] + [p for p in pin.rglob('*') if p.is_dir()]:
            directory.chmod(0o700)


# ── Non-destructive access check (replaces the policy simulator) ──────────────

def s3(answers, record=None):
    """answers: (operation, key-or-prefix startswith) -> (rc, stderr). Identity is always ARN."""
    def run(cmd, **kw):
        if record is not None:
            record.append(list(cmd))
        if cmd[1:3] == ['sts', 'get-caller-identity']:
            return done(0, json.dumps({'Arn': ARN}))
        op = cmd[2]
        target = cmd[cmd.index('--key') + 1] if '--key' in cmd else cmd[cmd.index('--prefix') + 1]
        for (o, start), (rc, err) in answers.items():
            if o == op and target.startswith(start):
                return done(rc, '{}' if rc == 0 else '', err)
        assert op in ('put-object', 'delete-object'), f'unexpected S3 call {cmd}'
        if op == 'put-object':          # only a conditional forgery probe may be answered "denied" by default
            assert '--if-match' in cmd and '--server-side-encryption' in cmd and '--body' in cmd, cmd
        else:
            assert '--if-match' not in cmd and cmd[cmd.index('--key') + 1].split('_access-probe/')[1], cmd
        return done(254, '', 'An error occurred (AccessDenied) when calling the operation: Access Denied')
    return run


NOT_FOUND = (254, 'An error occurred (NoSuchKey) when calling the PutObject operation: The specified key does not exist.')
HEAD_404 = (254, 'An error occurred (404) when calling the HeadObject operation: Not Found')
DENIED = (254, 'An error occurred (AccessDenied) when calling the operation: Access Denied')


def good_mac_answers():
    a = {}
    for prefix in CHECK.MATRIX['mac']['get_allow']:
        a[('head-object', 'vault/' + prefix)] = HEAD_404
    for prefix in CHECK.MATRIX['mac']['list_allow']:
        a[('list-objects-v2', 'vault/' + prefix)] = (0, '')
    return a


def test_check_passes_a_policy_that_matches_the_matrix(pinned):
    calls = []
    report = CHECK.check('s3://bucket/vault', 'mac', run=s3(good_mac_answers(), calls), aws=pinned)
    assert report['ok'] is True and report['principal'] == ARN
    assert all(c['verdict'] == ('not_probed' if c['cell'].startswith('put_allow:') else 'ok') for c in report['cells'])
    puts = [c for c in calls if c[2] == 'put-object']
    assert puts and all('--if-match' in c and '--server-side-encryption' in c for c in puts)      # conditional: stores nothing
    put_keys = [c[c.index('--key') + 1] for c in puts]
    assert sorted(k.split('_access-probe/')[0] for k in put_keys) == sorted('vault/' + p for p in CHECK.MATRIX['mac']['put_deny'])
    deletes = [c for c in calls if c[2] == 'delete-object']
    assert len(deletes) == len(CHECK.MATRIX['mac']['delete_deny'])
    assert all('_access-probe/' in c[c.index('--key') + 1] for c in puts + deletes)                # fresh synthetic keys only
    assert report['not_probed'] == ['put_allow:' + p for p in CHECK.MATRIX['mac']['put_allow']]


def test_mac_matrix_has_no_backup_grants_and_forgery_checks_are_readable():
    mac = CHECK.MATRIX['mac']
    for kind in ('put_allow', 'get_allow', 'list_allow', 'put_deny'):
        assert not {'daily/', 'monthly/'} & set(mac[kind])              # nightly backup is the external volume, not S3
    assert {'daily/', 'monthly/'} <= set(mac['delete_deny'])
    for side, m in CHECK.MATRIX.items():
        assert set(m['put_deny']) <= set(m['get_allow']), side            # If-Match writes need read rights to mean anything


def test_check_flags_a_forgeable_prefix_and_a_written_probe(pinned):
    answers = good_mac_answers()
    answers[('put-object', 'vault/sync/acks/')] = NOT_FOUND          # the Mac could forge production acknowledgements
    answers[('put-object', 'vault/sync/mc-export/')] = (0, '')       # and a probe object was actually written
    report = CHECK.check('s3://bucket/vault', 'mac', run=s3(answers), aws=pinned)
    bad = {c['cell']: c['verdict'] for c in report['cells'] if c['verdict'] != 'ok'}
    assert report['ok'] is False
    assert bad['put_deny:sync/acks/'] == 'allowed_but_must_be_denied'
    assert bad['put_deny:sync/mc-export/'] == 'probe_written'
    assert any(k.startswith('vault/sync/mc-export/_access-probe/') for k in report['written_probe_keys'])


def test_check_flags_missing_keys_answered_as_forbidden(pinned):
    answers = good_mac_answers()
    answers[('head-object', 'vault/sync/acks/')] = DENIED              # get_optional would read "no ack yet" as a failure
    report = CHECK.check('s3://bucket/vault', 'mac', run=s3(answers), aws=pinned)
    cell = next(c for c in report['cells'] if c['cell'] == 'get_allow:sync/acks/')
    assert report['ok'] is False and cell['verdict'] == 'missing_key_forbidden'


def test_check_refuses_the_wrong_principal_before_probing(pinned):
    calls = []

    def run(cmd, **kw):
        calls.append(list(cmd))
        return done(0, json.dumps({'Arn': DEPLOY}))
    with pytest.raises(B.BackupError, match='aws_principal_mismatch'):
        CHECK.check('s3://bucket/vault', 'mac', run=run, aws=pinned)
    assert all(c[1] == 'sts' for c in calls)


def test_check_report_carries_no_credentials(pinned, capsys, monkeypatch):
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', SECRET)
    report = CHECK.check('s3://bucket/vault', 'mac', run=s3(good_mac_answers()), aws=pinned)
    assert SECRET not in json.dumps(report) and KEY_ID not in json.dumps(report)



# ── r2 (independent review, 18 Oct) ───────────────────────────────────────────

@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / 'home'; h.mkdir()
    monkeypatch.setenv('HOME', str(h))
    return h


@pytest.mark.parametrize('method', ['put_new', 'put_replace', 'get', 'get_optional', 'list_keys'])
def test_every_s3_method_of_a_pinned_dest_runs_isolated_after_the_identity(pinned, home, tmp_path, method):
    calls = []
    d = B.Dest('s3://bucket/vault', run=aws(record=calls), aws=pinned)
    local = tmp_path / 'f'; local.write_text('x')
    {'put_new': lambda: d.put(local, 'sync/batches/b/x'), 'put_replace': lambda: d.put(local, 'production/latest.json', replace=True),
     'get': lambda: d.get('sync/acks/a.json', local), 'get_optional': lambda: d.get_optional('sync/acks/a.json', local),
     'list_keys': lambda: d.list_keys('sync/acks')}[method]()
    assert calls[0][0][1:3] == ['sts', 'get-caller-identity'] and len(calls) == 2
    assert calls[1][1].get('env') == calls[0][1]['env'] and calls[1][1]['env']['AWS_PROFILE'] == 'minimoi-vault-mac'


def test_the_isolated_env_is_an_allowlist(pinned, home, monkeypatch):
    for name in ('AWS_ENDPOINT_URL', 'AWS_ENDPOINT_URL_STS', 'AWS_ENDPOINT_URL_S3', 'AWS_DATA_PATH', 'AWS_DEFAULT_REGION',
                 'PYTHONPATH', 'PYTHONHOME', 'AWS_CA_BUNDLE', 'BOTO_CONFIG'):
        monkeypatch.setenv(name, 'steer')
    env = B.aws_env(pinned)
    assert 'steer' not in env.values()
    assert set(env) <= set(B.AWS_ENV_KEEP) | {'AWS_CONFIG_FILE', 'AWS_SHARED_CREDENTIALS_FILE', 'AWS_PROFILE', 'AWS_EC2_METADATA_DISABLED'}


@pytest.mark.parametrize('override', ['.aws/cli/alias', '.aws/models'])
def test_cli_overrides_in_the_home_folder_are_refused_before_any_call(pinned, home, override):
    target = home / override
    target.parent.mkdir(parents=True, exist_ok=True)
    (target.mkdir() if override.endswith('models') else target.write_text('[toplevel]\nsts = !echo forged\n'))
    calls = []
    d = B.Dest('s3://bucket/vault', run=aws(record=calls), aws=pinned)
    with pytest.raises(B.BackupError, match='aws_cli_override_present'):
        d.list_keys('sync/acks')
    assert calls == []


def test_an_ssm_identity_from_a_pin_is_read_as_the_pinned_principal(pinned, home, monkeypatch):
    monkeypatch.setattr(B, 'pinned_aws', lambda root=None: pinned)
    calls = []
    key = 'AGE-SECRET-KEY-1' + 'Q' * 58

    def run(cmd, **kw):
        calls.append((list(cmd), kw))
        if cmd[1:3] == ['sts', 'get-caller-identity']:
            return done(0, json.dumps({'Arn': ARN}))
        return done(0, key + '\n')
    assert B.fetch_identity('ssm:/minimoi/x', run=run).strip() == key
    assert [c[0][1] for c in calls] == ['sts', 'ssm']
    assert calls[1][1]['env']['AWS_PROFILE'] == 'minimoi-vault-mac'


def test_an_ssm_identity_from_a_pin_refuses_another_principal(pinned, home, monkeypatch):
    monkeypatch.setattr(B, 'pinned_aws', lambda root=None: pinned)
    calls = []

    def run(cmd, **kw):
        calls.append(list(cmd))
        return done(0, json.dumps({'Arn': DEPLOY}))
    with pytest.raises(B.BackupError, match='aws_principal_mismatch'):
        B.fetch_identity('ssm:/minimoi/x', run=run)
    assert [c[1] for c in calls] == ['sts']


def test_profile_and_helper_accept_only_an_iam_user_with_long_term_keys(tmp_path, capsys):
    with pytest.raises(ValueError):
        R.profile(profile_doc(tmp_path, **dict(AWS_KEYS, aws_principal_arn='arn:aws:iam::123456789012:role/minimoi-vault-mac')))
    rc = H.main(['--service', 'minimoi-vault-mac'], run=keychain({'access-key-id': 'ASIA' + 'Q' * 16, 'secret-access-key': SECRET}))
    assert rc != 0 and capsys.readouterr()[1].strip() == 'credential_malformed'


def test_prepare_quotes_paths_with_spaces_for_credential_process(tmp_path, monkeypatch):
    import shlex
    cfg = R.profile(profile_doc(tmp_path, **AWS_KEYS))
    cfg['python'] = str(tmp_path / 'py env' / 'python3')
    monkeypatch.setattr(R, 'dependency_receipt', lambda py: {'python': 'test', 'packages': {}})
    result = R.prepare(REPO, cfg, tmp_path / 'prepared with space')
    pin = Path(result['runtime'])
    try:
        line = next(l for l in (pin / 'config/aws_config').read_text().splitlines() if l.startswith('credential_process = '))
        assert shlex.split(line[len('credential_process = '):]) == [
            cfg['python'], '-I', '-B', str(pin / 'scripts/vault/aws_keychain_credentials.py'), '--service', 'minimoi-vault-mac']
        assert result['runtime_file_hashes']['scripts/vault/publish_production.py']          # the seed publish runs from the pin too
    finally:
        for directory in [pin] + [p for p in pin.rglob('*') if p.is_dir()]:
            directory.chmod(0o700)


def test_ec2_check_matches_the_instance_role_session_and_refuses_a_mac_pin(pinned, home):
    role = 'arn:aws:iam::123456789012:role/minimoi-ec2'
    session = 'arn:aws:sts::123456789012:assumed-role/minimoi-ec2/i-0abc'

    def run(cmd, **kw):
        if cmd[1:3] == ['sts', 'get-caller-identity']:
            return done(0, json.dumps({'Arn': session}))
        op = cmd[2]
        key = cmd[cmd.index('--key') + 1] if '--key' in cmd else ''
        if op == 'head-object':
            return done(*HEAD_404)
        if op == 'list-objects-v2':
            return done(0, '{}')
        return done(*DENIED)
    report = CHECK.check('s3://bucket/vault', 'ec2', run=run, aws=None, expect_principal=role)
    assert report['ok'] is True and report['principal'] == session
    with pytest.raises(B.BackupError, match='aws_principal_mismatch'):
        CHECK.check('s3://bucket/vault', 'ec2', run=run, aws=None, expect_principal='arn:aws:iam::123456789012:role/other')
    with pytest.raises(B.BackupError, match='side_mismatch'):
        CHECK.check('s3://bucket/vault', 'ec2', run=aws(), aws=pinned, expect_principal=role)



def test_an_accepted_delete_names_its_key_for_version_aware_inspection(pinned, home):
    answers = good_mac_answers()
    answers[('delete-object', 'vault/sync/acks/')] = (0, '')          # wrongly allowed: may have left a delete marker
    report = CHECK.check('s3://bucket/vault', 'mac', run=s3(answers), aws=pinned)
    cell = next(c for c in report['cells'] if c['cell'] == 'delete_deny:sync/acks/')
    assert report['ok'] is False and cell['verdict'] == 'allowed_but_must_be_denied'
    assert len(report['delete_marker_candidate_keys']) == 1
    assert report['delete_marker_candidate_keys'][0].startswith('vault/sync/acks/_access-probe/')
    assert report['written_probe_keys'] == []
    assert report['inspect'].startswith('aws s3api list-object-versions --bucket bucket --prefix ')


def test_a_clean_report_says_it_is_partial_evidence(pinned, home):
    report = CHECK.check('s3://bucket/vault', 'mac', run=s3(good_mac_answers()), aws=pinned)
    assert report['ok'] is True and report['delete_marker_candidate_keys'] == [] and report['inspect'] is None
    assert report['evidence'].startswith('partial')
