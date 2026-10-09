"""Scheduled jobs: a tool-neutral registry and status contract (see docs/jobs_status_contract.md).

Nothing here depends on a particular scheduler, alert channel or dashboard. The registry says
what should run and how often; each job writes one small status file; the Guild Operate tile,
the watchdog and any future exporter (Prometheus, Grafana) only read those two things.
"""
