"""Shared fixtures for the accumulation-reference tests.

Everything here works on copies in a temporary directory. Committed fixture
trees cannot carry owner-private directory modes through Git, and the roots
under test must be owner-private, so each test copies what it needs and sets
the modes itself.

Temporary paths are canonicalised first: on macOS the system temporary
directory reaches through ``/var -> /private/var``, and a root with a
symbolic link at any component is refused by design.

All fixture content is synthetic: invented people, employers and postings.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "work"


def make_private(path: Path) -> Path:
    """Make ``path`` and everything under it owner-private."""
    os.chmod(path, 0o700)
    for child in path.rglob("*"):
        if child.is_symlink():
            continue
        os.chmod(child, 0o700 if child.is_dir() else 0o600)
    return path


def copy_private(source: Path, destination: Path) -> Path:
    """Copy a fixture tree and make the copy owner-private."""
    shutil.copytree(source, destination, symlinks=False)
    return make_private(destination)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A canonical, symlink-free temporary directory."""
    resolved = Path(os.path.realpath(tmp_path))
    make_private(resolved)
    return resolved


@pytest.fixture
def fixture_root() -> Path:
    """The committed, synthetic fixture tree (read only)."""
    return FIXTURE_ROOT


@pytest.fixture
def career_sources(workspace: Path) -> Path:
    """An owner-private copy of the synthetic Career source tree."""
    return copy_private(FIXTURE_ROOT / "sources" / "career", workspace / "career-sources")


@pytest.fixture
def decision_memo_sources(workspace: Path) -> Path:
    """An owner-private copy of the synthetic decision-memo source tree."""
    return copy_private(
        FIXTURE_ROOT / "sources" / "decision_memo", workspace / "decision-memo-sources"
    )


@pytest.fixture
def private_work_root(workspace: Path) -> Path:
    """An owner-private copy of the synthetic canonical work tree."""
    return copy_private(FIXTURE_ROOT / "work_root", workspace / "work-root")


def declaration(mapping: dict, default_class: str = "robert_source") -> str:
    """Render the deployment declaration for authorized read-only roots.

    Every root carries an explicit provenance class. A value may be given as a
    plain path — which takes ``default_class`` — or as a ``(path, class)``
    pair, so a test can declare a root of any of the four classes.
    """
    document: dict[str, dict[str, dict[str, str]]] = {}
    for subject, refs in mapping.items():
        entries: dict[str, dict[str, str]] = {}
        for ref, value in refs.items():
            if isinstance(value, tuple):
                path, context_class = value
            else:
                path, context_class = value, default_class
            entries[ref] = {"path": str(path), "context_class": context_class}
        document[subject] = entries
    return json.dumps(document)


@pytest.fixture
def declare_source_roots():
    """Build the deployment declaration for authorized read-only roots."""
    return declaration


@pytest.fixture
def career_env(private_work_root: Path, career_sources: Path, declare_source_roots):
    """A complete, valid environment for the synthetic Career subject."""
    from domains.cos.work.roots import ENV_SOURCE_ROOTS, ENV_WORK_ROOT

    return {
        ENV_WORK_ROOT: str(private_work_root),
        ENV_SOURCE_ROOTS: declare_source_roots(
            {
                "career": {
                    "resumes": career_sources / "resumes",
                    "other-responses": career_sources / "other-responses",
                    "base-letters": career_sources / "base-letters",
                }
            }
        ),
    }


@pytest.fixture
def career_accumulation(career_env):
    """The accumulation reference, configured for the Career fixtures."""
    from domains.cos.work.retrieval import Accumulation
    from domains.cos.work.roots import load_root_configuration

    configuration = load_root_configuration(career_env)
    assert configuration.issues == ()
    return Accumulation(configuration)


@pytest.fixture
def temp_git_repo(workspace: Path):
    """Create a throwaway Git checkout so in-checkout root rules are testable."""

    created: dict[str, Path] = {}

    def build(name: str = "checkout") -> Path:
        if name in created:
            return created[name]
        repo = workspace / name
        repo.mkdir()
        make_private(repo)
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
        created[name] = repo
        return repo

    return build


# ---------------------------------------------------------------------------
# W0b — the Work service.
#
# Everything below is additive: no fixture above is changed. All content is
# synthetic (invented people, employers and postings), extending the same
# committed fixture tree, and the whole W0b suite runs with network access
# patched to fail.
# ---------------------------------------------------------------------------

