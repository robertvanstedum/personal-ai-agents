"""Item history from guild.design_log_transitions, or *unknown*.

Unlike the legacy endpoint (which answers [] on any error), a failed read is
unknown and a missing database is "not instrumented". The connection is
bounded: 2 s to connect, 2 s per statement.
"""
from __future__ import annotations

from typing import Callable

from .contract import READ_FAILED, SourceResult, live_ok, live_unknown, not_configured

EVIDENCE = "guild.design_log_transitions (read)"


def _default_connect(url: str):
    import psycopg2
    return psycopg2.connect(url, connect_timeout=2, options="-c statement_timeout=2000")


class DbHistory:
    def __init__(self, database_url: Callable[[], str | None], connect=None):
        self._database_url = database_url
        self._connect = connect or _default_connect

    def history(self, item_id: int) -> SourceResult:
        url = self._database_url()
        if not url:
            return not_configured(EVIDENCE, "the history database")
        try:
            conn = self._connect(url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT from_status, to_status, triggered_by, reason, created_at "
                        "FROM guild.design_log_transitions WHERE design_log_id = %s "
                        "ORDER BY created_at ASC", (item_id,))
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:  # any failure is unknown, never an empty history
            return live_unknown(READ_FAILED, f"history read failed: {type(exc).__name__}", EVIDENCE)
        data = []
        for row in rows:
            from_s, to_s, by, reason, at = (list(row) + [None] * 5)[:5]
            data.append({"from": from_s or "—", "to": to_s, "by": by or "—", "reason": reason or "",
                         "at": at.isoformat() if hasattr(at, "isoformat") else (at or "")})
        return live_ok(data, EVIDENCE, fresh_for_s=300)
