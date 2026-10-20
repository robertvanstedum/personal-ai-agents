"""HTTP adapter for the harness learning-eligibility snapshot (harness v0.3.1 §14.6), used by
``consolidation.run_consolidation`` as ``eligibility``. Codex owns the endpoint; this is P-L1's
"eligibility-snapshot adapter".

  GET  /internal/mc/v1/learning/eligible?cursor=<c>&limit=<n>
  GET  /internal/mc/v1/learning/snapshots/<snapshot_id>/sources/<source_id>   (proposed path)

Proposed wire details, recorded for Codex rather than silently assumed:
* ``cursor``: the decimal commit sequence of the last source this producer consumed (absent at
  the start). The job may take only a prefix of a page (token bound), so it resumes from a
  source's own ``seq``, not from the page's ``next_cursor``.
* A source read returns ``{"text": "..."}``; 410 means revoked (no text), 404 missing.

Validation is strict and fails closed: unknown fields, a page longer than requested, sources not
in strictly increasing ``seq`` after the cursor, bad digests or oversize bodies raise
``LearningUnavailable``; nothing partial is returned. The token comes from ``token_source()``
per call and is never logged or returned.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from .consolidation import Revoked

ELIGIBLE = "/internal/mc/v1/learning/eligible"
SOURCES = "/internal/mc/v1/learning/snapshots/{snapshot}/sources/{source}"
MAX_PAGE_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 256 * 1024
MAX_REFS = 50
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_PAGE_KEYS = {"snapshot_id", "sources", "learning_refs", "policy_version", "next_cursor", "coverage"}
_SOURCE_KEYS = {"id", "edition", "digest", "seq"}


class LearningUnavailable(RuntimeError):
    """The snapshot could not be read or failed validation; nothing is consumed."""


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class EligibilityClient:
    def __init__(self, base_url: str, token_source, *, timeout_s: float = 10.0, opener=None):
        if not base_url.startswith(("http://", "https://")) or "@" in base_url:
            raise ValueError("learning_url_invalid")
        self.base = base_url.rstrip("/")
        self._token = token_source
        self.timeout = timeout_s
        self._open = opener or urllib.request.build_opener(urllib.request.HTTPHandler,
                                                           urllib.request.HTTPSHandler).open

    def _get(self, path: str, limit_bytes: int):
        req = urllib.request.Request(self.base + path, method="GET")
        req.add_header("Authorization", f"Bearer {self._token()}")
        try:
            with self._open(req, timeout=self.timeout) as resp:
                raw = resp.read(limit_bytes + 1)
        except urllib.error.HTTPError as err:
            return err.code, None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionResetError, OSError):
            raise LearningUnavailable("learning_unreachable") from None
        if len(raw) > limit_bytes:
            raise LearningUnavailable("response_too_large")
        try:
            doc = json.loads(raw)
        except ValueError:
            raise LearningUnavailable("response_not_json") from None
        if not isinstance(doc, dict):
            raise LearningUnavailable("response_not_object")
        return 200, doc

    def eligible(self, cursor, limit: int) -> dict:
        if not _int(limit) or not 1 <= limit <= 10:
            raise ValueError("limit_invalid")
        if cursor is not None and (not _int(cursor) or cursor < 0):
            raise ValueError("cursor_invalid")
        query = {"limit": str(limit)}
        if cursor is not None:
            query["cursor"] = str(cursor)
        status, doc = self._get(f"{ELIGIBLE}?{urllib.parse.urlencode(query)}", MAX_PAGE_BYTES)
        if status != 200:
            raise LearningUnavailable(f"eligible_http_{status}")
        if set(doc) != _PAGE_KEYS:
            raise LearningUnavailable("page_fields_invalid")
        if not isinstance(doc["snapshot_id"], str) or not _ID.match(doc["snapshot_id"]):
            raise LearningUnavailable("snapshot_id_invalid")
        if doc["coverage"] not in ("complete", "partial"):
            raise LearningUnavailable("coverage_invalid")
        if not isinstance(doc["policy_version"], str) or not _ID.match(doc["policy_version"]):
            raise LearningUnavailable("policy_version_invalid")
        sources = doc["sources"]
        if not isinstance(sources, list) or len(sources) > limit:
            raise LearningUnavailable("sources_invalid")
        last = cursor
        for s in sources:
            if not isinstance(s, dict) or set(s) != _SOURCE_KEYS or not isinstance(s["id"], str) \
                    or not _ID.match(s["id"]) or not isinstance(s["edition"], str) or not _ID.match(s["edition"]) \
                    or not isinstance(s["digest"], str) or not _SHA.match(s["digest"]) or not _int(s["seq"]):
                raise LearningUnavailable("source_invalid")
            if last is not None and s["seq"] <= last:
                raise LearningUnavailable("sources_not_in_commit_order")
            last = s["seq"]
        refs = doc["learning_refs"]
        if not isinstance(refs, list) or len(refs) > MAX_REFS \
                or not all(isinstance(r, str) and _ID.match(r) for r in refs):
            raise LearningUnavailable("learning_refs_invalid")
        return doc

    def read(self, snapshot_id: str, source_id: str):
        if not _ID.match(snapshot_id or "") or not _ID.match(source_id or ""):
            raise ValueError("ref_invalid")
        path = SOURCES.format(snapshot=urllib.parse.quote(snapshot_id, safe=""),
                              source=urllib.parse.quote(source_id, safe=""))
        status, doc = self._get(path, MAX_SOURCE_BYTES)
        if status == 410:
            raise Revoked()
        if status == 404:
            return None                              # recorded as a "missing" disposition
        if status != 200:
            raise LearningUnavailable(f"source_http_{status}")
        if set(doc) != {"text"} or not isinstance(doc["text"], str):
            raise LearningUnavailable("source_body_invalid")
        return doc["text"]
