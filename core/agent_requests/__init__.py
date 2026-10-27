"""Agent requests: a small file ledger so Master Craftsman (or any agent) can ask an existing Claude Code or Codex session for something
and read the attributed reply, without anyone carrying messages and without MC being a gate (MC front door, slice 1).

Standard library only, so the portal and the agents' command-line helper (scripts/agent_requests.py) share one implementation."""
from core.agent_requests.ledger import (  # noqa: F401
    ACTION_MAX, ACTORS, CONNECTED, EVENT_KINDS, connection_owner, RECIPIENTS, STAGES, TEXT_MAX, Refused, arm, armed_age, cancel, create_request, for_conversation, load, needs_you,
    notice, pending, progress, receipt, record_delivery, reconcile_request, terminal_failure, unarm, request_id_for, result, valid_conversation, valid_request_id)
