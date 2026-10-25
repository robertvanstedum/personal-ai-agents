"""MC's tool policy v0: a strict declarative file over code-owned handlers and floors (spec v1.2 §8.1–8.3).

The policy file is operator-installed (never in a model's reach) and names, for each enabled capability, a
code-owned handler ID; it never contains import paths, commands, file destinations or URLs. A separate host-installed
authority record ties the file's revision and digest to Robert's approval: editing a row approves nothing, and a file
without a matching record enables nothing. The loader refuses duplicate keys, unknown fields, unknown handlers,
mismatched verifiers or egress, and any tier the handler's code floor does not allow, so a guest decision, an access
change, a public write or delegated work can never become tier 1 or 2 by an edit. An invalid or missing policy
disables every tool with a visible diagnostic; there is no permissive fallback.

``CompiledPolicy.version`` ("mc-policy:<revision>:<sha256>") is the ActionContext / C07 policy version, so a policy
swap makes every grant issued under the previous one unusable, and its activation revokes live turns' admission.

Operator use:  python -m core.mc_tools.policy digest <policy.json>
               python -m core.mc_tools.policy check <policy.json> <authority.json>
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from dataclasses import dataclass

from core.minimoi_actions.context import ActionContractError

from . import catalogue

SCHEMA = "mc-policy/1"
AUTHORITY_SCHEMA = "mc-policy-authority/1"
_REVISION = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_RULE_FIELDS = {"capability", "version", "actors", "origins", "tier", "approval", "source_requirement", "handler",
                "verify", "undo", "egress", "max_operations_per_turn"}
_APPROVAL_FOR_TIER = {1: "none", 2: "standing", 3: "exact"}


@dataclass(frozen=True)
class Handler:
    """What code implements, and the least authority its consequence allows (the floor)."""
    tool: str
    capability: str
    tiers: frozenset
    verify: str | None
    egress: str
    undo: str | None = None


# Code-owned. A capability that decides about a guest, changes access, writes publicly or starts paid/delegated work
# is tier 3 only: an exact owner approval for each effect (spec §8.1 floors, §8.2 placement).
HANDLERS = {
    "recall_v1": Handler("minimoi_recall", "mc.records.read", frozenset({1}), None, "none"),
    "agent_contact_v1": Handler("minimoi_agent_contact", "agent.contact.existing", frozenset({2}), "native_agent_receipt_v1", "none"),
    "agent_status_v1": Handler("minimoi_agent_status", "agent.contact.status", frozenset({1}), None, "none"),
    "agent_watch_v1": Handler("minimoi_agent_watch", "agent.watch", frozenset({2}), "native_notice_readback_v1", "none"),
    "career_trial_v1": Handler("minimoi_career_trial", "host.career.synthetic", frozenset({2}), "host_artifact_readback_v1", "none"),
    "working_write_v1": Handler("minimoi_working_write", "host.artifacts.create", frozenset({2}), "host_artifact_readback_v1", "none"),
    "conversation_export_v1": Handler("minimoi_conversation_export", "host.conversation.export", frozenset({2}), "host_artifact_readback_v1", "none"),
    "artifact_recover_v1": Handler("minimoi_artifact_recover", "host.artifacts.recover", frozenset({1}), None, "none"),
    "host_read_v1": Handler("minimoi_host_read", "host.files.read", frozenset({1}), None, "none"),
    "host_list_v1": Handler("minimoi_host_list", "host.files.list", frozenset({1}), None, "none"),
    "host_health_v1": Handler("minimoi_host_health", "host.health", frozenset({1}), None, "none"),
    "guest_list_v1": Handler("minimoi_guest_requests", "guest.list", frozenset({1}), None, "none"),
    "house_describe_v1": Handler("minimoi_describe_house", "house.describe", frozenset({1}), None, "none"),
    "house_describe_domain_v1": Handler("minimoi_describe_domain", "house.describe_domain", frozenset({1}), None,
                                        "none"),
    "gap_record_v1": Handler("minimoi_record_gap", "work.record_gap", frozenset({2}), "register_readback_v1", "none",
                             "register_withdraw_v1"),
    "gap_withdraw_v1": Handler("minimoi_withdraw_gap", "work.withdraw_gap", frozenset({2}), "register_readback_v1",
                               "none"),
    "github_read_v1": Handler("minimoi_github_issues", "github.issues.read", frozenset({1}), None, "github_read"),
    "guest_reject_v1": Handler("minimoi_guest_request_reject", "guest.reject_request", frozenset({3}),
                               "guest_readback_v1", "none"),
    "issue_comment_v1": Handler("minimoi_issue_comment", "issue.comment", frozenset({3}), "github_receipt_v1",
                                "github_write"),
    "data_repair_ticket_v1": Handler("minimoi_data_repair_ticket", "issue.create_data_repair", frozenset({3}),
                                     "github_receipt_v1", "github_write"),
    "lesson_adopt_v1": Handler("minimoi_lesson_propose", "lessons.adopt", frozenset({3}), "lesson_readback_v1", "none"),
    # Ordinary private actions with read-back and undo: tier 2 (kept at once from Robert's own words, spec §10.2).
    "preference_keep_v1": Handler("minimoi_keep_preference", "lessons.keep_preference", frozenset({2}),
                                  "lesson_readback_v1", "none", "lesson_revoke_v1"),
    "preference_revoke_v1": Handler("minimoi_revoke_preference", "lessons.revoke_preference", frozenset({2}),
                                    "lesson_readback_v1", "none"),
    "research_v1": Handler("minimoi_delegate_research", "delegate.start_research", frozenset({3}),
                           "job_started_v1", "delegation"),
    "access_invite_v1": Handler("minimoi_access_invite", "access.invite_user", frozenset({3}), "account_readback_v1",
                                "email"),
}
# Generated operation facts select explicit code-owned handlers; no policy is enabled here.
from domains.guild.board_operations import operations as _board_operations
for _op in _board_operations():
    if _op.tool:
        HANDLERS[_op.handler] = Handler(_op.tool, _op.capability,
            frozenset({1 if _op.effect == "read" else 2}),
            None if _op.effect == "read" else "board_readback_v1", "none",
            "board_conditional_undo_v1" if _op.undo else None)
HANDLERS["action_clarify_v1"] = Handler("minimoi_clarify_action", "guild.board.clarify", frozenset({1}), None, "none")

# Belt and braces: whatever HANDLERS says, these capability families never go below tier 3.
FLOOR_3 = ("guest.reject", "guest.approve", "access.", "issue.", "delegate.", "deploy.", "repair.", "policy.")


class PolicyInvalid(ActionContractError):
    """The policy cannot be used. A fixed code, never file contents."""


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise PolicyInvalid("policy_duplicate_key")
        out[key] = value
    return out


def _load_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except PolicyInvalid:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise PolicyInvalid("policy_not_json") from None


def digest_of(doc: dict) -> str:
    """The canonical digest: sorted keys, no whitespace, UTF-8. Formatting never changes it; content always does."""
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Rule:
    capability: str
    tier: int
    handler: str
    tool: str
    verify: str | None
    undo: str | None
    egress: str
    max_operations_per_turn: int


@dataclass(frozen=True)
class CompiledPolicy:
    revision: str
    digest: str
    rules: dict                                     # tool name -> Rule
    authority_ref: str

    @property
    def version(self) -> str:
        return f"mc-policy:{self.revision}:{self.digest}"

    def rule_for(self, tool: str) -> Rule | None:
        return self.rules.get(tool)

    @property
    def tools(self) -> tuple:
        """The tools this policy enables, in catalogue order: what the plugin's operator list should name."""
        return tuple(name for name in catalogue.TOOLS if name in self.rules)