W0B_MODULES = (
    "test_work_service_contract",
    "test_work_receipt_envelope",
    "test_work_grants",
    "test_work_transaction",
    "test_work_open_work_idempotency",
    "test_work_recovery",
    "test_work_disposition",
    "test_work_robert_edit",
    "test_work_receipts_sanitized",
    "test_work_adapter_removal",
    "test_work_based_on_approved",
    "test_work_subjects_synthetic",
    "test_work_no_network",
)


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Make every socket in the W0b suite fail.

    Approval sends nothing, retrieval reads only local files, and the whole
    service is provider-neutral by contract. The cheapest way to keep that
    true is to remove the ability to reach a network at all while these tests
    run, so a regression that reached one would fail rather than succeed
    quietly.
    """
    module = request.module.__name__.rsplit(".", 1)[-1]
    if module not in W0B_MODULES:
        return

    import socket
    import ssl

    def refuse(*args, **kwargs):
        raise OSError("the Work foundation makes no network connections")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", refuse)


@pytest.fixture
def synthetic_roots(workspace: Path) -> Path:
    """Two synthetic subjects' read-only roots, of three provenance classes."""
    base = workspace / "roots"
    tree = {
        "authored": (
            "robert_source",
            {
                "current-resume.md": (
                    "# Robin Ashgrove\n\n"
                    "Closed a reconciliation gap at Quayside Logistics.\n"
                ),
                "answers.md": "I prefer written handovers to standing meetings.\n",
            },
        ),
        "postings": (
            "external_source",
            {
                "operations-lead.txt": (
                    "Quayside Logistics is hiring an operations lead.\n"
                    "You will own reconciliation across three depots.\n"
                ),
            },
        ),
        "drafts": (
            "agent_draft",
            {"scratch.md": "An earlier machine draft nobody approved.\n"},
        ),
        "memo-notes": (
            "external_source",
            {
                "vendor-notes.txt": "Vendor A quotes less than Vendor B for the same scope.\n"
            },
        ),
    }
    for name, (_class, files) in tree.items():
        directory = base / name
        directory.mkdir(parents=True, exist_ok=True)
        for filename, body in files.items():
            (directory / filename).write_text(body, "utf-8")
    make_private(base)
    return base


@pytest.fixture
def work_root(workspace: Path) -> Path:
    """An empty, owner-private canonical write root."""
    root = workspace / "work-root"
    root.mkdir()
    make_private(root)
    return root


@pytest.fixture
def work_env(work_root: Path, synthetic_roots: Path, declare_source_roots) -> dict:
    """A complete environment for two subjects that share no code path."""
    from domains.cos.work.roots import ENV_SOURCE_ROOTS, ENV_WORK_ROOT

    return {
        ENV_WORK_ROOT: str(work_root),
        ENV_SOURCE_ROOTS: declare_source_roots(
            {
                "career": {
                    "authored": (synthetic_roots / "authored", "robert_source"),
                    "postings": (synthetic_roots / "postings", "external_source"),
                    "drafts": (synthetic_roots / "drafts", "agent_draft"),
                },
                "decision-memo": {
                    "memo-notes": (synthetic_roots / "memo-notes", "external_source"),
                },
            }
        ),
    }


@pytest.fixture
def work_service(work_env):
    """The Work service, with the lazy-recovery age gate open for tests."""
    from domains.cos.work.service import WorkService

    return WorkService(env=work_env, recovery_min_age_seconds=0)


@pytest.fixture
def work_adapter(work_service):
    """The product-free in-process adapter over that service."""
    from domains.cos.work.adapter import InProcessWorkAdapter

    return InProcessWorkAdapter(work_service)


@pytest.fixture
def issuer(work_service):
    """The in-process grant issuer the service was built with."""
    return work_service.issuer


