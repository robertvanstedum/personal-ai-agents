"""The closed contract: eight effects, no paths, no chosen provenance.

The rules tested here are the ones that make the folder the interface rather
than a detail. A caller names a root and a relative path, or a handle already
in the record; it never names a filesystem location, never chooses where its
output lands, and never says what its bytes are.
"""

from __future__ import annotations

import pytest

from domains.cos.work import grants, service
from domains.cos.work.envelope import EFFECTS, new_operation_id

DIGEST = "c" * 64


def code_of(response):
    assert response["ok"] is False, response
    return response["error"]["code"]


#: §2.2, restated as data so the service's own table can be compared with it.
DESIGN_MATRIX = {
    "open_work": (
        {"subject"},
        {"work_id", "label", "intent", "conversation_id"},
        (),
        True,
    ),
    "attach_source": (
        {"work_id"},
        {"origin_note", "filename_hint"},
        ({"content"}, {"file_ref"}),
        True,
    ),
    "search_sources": (
        {"work_id", "query"},
        {
            "root_refs",
            "max_results",
            "max_excerpt_chars",
            "max_files_examined",
            "max_bytes_examined",
        },
        (),
        False,
    ),
    "read_source": ({"work_id"}, set(), ({"source_ref"}, {"file_ref"}), False),
    "write_artifact": ({"work_id", "content"}, {"based_on"}, (), True),
    "request_disposition": ({"work_id", "proposed_state"}, {"artifact_ref"}, (), True),
    "record_disposition": (
        {"work_id", "pending_id", "confirmed_state"},
        {"reason"},
        (),
        True,
    ),
    "use_robert_edit": (
        {"work_id", "supersedes_ref", "expected_sha256"},
        set(),
        ({"content"}, {"file_ref"}),
        True,
    ),
}

#: §2.2's grant column, likewise.
DESIGN_GRANTS = {
    "open_work": {frozenset({"work_id"}), frozenset({"allow_create", "operation_id"})},
    "attach_source": {
        frozenset({"work_id", "root_refs", "relative_path"}),
        frozenset({"work_id", "source_class", "content_sha256", "content_bytes"}),
    },
    "search_sources": {frozenset({"work_id", "root_refs"})},
    "read_source": {
        frozenset({"work_id", "source_ref"}),
        frozenset({"work_id", "root_refs", "relative_path"}),
    },
    "write_artifact": {frozenset({"work_id", "content_sha256", "content_bytes"})},
    "request_disposition": {
        frozenset({"work_id"}),
        frozenset({"work_id", "artifact_ref"}),
    },
    "record_disposition": {frozenset({"work_id", "pending_id"})},
    "use_robert_edit": {
        frozenset(
            {
                "work_id",
                "supersedes_ref",
                "expected_sha256",
                "root_refs",
                "relative_path",
                "expected_input_sha256",
            }
        ),
        frozenset(
            {"work_id", "supersedes_ref", "expected_sha256", "content_sha256", "content_bytes"}
        ),
    },
}


def test_eight_effects_are_served(work_service):
    """each effect in the closed set reaches a handler"""
    assert set(work_service._handlers()) == set(EFFECTS)
    assert len(EFFECTS) == 8


def test_no_ninth_effect(work_service, work_adapter):
    """the handler table's keys are exactly the closed set"""
    assert set(work_service._handlers()) == set(service.EFFECT_SPECS)
    for absent in ("list_work", "delete_work", "send", "export", "schedule", "delegate"):
        assert absent not in EFFECTS
        response = work_adapter.invoke(
            {
                "work_contract_version": 1,
                "operation_id": new_operation_id(),
                "grant_ref": None,
                "effect": absent,
                "params": {},
            }
        )
        assert code_of(response) == "invalid_request"


def test_effect_matrix_matches_the_service():
    """the service's table and the reviewed matrix are the same table"""
    assert set(service.EFFECT_SPECS) == set(DESIGN_MATRIX)
    for effect, (required, optional, exclusive, writes) in DESIGN_MATRIX.items():
        spec = service.EFFECT_SPECS[effect]
        assert set(spec.required) == required
        assert set(spec.optional) == optional
        assert {frozenset(group) for group in spec.exclusive} == {
            frozenset(group) for group in exclusive
        }
        assert spec.writes is writes
    assert set(grants.GRANT_BINDINGS) == set(DESIGN_GRANTS)
    for effect, variants in DESIGN_GRANTS.items():
        assert set(grants.GRANT_BINDINGS[effect]) == variants


