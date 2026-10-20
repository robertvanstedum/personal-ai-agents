"""MC standing operations (spec v0.3.1): read-only watches, observations, findings and the morning brief.

The worker reads sanitized snapshots only. It holds no product-mutation credential, no Docker socket
and no model tools; it writes nothing outside its own spool. Every unreadable or stale source is an
explicit ``unknown``, never a green line.
"""