class Flow:
    """A small driver that mints the right grant for each call.

    Tests that are about something other than authority should not have to
    restate the binding table every time; tests that *are* about authority
    mint their own grants instead of using this.
    """

    def __init__(self, service, subject="career", conversation_id="owner"):
        import hashlib

        from domains.cos.work.adapter import InProcessWorkAdapter

        self.service = service
        self.issuer = service.issuer
        self.adapter = InProcessWorkAdapter(service)
        self.subject = subject
        self.conversation_id = conversation_id
        self._sha = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()

    def new_operation_id(self):
        from domains.cos.work.envelope import new_operation_id

        return new_operation_id()

    def call(self, effect, params, *, grant, operation_id=None):
        return self.adapter.call(
            effect, params, operation_id=operation_id, grant_ref=grant.grant_ref
        )

    def mint(self, effect, **bindings):
        return self.issuer.mint(
            effect=effect,
            subject=self.subject,
            conversation_id=self.conversation_id,
            **bindings,
        )

    def create(self, label="Operations lead", intent="Show a closed gap.", operation_id=None,
               conversation_id=..., **extra):
        operation_id = operation_id or self.new_operation_id()
        grant = self.mint("open_work", allow_create=True, operation_id=operation_id)
        params = {"subject": self.subject, "label": label, "intent": intent}
        if conversation_id is ...:
            conversation_id = self.conversation_id
        if conversation_id is not None:
            params["conversation_id"] = conversation_id
        params.update(extra)
        return self.call("open_work", params, grant=grant, operation_id=operation_id)

    def open_existing(self, work_id, *, conversation_id=..., operation_id=None):
        operation_id = operation_id or self.new_operation_id()
        grant = self.mint("open_work", work_id=work_id)
        params = {"subject": self.subject, "work_id": work_id}
        if conversation_id is ...:
            conversation_id = self.conversation_id
        if conversation_id is not None:
            params["conversation_id"] = conversation_id
        return self.call("open_work", params, grant=grant, operation_id=operation_id)

    def attach_file(self, work_id, root_ref, relative_path, *, operation_id=None, **extra):
        grant = self.mint(
            "attach_source",
            work_id=work_id,
            root_refs=[root_ref],
            relative_path=relative_path,
        )
        params = {
            "work_id": work_id,
            "file_ref": {"root_ref": root_ref, "relative_path": relative_path},
        }
        params.update(extra)
        return self.call("attach_source", params, grant=grant, operation_id=operation_id)

    def attach_inline(self, work_id, content, source_class="external_source",
                      data_class="private_personal", operation_id=None, **extra):
        grant = self.mint(
            "attach_source",
            work_id=work_id,
            source_class=source_class,
            content_sha256=self._sha(content),
            content_bytes=len(content.encode("utf-8")),
            data_class=data_class,
        )
        params = {"work_id": work_id, "content": content}
        params.update(extra)
        return self.call("attach_source", params, grant=grant, operation_id=operation_id)

    def search(self, work_id, query, root_refs=None, *, operation_id=None, **extra):
        available = list(self.service.accumulation.available_root_refs(self.subject))
        grant = self.mint(
            "search_sources", work_id=work_id, root_refs=root_refs or available
        )
        params = {"work_id": work_id, "query": query}
        if root_refs is not None:
            params["root_refs"] = list(root_refs)
        params.update(extra)
        return self.call("search_sources", params, grant=grant, operation_id=operation_id)

    def read_file(self, work_id, root_ref, relative_path, *, operation_id=None):
        grant = self.mint(
            "read_source",
            work_id=work_id,
            root_refs=[root_ref],
            relative_path=relative_path,
        )
        return self.call(
            "read_source",
            {
                "work_id": work_id,
                "file_ref": {"root_ref": root_ref, "relative_path": relative_path},
            },
            grant=grant,
            operation_id=operation_id,
        )

    def read_captured(self, work_id, source_ref, *, operation_id=None):
        grant = self.mint("read_source", work_id=work_id, source_ref=source_ref)
        return self.call(
            "read_source",
            {"work_id": work_id, "source_ref": source_ref},
            grant=grant,
            operation_id=operation_id,
        )

    def write(self, work_id, content, based_on=None, *, operation_id=None):
        grant = self.mint(
            "write_artifact",
            work_id=work_id,
            content_sha256=self._sha(content),
            content_bytes=len(content.encode("utf-8")),
        )
        params = {"work_id": work_id, "content": content}
        if based_on is not None:
            params["based_on"] = list(based_on)
        return self.call("write_artifact", params, grant=grant, operation_id=operation_id)

    def edit_inline(self, work_id, content, supersedes_ref, expected_sha256, *, operation_id=None):
        grant = self.mint(
            "use_robert_edit",
            work_id=work_id,
            supersedes_ref=supersedes_ref,
            expected_sha256=expected_sha256,
            content_sha256=self._sha(content),
            content_bytes=len(content.encode("utf-8")),
        )
        return self.call(
            "use_robert_edit",
            {
                "work_id": work_id,
                "content": content,
                "supersedes_ref": supersedes_ref,
                "expected_sha256": expected_sha256,
            },
            grant=grant,
            operation_id=operation_id,
        )

    def edit_file(self, work_id, root_ref, relative_path, supersedes_ref, expected_sha256,
                  expected_input_sha256, *, operation_id=None):
        grant = self.mint(
            "use_robert_edit",
            work_id=work_id,
            supersedes_ref=supersedes_ref,
            expected_sha256=expected_sha256,
            root_refs=[root_ref],
            relative_path=relative_path,
            expected_input_sha256=expected_input_sha256,
        )
        return self.call(
            "use_robert_edit",
            {
                "work_id": work_id,
                "file_ref": {"root_ref": root_ref, "relative_path": relative_path},
                "supersedes_ref": supersedes_ref,
                "expected_sha256": expected_sha256,
            },
            grant=grant,
            operation_id=operation_id,
        )

    def propose(self, work_id, proposed_state, artifact_ref=None, *, operation_id=None):
        bindings = {"work_id": work_id}
        if artifact_ref is not None:
            bindings["artifact_ref"] = artifact_ref
        grant = self.mint("request_disposition", **bindings)
        params = {"work_id": work_id, "proposed_state": proposed_state}
        if artifact_ref is not None:
            params["artifact_ref"] = artifact_ref
        return self.call("request_disposition", params, grant=grant, operation_id=operation_id)

    def decide(self, work_id, pending_id, confirmed_state, reason=None, *, operation_id=None):
        grant = self.mint("record_disposition", work_id=work_id, pending_id=pending_id)
        params = {
            "work_id": work_id,
            "pending_id": pending_id,
            "confirmed_state": confirmed_state,
        }
        if reason is not None:
            params["reason"] = reason
        return self.call("record_disposition", params, grant=grant, operation_id=operation_id)

    # -- convenience -------------------------------------------------

    def started(self, **kwargs):
        """A created work item's id."""
        response = self.create(**kwargs)
        assert response["ok"], response["error"]
        return response["result"]["work_id"]

    def work_dir(self, work_id):
        return self.service.store.find_work_directory(self.subject, work_id)


