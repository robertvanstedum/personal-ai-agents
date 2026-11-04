"""Load contracts/partnership/v1 and validate against one named definition. The schemas are the runtime truth."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "contracts" / "partnership" / "v1"
SPEC_SHA256 = "191df665c2fa006c14e51e53a6bddff353be7dd4ec08cd74ab7101479fd4130e"


def contracts_dir() -> Path:
    return Path(os.environ.get("PARTNERSHIP_CONTRACTS_DIR") or _DEFAULT_DIR)


@lru_cache(maxsize=4)
def _registry(folder: str) -> tuple[Registry, dict]:
    docs = {}
    for path in sorted(Path(folder).glob("*.schema.json")):
        docs[path.name[: -len(".schema.json")]] = json.loads(path.read_text(encoding="utf-8"))
    if "common" not in docs:
        raise RuntimeError(f"partnership contracts not found in {folder}")
    registry = Registry().with_resources((d["$id"], Resource.from_contents(d)) for d in docs.values())
    return registry, docs


@lru_cache(maxsize=128)
def _validator(folder: str, stem: str, definition: str) -> Draft202012Validator:
    registry, docs = _registry(folder)
    schema = {"$ref": f"{docs[stem]['$id']}#/$defs/{definition}"}
    return Draft202012Validator(schema, registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER)


def errors(stem: str, definition: str, instance: object) -> list[str]:
    """Up to five readable errors; an empty list means valid."""
    found = sorted(_validator(str(contracts_dir()), stem, definition).iter_errors(instance),
                   key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:200]}" for e in found[:5]]


def kinds() -> list[str]:
    return list(_registry(str(contracts_dir()))[1]["command"]["$defs"]["kind"]["enum"])
