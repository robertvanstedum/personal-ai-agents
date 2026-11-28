from core.mc_delegation_runner.runner import job_short


def test_job_short_uat_marker():
    assert job_short('30ff5b4d-b96b-517f-85eb-5b0b3f9b250d', 1) == '30ff5b4d-g1'
