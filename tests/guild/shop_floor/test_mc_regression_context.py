from minimoi_portal.guild_ui import mc_regression as m

ENV = {'MINIMOI_GUILD_REGRESSION_REPORTS': '1',
       'MINIMOI_GUILD_REGRESSION_OWNER': 'owner',
       'MINIMOI_GUILD_REGRESSION_ROOT': '/private/results'}


def test_denied_or_unconfigured_never_reads(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('read without authority')
    monkeypatch.setattr(m.regression, 'history', refuse)
    assert m.block('hello', 'owner', environ=ENV) == ''
    assert m.block('show regression results', 'other', environ=ENV) == m.UNAVAILABLE
    assert m.block('show regression results', 'owner', environ={}) == m.UNAVAILABLE
    assert m.block('show regression results', 'owner', environ={**ENV, 'MINIMOI_GUILD_REGRESSION_ROOT':'relative'}) == m.UNAVAILABLE


def row(n=0):
    return {'run_id': 'r-'+format(n, '032x'), 'state':'complete',
            'started_at':'2026-10-16T12:00:00Z', 'finished_at':'2026-10-16T12:01:00Z',
            'counts':{'needs_review':20}, 'question':'PRIVATE QUESTION',
            'suite_id':'PRIVATE SUITE', 'source':'/private/SECRET'}


def test_projects_only_bounded_metadata_no_acceptance_claim(monkeypatch):
    seen=[]
    monkeypatch.setattr(m.regression, 'history', lambda root, **kwargs: seen.append(root) or [row(n) for n in range(8)])
    out=m.block('show regression /etc/passwd', 'owner', environ=ENV)
    assert seen==['/private/results']
    assert 'PRIVATE' not in out and 'SECRET' not in out and '/etc/passwd' not in out
    assert '"returned_runs": 6' in out and '"omitted_older_runs": 2' in out
    assert 'not_established_by_execution' in out and '2026-10-16T12:01:00+00:00' in out


def test_bad_metadata_or_reader_failure_refuses_without_raw_error(monkeypatch):
    bad=row();bad['counts']['pass']='PRIVATE'
    monkeypatch.setattr(m.regression, 'history', lambda root, **kwargs:[bad])
    assert m.block('regression', 'owner', environ=ENV)==m.UNAVAILABLE
    monkeypatch.setattr(m.regression, 'history', lambda root, **kwargs: (_ for _ in ()).throw(OSError('PRIVATE')))
    assert m.block('regression', 'owner', environ=ENV)==m.UNAVAILABLE


def test_incomplete_run_is_visible_not_passed(monkeypatch):
    monkeypatch.setattr(m.regression, 'history', lambda root, **kwargs:[{'run_id':'r-'+'a'*32,'state':'incomplete_or_invalid'}])
    out=m.block('test suite', 'owner', environ=ENV)
    assert 'incomplete_or_invalid' in out and '"counts"' not in out


def test_noncanonical_failed_directory_does_not_hide_good_runs_or_leak_name(monkeypatch):
    rows=[{'run_id':'r-PRIVATE_OLD_ATTEMPT','state':'incomplete_or_invalid'},row()]
    monkeypatch.setattr(m.regression,'history',lambda root, **kwargs:rows)
    out=m.block('regression','owner',environ=ENV)
    assert out!=m.UNAVAILABLE and 'PRIVATE_OLD_ATTEMPT' not in out
    assert 'unidentified_invalid_run' in out and '"needs_review": 20' in out