@pytest.fixture
def flow(work_service):
    """A driver for the first synthetic subject."""
    return Flow(work_service)


@pytest.fixture
def memo_flow(work_service):
    """The same driver for a second subject, with different roots."""
    return Flow(work_service, subject="decision-memo", conversation_id="memo-owner")


def tree_snapshot(root):
    """Every name, mode, size and digest under ``root``, for equality checks."""
    import hashlib
    import os as _os

    entries = {}
    for directory, dirnames, filenames in _os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(directory) / name
            relative = str(path.relative_to(root))
            raw = path.read_bytes()
            entries[relative] = (
                _os.stat(path).st_mode,
                len(raw),
                hashlib.sha256(raw).hexdigest(),
            )
        for name in sorted(dirnames):
            path = Path(directory) / name
            entries[str(path.relative_to(root)) + "/"] = ("dir",)
    return entries


@pytest.fixture
def snapshot_tree():
    """Compare a whole subject tree before and after an operation."""
    return tree_snapshot


class Crash(RuntimeError):
    """A failure injected at one exact step of a transaction."""


@pytest.fixture
def crash_at(monkeypatch):
    """Fail at one named step, the way a power cut would.

    The step names are the transaction's own; nothing is simulated and no
    alternative code path is taken, so what the truth tables are tested
    against is the real sequence with one real interruption in it.
    """
    from domains.cos.work import store

    def install(step, *, times=1, matcher=None):
        state = {"seen": 0}

        def hook(name):
            hit = matcher(name) if matcher is not None else name == step
            if hit:
                state["seen"] += 1
                if state["seen"] <= times:
                    raise Crash(name)

        monkeypatch.setattr(store, "_checkpoint", hook)
        return state

    return install


@pytest.fixture
def uninjected(monkeypatch):
    """Put the transaction back to its ordinary, uninterrupted form."""
    from domains.cos.work import store

    def restore():
        monkeypatch.setattr(store, "_checkpoint", lambda step: None)

    return restore