def test_contract_version_mismatch_fails_closed(work_adapter, flow):
    """a request from another contract version is not served"""
    work_id = flow.started()
    response = work_adapter.invoke(
        {
            "work_contract_version": 2,
            "operation_id": new_operation_id(),
            "grant_ref": None,
            "effect": "read_source",
            "params": {"work_id": work_id, "source_ref": "src-0001"},
        }
    )
    assert code_of(response) == "contract_version_unsupported"


@pytest.mark.parametrize("effect", ["attach_source", "read_source", "use_robert_edit"])
def test_absolute_path_rejected_in_every_file_ref(effect, flow):
    """no effect taking a file reference accepts an absolute path"""
    work_id = flow.started()
    grant = flow.mint(
        "read_source", work_id=work_id, root_refs=["authored"], relative_path="answers.md"
    )
    params = {
        "work_id": work_id,
        "file_ref": {"root_ref": "authored", "relative_path": "/etc/passwd"},
    }
    if effect == "use_robert_edit":
        params["supersedes_ref"] = "art-0001"
        params["expected_sha256"] = DIGEST
    response = flow.call(effect, params, grant=grant)
    assert code_of(response) in ("path_denied", "grant_effect_mismatch")


def test_caller_cannot_choose_output_path(flow):
    """there is no output-path parameter, and derived paths stay in their subtrees"""
    work_id = flow.started()
    grant = flow.mint(
        "write_artifact",
        work_id=work_id,
        content_sha256=flow._sha("x"),
        content_bytes=1,
    )
    response = flow.call(
        "write_artifact",
        {"work_id": work_id, "content": "x", "output_path": "../escape.md"},
        grant=grant,
    )
    assert code_of(response) == "invalid_request"
    assert "output_path" in response["error"]["message"]

    written = flow.write(work_id, "A first draft.\n")
    assert written["result"]["relative_path"].startswith("artifacts/")
    attached = flow.attach_inline(work_id, "Some supplied text.\n")
    assert attached["result"]["relative_path"].startswith("sources/")


def test_parameter_limits_refused_not_clamped(flow):
    """over-long input is refused, never shortened"""
    work_id = flow.started()
    long_label = "l" * (service.MAX_LABEL_CHARS + 1)
    operation_id = new_operation_id()
    grant = flow.mint("open_work", allow_create=True, operation_id=operation_id)
    assert code_of(
        flow.call(
            "open_work",
            {"subject": "career", "label": long_label},
            grant=grant,
            operation_id=operation_id,
        )
    ) == "invalid_request"

    grant = flow.mint(
        "attach_source",
        work_id=work_id,
        source_class="external_source",
        content_sha256=flow._sha("t"),
        content_bytes=1,
    )
    assert code_of(
        flow.call(
            "attach_source",
            {"work_id": work_id, "content": "t", "origin_note": "n" * 201},
            grant=grant,
        )
    ) == "invalid_request"

    grant = flow.mint(
        "write_artifact", work_id=work_id, content_sha256=flow._sha("t"), content_bytes=1
    )
    assert code_of(
        flow.call(
            "write_artifact",
            {
                "work_id": work_id,
                "content": "t",
                "based_on": [
                    {"ref": f"src-{index:04d}", "sha256": DIGEST}
                    for index in range(service.MAX_BASED_ON_ENTRIES + 1)
                ],
            },
            grant=grant,
        )
    ) == "invalid_request"

    assert code_of(flow.search(work_id, "q" * 300)) == "invalid_request"


def test_provenance_is_not_a_caller_choice(flow):
    """what a capture is recorded as is never a request field"""
    for spec in service.EFFECT_SPECS.values():
        assert "context_class" not in spec.known

    work_id = flow.started()
    grant = flow.mint(
        "attach_source",
        work_id=work_id,
        source_class="external_source",
        content_sha256=flow._sha("t"),
        content_bytes=1,
    )
    assert code_of(
        flow.call(
            "attach_source",
            {"work_id": work_id, "content": "t", "context_class": "robert_source"},
            grant=grant,
        )
    ) == "invalid_request"

    drafted = flow.write(work_id, "A machine draft.\n")
    assert drafted["result"]["context_class"] == "agent_draft"
    edited = flow.edit_inline(
        work_id,
        "A machine draft, corrected by hand.\n",
        "art-0001",
        drafted["result"]["sha256"],
    )
    assert edited["result"]["context_class"] == "coauthored_output"


def test_attach_source_class_comes_from_the_root(flow):
    """a captured file is stored as the class its root declares"""
    work_id = flow.started()
    authored = flow.attach_file(work_id, "authored", "answers.md")
    assert authored["result"]["context_class"] == "robert_source"
    posting = flow.attach_file(work_id, "postings", "operations-lead.txt")
    assert posting["result"]["context_class"] == "external_source"

    before = sorted(p.name for p in (flow.work_dir(work_id) / "sources").iterdir())
    refused = flow.attach_file(work_id, "drafts", "scratch.md")
    assert code_of(refused) == "invalid_request"
    assert sorted(p.name for p in (flow.work_dir(work_id) / "sources").iterdir()) == before


