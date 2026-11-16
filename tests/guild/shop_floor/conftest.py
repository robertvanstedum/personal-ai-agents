"""Shop-floor browser runs record the host they ran under."""
from __future__ import annotations


def pytest_sessionfinish(session, exitstatus):
    from browser_host import finish_session
    finish_session(exitstatus)