def _rule(row) -> Rule:
    if type(row) is not dict or set(row) != _RULE_FIELDS:
        raise PolicyInvalid("policy_rule_fields")
    handler = HANDLERS.get(row["handler"]) if type(row["handler"]) is str else None
    if handler is None:
        raise PolicyInvalid("policy_unknown_handler")
    tier = row["tier"]
    if (row["capability"] != handler.capability or row["version"] != 1 or type(row["version"]) is not int
            or row["actors"] != ["mc"] or row["origins"] != ["owner_turn"]
            or row["source_requirement"] != "current_owner_turn"):
        raise PolicyInvalid("policy_rule_mismatch")
    if type(tier) is not int or tier not in handler.tiers or row["approval"] != _APPROVAL_FOR_TIER.get(tier) \
            or (tier < 3 and handler.capability.startswith(FLOOR_3)):
        raise PolicyInvalid("policy_below_floor")
    if row["verify"] != handler.verify or (tier >= 2 and not handler.verify):
        raise PolicyInvalid("policy_verifier_mismatch")
    if row["undo"] != handler.undo or row["egress"] != handler.egress:
        raise PolicyInvalid("policy_rule_mismatch")
    limit = row["max_operations_per_turn"]
    if type(limit) is not int or not 1 <= limit <= 20:
        raise PolicyInvalid("policy_quota_invalid")
    return Rule(handler.capability, tier, row["handler"], handler.tool, handler.verify, handler.undo, handler.egress,
                limit)


