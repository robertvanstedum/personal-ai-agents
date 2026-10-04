"""Fixed reason codes for the shelf's writers and listings (amendment R4, §4).

Ledgers, status files and refusal listings carry **only** these codes plus
counts, ids and hashes. An exception message, a matched string or a piece of
conversation text is never written next to a code, because it can carry content.
"""
from __future__ import annotations

# Outcomes (one per considered source item)
CAPTURED = "captured"
EDITION_ADDED = "edition-added"
UNCHANGED = "unchanged"
EXCLUDED = "excluded"
REFUSED = "refused"
DISCOVERED = "discovered"          # seen, no outcome yet: a crash here reads as "missing"
FAILED = "failed"
UNSTABLE = "unstable"              # the file changed while it was read; retried next run
DISK_LOW = "disk_low"
HELD = "held"                      # a file kept for a later version: listed, untouched, not refused
OK_OUTCOMES = frozenset({CAPTURED, EDITION_ADDED, UNCHANGED})

# Expected-exclusion reasons (not "missing")
NEVER_COPY = "never_copy"
PRIVATE = "private"
EMPTY = "empty"
NOT_APPROVED = "not_approved"
ACCOUNT_METADATA = "account_metadata"      # login history, account data: never imported, left in place
EXPORT_MANIFEST = "export_manifest"        # expiring download links: never copied, ignored
UNSUPPORTED_KIND = "unsupported_kind"      # memories, projects, frames: held for a later version
ORPHAN_PARENT = "orphan_parent"            # a message whose parent is not in the export: kept as its own branch
EXTRA_ROOT = "extra_root"
UNKNOWN_SENDER = "unknown_sender"          # a Grok response whose sender is outside the closed set: the conversation is refused

# Inbox refusal reasons
UNPARSEABLE = "unparseable"
UNKNOWN_SCHEMA = "unknown_schema"
NOT_UTF8 = "not_utf8"
TOO_LARGE = "too_large"
UNSUPPORTED_TYPE = "unsupported_type"
ZIP_UNSAFE = "zip_unsafe"
NO_TURNS = "no_turns"
