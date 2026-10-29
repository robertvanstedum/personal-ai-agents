"""Board v1 canonical descriptions. Registry facts do not grant admission."""
from copy import deepcopy

from core.domain_operations.definitions import Operation, checked

BASE = "/internal/domain-api/v1/guild/board"
from domains.guild.board_contract import LABELS
from domains.guild.board_text import LIMIT, CONVENTION


def obj(properties, required=(), **extra):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False, **extra}


REF = {"type": "string", "minLength": 1, "maxLength": 256}
UUID = {"type": "string", "format": "uuid"}
REV = {"type": "integer", "minimum": 1}
TEXT = {"type": "string", "minLength": 1, "maxLength": 4096,
        "description": "The domain accepts at most 280 Unicode scalar values; never truncate an overlong request."}
LABEL = {"type": ["string", "null"], "enum": [*LABELS, None]}
ITEM = {"type": ["integer", "null"], "minimum": 1}
FIELDS = {"text": TEXT, "label": LABEL, "item_ref": ITEM, "done": {"type": "boolean"}}
CHANGES = obj(FIELDS, minProperties=1)
NOTE = obj({"id": REF, "text": {"type": "string"}, "label": LABEL, "item_ref": ITEM,
            "state": {"enum": ["active", "done", "binned", "unavailable", "purged"]},
            "semantic_revision": REV}, ("id", "state"))
RECEIPT = obj({"operation_id": UUID, "capability": {"type": "string"}, "version": {"const": 1},
               "principal": REF, "executor": REF, "environment": {"enum": ["dev", "prod"]},
               "board": REF, "payload_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
               "target_ref": {"type": ["string", "null"]}, "outcome": {"type": "string"},
               "committed_at": {"type": "string"}, "before_revision": {"type": ["integer", "null"]},
               "after_revision": {"type": ["integer", "null"]},
               "source_ref": REF, "undo_of": {"type": ["string", "null"]},
               "text_scrubbed": {"type": "boolean"}},
              ("operation_id", "capability", "version", "principal", "executor", "environment", "board",
               "payload_sha256", "target_ref", "outcome", "committed_at", "before_revision",
               "after_revision", "source_ref", "undo_of", "text_scrubbed"))
RESULT = obj({"state": {"enum": ["verified", "not_applied", "unknown"]},
              "receipt": {"anyOf": [RECEIPT, {"type": "null"}]},
              "current": {"anyOf": [NOTE, {"type": "null"}]},
              "replayed": {"type": "boolean"}, "code": {"type": "string"}},
             ("state", "receipt", "current", "replayed", "code"))


def operations():
    """Fresh schema graphs; a projection consumer cannot mutate another's copy."""
    query = obj({"query": {"type": "string", "maxLength": 200},
                 "state": {"enum": ["active", "done", "all"], "default": "active"},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                 "cursor": {"type": "string", "minLength": 1, "maxLength": 2048}})
    query_result = obj({"notes": {"type": "array", "items": NOTE, "maxItems": 20},
                        "next_cursor": {"type": ["string", "null"]}, "truncated": {"type": "boolean"},
                        "observed_revision": {"type": "integer", "minimum": 0},
                        "observed_at": {"type": "string"}},
                       ("notes", "next_cursor", "truncated", "observed_revision", "observed_at"))
    create = obj({k: FIELDS[k] for k in ("text", "label", "item_ref")}, ("text",))
    patch = obj({"expected_revision": REV, "changes": CHANGES}, ("expected_revision", "changes"))
    undo = obj({"expected_revision": REV}, ("expected_revision",))
    rows = []

    def add(suffix, capability, method, path, description, args, result, *, model=None,
            effect="read", undo_ref=None):
        rows.append(Operation("guild.board." + suffix, "guild.board." + capability, 1,
                              method, BASE + path, description, args, result,
                              "board_" + suffix.replace(".", "_") + "_v1", ("guild_board_service_v1",),
                              effect, "minimoi_board_" + suffix.replace(".", "_") if model is not None else None,
                              model, undo_ref))

    add("notes.query", "notes.read", "POST", "/notes/query",
        "Find current Board notes by text. Read only; ask which note if results are ambiguous.",
        query, query_result, model=query)
    add("note.read", "notes.read", "GET", "/notes/{note_id}",
        "Read a previously supplied Board note reference and its current revision.",
        obj({}), NOTE, model=obj({"note_ref": REF}, ("note_ref",)))
    add("note.create", "note.create", "POST", "/notes",
        "Save one private text note when the owner asks. Preserve its language; no extra approval. "
        "An overlong note needs a shorter-text offer, never truncation. Discussion alone is not a save request.",
        create, RESULT, model=create, effect="private_write", undo_ref="guild.board.operation.undo")
    add("note.update", "note.update", "PATCH", "/notes/{note_id}",
        "Change specified fields of one referenced Board note, including done true or false. "
        "Read the current note first; clarify ambiguous targets or intervening edits.",
        patch, RESULT, model=obj({"note_ref": REF, "changes": CHANGES}, ("note_ref", "changes")),
        effect="private_write", undo_ref="guild.board.operation.undo")
    add("operation.read", "operations.read", "GET", "/operations/{operation_id}",
        "Recover a prior Board action's receipt and current state. A lost reply never warrants a new create.",
        obj({}), RESULT, model=obj({"operation_ref": REF}, ("operation_ref",)))
    add("operation.undo", "operation.undo", "POST", "/operations/{operation_id}/undo",
        "Undo a specified successful Board creation or update only when the owner requests it. "
        "Later substantive edits cause a conflict; no force option or permanent deletion.",
        undo, RESULT, model=obj({"operation_ref": REF}, ("operation_ref",)), effect="private_write")
    add("capabilities.read", "capabilities.read", "GET", "/capabilities",
        "Describe the authenticated caller's effective Board capabilities; descriptions grant no authority.",
        obj({}), obj({"version": {"const": 1}, "capabilities": {"type": "array", "items": {"type": "string"}},
                      "labels": {"type": "array", "items": {"type": "string"}},
                      "text_limit": {"const": LIMIT}, "count_convention": {"const": CONVENTION}},
                     ("version", "capabilities", "labels", "text_limit", "count_convention")))
    # Optional continuation is conversational state, never owner/turn/grant authority.
    for row in rows:
        if row.tool and row.effect == "private_write":
            row.model_arguments["properties"]["clarification_ref"] = REF
    return deepcopy(checked(rows))
