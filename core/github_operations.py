"""GitHub issue operations for MC (harness v0.3.1 §7 catalogue: github.issues.search/get/comment/
create), one fixed repository, behind the public-publication rule (BP-2: L2 exact preview).

Reads: bounded search and get. Writes are two steps:

  prepare_comment / prepare_create  → a Proposal: the exact text that would be published, its
                                     target, and a sha256 digest. Nothing is sent.
  execute(proposal, approved_digest) → publishes only if the owner approved exactly that digest.

Duplicate protection without an idempotency API: every published body carries a hidden marker
``<!-- mc-op:<operation id> -->``. Before posting, and after any unconfirmed send, the operation
reads back recent items for that marker; a found marker is ``already_applied``, never a second
post. Text is screened for secrets and local paths before it can be proposed (the repository is
public). The token is supplied by the caller per call and never logged or returned.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

API = "https://api.github.com"
MAX_QUERY = 200
MAX_RESULTS = 20
MAX_BODY = 60_000
MAX_TITLE = 200
_OP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,99}$")
_REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
# Things that must never reach a public issue, even if the owner's text quotes them.
_UNSAFE = (
    ("secret_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}")),
    ("secret_key", re.compile(r"\b(?:sk|xai|AKIA)[-_A-Za-z0-9]{16,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("local_path", re.compile(r"(?:/Users/|/home/|/private/|/opt/minimoi/)[^\s)]*")),
    ("connection_string", re.compile(r"\b(?:postgres(?:ql)?|mysql|redis)://\S+")),
)


class GitHubOpError(RuntimeError):
    """Fixed code; never includes the token or the upstream response body."""


@dataclass(frozen=True)
class Proposal:
    operation_id: str
    kind: str                       # comment | create
    repository: str
    issue_number: int | None
    title: str | None
    body: str                       # exactly what will be published, marker included
    digest: str
    findings: tuple = ()            # safety findings; a proposal with findings cannot execute


@dataclass(frozen=True)
class Outcome:
    status: str                     # done | already_applied | refused | not_found | unknown
    code: str = ""
    url: str | None = None
    number: int | None = None
    verified_state: dict = field(default_factory=dict)


def _marker(operation_id: str) -> str:
    return f"<!-- mc-op:{operation_id} -->"


def screen(text: str) -> tuple:
    return tuple(name for name, pattern in _UNSAFE if pattern.search(text or ""))


def _digest(kind, repository, number, title, body) -> str:
    return hashlib.sha256(json.dumps([kind, repository, number, title, body]).encode()).hexdigest()


class GitHubIssues:
    def __init__(self, repository: str, transport, token_source):
        """``transport(method, url, headers, params, json_body) -> (status, json)``;
        ``token_source()`` returns the repository-scoped token at call time."""
        if not _REPO.match(repository):
            raise ValueError("repository_invalid")
        self.repository = repository
        self._transport = transport
        self._token = token_source

    def _call(self, method, path, *, params=None, body=None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "Authorization": f"Bearer {self._token()}"}
        try:
            status, data = self._transport(method, f"{API}{path}", headers, params or {}, body)
        except TimeoutError:
            raise GitHubOpError("github_timeout") from None
        except Exception:
            raise GitHubOpError("github_unavailable") from None
        return status, data

    # ── reads ────────────────────────────────────────────────────────────
    def search(self, query: str, *, state: str = "open") -> dict:
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY:
            raise GitHubOpError("query_invalid")
        if state not in ("open", "closed", "all"):
            raise GitHubOpError("state_invalid")
        terms = " ".join(query.split()).replace('"', "")
        q = f'repo:{self.repository} is:issue {"" if state == "all" else "is:" + state} {terms}'.strip()
        status, data = self._call("GET", "/search/issues", params={"q": q, "per_page": MAX_RESULTS})
        if status != 200 or not isinstance(data, dict):
            raise GitHubOpError(f"github_status_{status}")
        items = [{"number": i.get("number"), "title": i.get("title"), "state": i.get("state"),
                  "url": i.get("html_url"), "updated_at": i.get("updated_at")}
                 for i in data.get("items", [])[:MAX_RESULTS] if "pull_request" not in i]
        return {"items": items, "total": data.get("total_count"),
                "complete": not data.get("incomplete_results", False)}

    def get(self, number: int) -> dict | None:
        number = _number(number)
        status, data = self._call("GET", f"/repos/{self.repository}/issues/{number}")
        if status == 404:
            return None
        if status != 200 or not isinstance(data, dict):
            raise GitHubOpError(f"github_status_{status}")
        return {"number": data.get("number"), "title": data.get("title"), "state": data.get("state"),
                "url": data.get("html_url"), "body": data.get("body") or "",
                "comments": data.get("comments")}

    # ── writes: prepare (no effect) ──────────────────────────────────────
    def prepare_comment(self, number: int, text: str, *, operation_id: str) -> Proposal:
        number = _number(number)
        body = self._body(text, operation_id)
        return Proposal(operation_id, "comment", self.repository, number, None, body,
                        _digest("comment", self.repository, number, None, body), screen(text))

    def prepare_create(self, title: str, text: str, *, operation_id: str) -> Proposal:
        if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE:
            raise GitHubOpError("title_invalid")
        title = " ".join(title.split())
        body = self._body(text, operation_id)
        return Proposal(operation_id, "create", self.repository, None, title, body,
                        _digest("create", self.repository, None, title, body), screen(title + "\n" + text))

    def _body(self, text, operation_id) -> str:
        if not isinstance(operation_id, str) or not _OP_ID.match(operation_id):
            raise GitHubOpError("operation_id_invalid")
        if not isinstance(text, str) or not text.strip():
            raise GitHubOpError("text_empty")
        body = f"{text.strip()}\n\n{_marker(operation_id)}"
        if len(body) > MAX_BODY:
            raise GitHubOpError("text_too_long")
        return body

    # ── writes: execute (only the approved digest) ───────────────────────
    def execute(self, proposal: Proposal, approved_digest: str) -> Outcome:
        if proposal.findings:
            return Outcome("refused", "unsafe_content:" + ",".join(proposal.findings))
        if proposal.repository != self.repository:
            return Outcome("refused", "repository_mismatch")
        expected = _digest(proposal.kind, proposal.repository, proposal.issue_number, proposal.title,
                           proposal.body)
        if approved_digest != proposal.digest or expected != proposal.digest:
            return Outcome("refused", "approval_does_not_match_proposal")
        try:
            found = self._find_marker(proposal)
        except GitHubOpError as exc:
            return Outcome("unknown", f"readback_failed:{exc}")
        if found:
            return Outcome("already_applied", "marker_found", **found)
        try:
            if proposal.kind == "comment":
                status, data = self._call("POST", f"/repos/{self.repository}/issues/{proposal.issue_number}/comments",
                                          body={"body": proposal.body})
            else:
                status, data = self._call("POST", f"/repos/{self.repository}/issues",
                                          body={"title": proposal.title, "body": proposal.body})
        except GitHubOpError:
            # May or may not have been created: reconcile by marker, never blind retry.
            try:
                found = self._find_marker(proposal)
            except GitHubOpError:
                return Outcome("unknown", "send_unconfirmed")
            return Outcome("already_applied", "marker_found_after_uncertain_send", **found) if found \
                else Outcome("unknown", "send_unconfirmed")
        if status == 404 and proposal.kind == "comment":
            return Outcome("not_found", "issue_not_found")
        if status not in (200, 201) or not isinstance(data, dict):
            return Outcome("refused", f"github_status_{status}")
        url = data.get("html_url")
        number = data.get("number") if proposal.kind == "create" else proposal.issue_number
        return Outcome("done", proposal.kind, url, number,
                       {"url": url, "marker": _marker(proposal.operation_id), "kind": proposal.kind})

    def _find_marker(self, proposal: Proposal) -> dict | None:
        marker = _marker(proposal.operation_id)
        if proposal.kind == "comment":
            status, data = self._call("GET", f"/repos/{self.repository}/issues/{proposal.issue_number}/comments",
                                      params={"per_page": 100, "sort": "created", "direction": "desc"})
            if status == 404:
                return None
            if status != 200 or not isinstance(data, list):
                raise GitHubOpError(f"github_status_{status}")
            hit = next((c for c in data if marker in (c.get("body") or "")), None)
            return {"url": hit.get("html_url"), "number": proposal.issue_number} if hit else None
        status, data = self._call("GET", f"/repos/{self.repository}/issues",
                                  params={"state": "all", "per_page": 50, "sort": "created", "direction": "desc"})
        if status != 200 or not isinstance(data, list):
            raise GitHubOpError(f"github_status_{status}")
        hit = next((i for i in data if marker in (i.get("body") or "")), None)
        return {"url": hit.get("html_url"), "number": hit.get("number")} if hit else None


def _number(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10**9:
        raise GitHubOpError("issue_number_invalid")
    return value
