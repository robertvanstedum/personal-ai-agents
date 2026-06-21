"""
Environment role helpers for mini-moi two-node setup.

MINIMOI_ROLE=production  — real bots, cron runs, outbound alerts active
MINIMOI_ROLE=standby     — test bots, cron suppressed, outbound alerts suppressed

Default is production so EC2 works without any explicit setting.
Standby must be explicitly set (Mac dev environment).
"""
import os


def is_production() -> bool:
    """True if this instance holds the production role."""
    return os.environ.get('MINIMOI_ROLE', 'production') == 'production'


def get_telegram_env() -> str:
    """Returns SSM path segment: 'production' or 'test'."""
    return 'production' if is_production() else 'test'


def role_label() -> str:
    return os.environ.get('MINIMOI_ROLE', 'production')
