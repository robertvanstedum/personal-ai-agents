"""Load the frozen CoS contracts (contracts/cos/v1) and validate against one named definition.

The schemas are the runtime truth, not only a test fixture: the command writer refuses anything they refuse. The
folder is found beside the repository root (/app in the CoS images, which copy contracts/cos into the image).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "contracts" / "cos" / "v1"


def contracts_dir() -> Path:
    return Path(os.environ.get("COS_CONTRACTS_DIR") or _DEFAULT_DIR)


@lru_cache(maxsize=4)
def _registry(folder: str) -> tuple[Registry, dict]:
    docs = {}
    for path in sorted(Path(folder).glob("*.schema.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        docs[path.name[: -len(".schema.json")]] = doc
    if "common" not in docs:
        raise RuntimeError(f"CoS contracts not found in {folder}")
    registry = Registry().with_resources((doc["$id"], Resource.from_contents(doc)) for doc in docs.values())
    return registry, docs


@lru_cache(maxsize=64)
def _validator(folder: str, stem: str, definition: str | None) -> Draft202012Validator:
    registry, docs = _registry(folder)
    doc = docs[stem]
    schema = {"$ref": f"{doc['$id']}#/$defs/{definition}"} if definition else doc
    return Draft202012Validator(schema, registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER)


def errors(stem: str, definition: str | None, instance: object) -> list[str]:
    """Up to five readable errors; an empty list means valid."""
    validator = _validator(str(contracts_dir()), stem, definition)
    found = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:200]}" for e in found[:5]]