def test_attach_source_inline_class_comes_from_the_grant(flow):
    """identical bytes under two grants store the two classes the grants fixed"""
    work_id = flow.started()
    text = "The same sentence, twice.\n"
    first = flow.attach_inline(work_id, text, source_class="external_source")
    second = flow.attach_inline(work_id, text, source_class="robert_source")
    assert first["result"]["context_class"] == "external_source"
    assert second["result"]["context_class"] == "robert_source"
    assert first["result"]["sha256"] == second["result"]["sha256"]


def test_no_laundering_into_robert_or_coauthored(flow, issuer):
    """no combination moves bytes into a class they did not come from"""
    work_id = flow.started()
    text = "External material.\n"

    for data_class in ("private_personal", "external_public"):
        for root_ref, expected in (
            ("authored", "robert_source"),
            ("postings", "external_source"),
            ("drafts", None),
        ):
            grant = issuer.mint(
                effect="attach_source",
                subject="career",
                conversation_id="owner",
                work_id=work_id,
                root_refs=[root_ref],
                relative_path=(
                    "answers.md"
                    if root_ref == "authored"
                    else "operations-lead.txt"
                    if root_ref == "postings"
                    else "scratch.md"
                ),
                data_class=data_class,
            )
            response = flow.call(
                "attach_source",
                {
                    "work_id": work_id,
                    "file_ref": {
                        "root_ref": root_ref,
                        "relative_path": grant.bound("relative_path"),
                    },
                },
                grant=grant,
            )
            if expected is None:
                assert code_of(response) == "invalid_request"
            else:
                assert response["result"]["context_class"] == expected

    # inline: an external-public turn cannot even mint the class
    from domains.cos.work.envelope import InvalidRequest

    with pytest.raises(InvalidRequest):
        issuer.mint(
            effect="attach_source",
            subject="career",
            conversation_id="owner",
            work_id=work_id,
            source_class="robert_source",
            content_sha256=flow._sha(text),
            content_bytes=len(text.encode()),
            data_class="external_public",
        )

    # and nothing stored is co-authored without an edit
    from domains.cos.work import store

    record = store.read_record(store.WorkPaths(directory=flow.work_dir(work_id)))[0]
    assert all(entry.context_class != "coauthored_output" for entry in record.artifacts)


def test_write_artifact_note_is_unknown_param(flow):
    """there is nowhere in the record for a note, so there is no note"""
    work_id = flow.started()
    grant = flow.mint(
        "write_artifact", work_id=work_id, content_sha256=flow._sha("t"), content_bytes=1
    )
    response = flow.call(
        "write_artifact", {"work_id": work_id, "content": "t", "note": "why"}, grant=grant
    )
    assert code_of(response) == "invalid_request"
    assert "note" in response["error"]["message"]


def test_no_lookup_by_label(flow, issuer):
    """a work item is reached by its identifier, or it is created

    There is deliberately no lookup by label. Two items may honestly carry
    the same one, and quietly picking between them is exactly the ambiguity
    this contract refuses to have — which is why the code reserved for that
    ambiguity is raised by nothing in the package.
    """
    first = flow.started(label="An item")
    again = flow.create(label="An item")
    assert again["ok"] is True
    assert again["result"]["work_id"] != first

    operation_id = new_operation_id()
    grant = flow.mint("open_work", work_id=first)
    response = flow.call(
        "open_work",
        {"subject": "career", "label": "An item"},
        grant=grant,
        operation_id=operation_id,
    )
    assert response["ok"] is False

    from pathlib import Path

    import domains.cos.work as package
    from domains.cos.work import envelope

    assert "ambiguous_work" in envelope.RESERVED_ERROR_CODES
    root = Path(package.__file__).parent
    for path in sorted(root.rglob("*.py")):
        if path.name == "envelope.py":
            continue
        assert "ambiguous_work" not in path.read_text("utf-8")


def test_no_subject_branch_in_service():
    """no subject name appears in the service, the store or the grant issuer"""
    from pathlib import Path

    import domains.cos.work as package

    root = Path(package.__file__).parent
    for name in ("service.py", "store.py", "grants.py", "adapter.py", "approval.py"):
        text = (root / name).read_text("utf-8").casefold()
        for word in ("career", "resume", "cover letter"):
            assert word not in text, f"{name} names {word}"