def compile_policy(policy_raw: bytes, authority_raw: bytes) -> CompiledPolicy:
    """The usable policy, or PolicyInvalid. Both files are bytes as read; nothing else is consulted."""
    doc = _load_json(policy_raw)
    if type(doc) is not dict or set(doc) != {"schema", "revision", "rules"} or doc["schema"] != SCHEMA:
        raise PolicyInvalid("policy_schema")
    if type(doc["revision"]) is not str or not _REVISION.fullmatch(doc["revision"]):
        raise PolicyInvalid("policy_revision_invalid")
    if type(doc["rules"]) is not list or not 1 <= len(doc["rules"]) <= 64:
        raise PolicyInvalid("policy_rules_invalid")
    rules = {}
    for row in doc["rules"]:
        rule = _rule(row)
        if rule.tool in rules:
            raise PolicyInvalid("policy_duplicate_rule")
        rules[rule.tool] = rule
    digest = digest_of(doc)
    auth = _load_json(authority_raw)
    if (type(auth) is not dict
            or set(auth) != {"schema", "revision", "digest", "approved_by", "approved_at", "ref"}
            or auth["schema"] != AUTHORITY_SCHEMA or not all(type(auth[k]) is str for k in auth)
            or not auth["approved_by"].strip() or not auth["approved_at"].strip() or not auth["ref"].strip()):
        raise PolicyInvalid("policy_authority_invalid")
    if auth["revision"] != doc["revision"] or not _DIGEST.fullmatch(auth["digest"]) or auth["digest"] != digest:
        raise PolicyInvalid("policy_unapproved")          # this exact file was not approved
    return CompiledPolicy(doc["revision"], digest, rules, auth["ref"][:200])


class PolicySource:
    """The installed policy, re-read when either file changes and swapped whole after validation. ``current()`` is
    (CompiledPolicy, None) or (None, diagnostic code): an invalid file never leaves the previous policy in force."""

    def __init__(self, policy_path, authority_path):
        self.policy_path, self.authority_path = policy_path, authority_path
        self._lock, self._stamp, self._state = threading.Lock(), None, (None, "policy_not_loaded")

    def _read(self, path):
        if not isinstance(path, str) or not os.path.isabs(path):
            raise PolicyInvalid("policy_path_invalid")
        try:
            with open(path, "rb") as f:
                st = os.fstat(f.fileno())
                if st.st_size > 256 * 1024:
                    raise PolicyInvalid("policy_too_large")
                return f.read(), (st.st_ino, st.st_size, st.st_mtime_ns)
        except OSError:
            raise PolicyInvalid("policy_unreadable") from None

    def current(self):
        with self._lock:
            try:
                policy_raw, a = self._read(self.policy_path)
                authority_raw, b = self._read(self.authority_path)
            except PolicyInvalid as exc:
                self._stamp, self._state = None, (None, str(exc))
                return self._state
            if (a, b) != self._stamp:
                try:
                    self._state = (compile_policy(policy_raw, authority_raw), None)
                except PolicyInvalid as exc:
                    self._state = (None, str(exc))
                self._stamp = (a, b)
            return self._state


def _main(argv):
    if len(argv) == 2 and argv[0] == "digest":
        with open(argv[1], "rb") as f:
            doc = _load_json(f.read())
        print(digest_of(doc))
        return 0
    if len(argv) == 3 and argv[0] == "check":
        policy, problem = PolicySource(os.path.abspath(argv[1]), os.path.abspath(argv[2])).current()
        if policy is None:
            print(f"INVALID: {problem}")
            return 1
        print(json.dumps({"version": policy.version, "tools": list(policy.tools),
                          "tiers": {t: r.tier for t, r in policy.rules.items()}}, indent=1))
        return 0
    print(__doc__.split("Operator use:")[1])
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
