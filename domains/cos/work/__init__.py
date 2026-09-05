"""Accumulation reference — the read side of person-owned work.

Career is the first subject this serves, and the only one whose real needs
shaped it. Nothing in this package carries Career vocabulary, a product name,
a model name or a runtime assumption, so a second subject adopts it by
supplying configuration rather than by editing code.

This gate builds only what the first collaboration needs:

* deployment-configured roots, validated fail-closed (:mod:`.roots`);
* path confinement and file gates (:mod:`.confine`);
* the canonical record shapes, read side (:mod:`.records`);
* bounded, provenance-preserving search and read, including the
  subject-scoped approved-output projection (:mod:`.retrieval`);
* the version-1 envelope and closed error vocabulary (:mod:`.envelope`).

It deliberately does not build: a database, a vector index, a scheduler, a
cross-subject registry, automatic extraction, an inferred profile, or any
write path. It is a reference implementation, not a shared framework; it is
promoted to shared platform infrastructure only when a second area has a real
approved artifact and demonstrates the boundary through use.
"""

from __future__ import annotations

from .confine import (
    ALLOWED_EXTENSIONS,
    DEFAULT_MAX_FILE_BYTES,
    ConfinedFile,
    NotFound,
    PathDenied,
    TooLarge,
    UnsupportedMedia,
    confine,
    read_text,
)
from .envelope import (
    CONTEXT_CLASSES,
    EFFECTS,
    ERROR_CODES,
    ISSUE_CODES,
    SEARCH_TRUNCATED,
    WORK_CONTRACT_VERSION,
    InvalidRequest,
    WorkError,
)
from .records import (
    SCHEMA_VERSION,
    ArtifactRef,
    ConversationBinding,
    Disposition,
    SourceRef,
    StaleContext,
    WorkRecord,
    load_work_record,
)
from .retrieval import (
    APPROVED_ROOT_PREFIX,
    DEFAULT_MAX_BYTES_EXAMINED,
    DEFAULT_MAX_FILES_EXAMINED,
    MAX_QUERY_CHARS,
    MAX_QUERY_TOKENS,
    Accumulation,
    ReadOutcome,
    RetrievalIssue,
    SearchHit,
    SearchOutcome,
)
from .roots import (
    ENV_SOURCE_ROOTS,
    ENV_SOURCE_ROOTS_FILE,
    ENV_WORK_ROOT,
    RootConfiguration,
    SourceRoot,
    load_root_configuration,
)

__all__ = [
    "ALLOWED_EXTENSIONS",
    "APPROVED_ROOT_PREFIX",
    "Accumulation",
    "ArtifactRef",
    "CONTEXT_CLASSES",
    "ConfinedFile",
    "ConversationBinding",
    "DEFAULT_MAX_BYTES_EXAMINED",
    "DEFAULT_MAX_FILES_EXAMINED",
    "DEFAULT_MAX_FILE_BYTES",
    "Disposition",
    "EFFECTS",
    "ENV_SOURCE_ROOTS",
    "ENV_SOURCE_ROOTS_FILE",
    "ENV_WORK_ROOT",
    "ERROR_CODES",
    "ISSUE_CODES",
    "InvalidRequest",
    "MAX_QUERY_CHARS",
    "MAX_QUERY_TOKENS",
    "NotFound",
    "PathDenied",
    "ReadOutcome",
    "RetrievalIssue",
    "RootConfiguration",
    "SCHEMA_VERSION",
    "SEARCH_TRUNCATED",
    "SearchHit",
    "SearchOutcome",
    "SourceRef",
    "SourceRoot",
    "StaleContext",
    "TooLarge",
    "UnsupportedMedia",
    "WORK_CONTRACT_VERSION",
    "WorkError",
    "WorkRecord",
    "confine",
    "load_root_configuration",
    "load_work_record",
    "read_text",
]
