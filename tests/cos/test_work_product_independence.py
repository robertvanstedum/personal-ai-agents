"""The reference carries no product, vendor or subject identity.

Mini-moi owns the durable contract; models, runtimes, adapters, channels and
interfaces are replaceable. That is only true if the code cannot name one.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "domains" / "cos" / "work"

#: Vendor and product identifiers that must not appear anywhere.
FORBIDDEN_NAMES = (
    "openclaw",
    "grok",
    "anthropic",
    "claude",
    "codex",
    "xai",
)

#: Modules that would give this package a way to reach a network or a model.
FORBIDDEN_IMPORTS = (
    "socket",
    "http",
    "httpx",
    "requests",
    "urllib",
    "aiohttp",
    "openai",
    "litellm",
)

#: Subject vocabulary that may appear in prose but never in executable code.
SUBJECT_WORDS = ("career", "cover letter", "resume")


def package_files() -> list[Path]:
    files = sorted(PACKAGE.rglob("*.py"))
    assert files, "the package must exist to be checked"
    return files


def test_no_product_or_vendor_identifier_in_package():
    """no vendor or product name appears anywhere in the package"""
    pattern = re.compile("|".join(FORBIDDEN_NAMES), re.IGNORECASE)
    offences = []
    for path in package_files():
        for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if pattern.search(line):
                offences.append(f"{path.name}:{number}: {line.strip()}")
    assert offences == []


def test_package_imports_no_provider_sdk_or_http_client():
    """the package imports nothing that could reach a network"""
    offences = []
    for path in package_files():
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                head = name.split(".")[0]
                if head in FORBIDDEN_IMPORTS:
                    offences.append(f"{path.name}: {name}")
    assert offences == []


def test_no_career_vocabulary_in_executable_code():
    """subject names appear in prose only, never in code

    Docstrings may say that Career is the first subject this serves — that is
    how the reader learns where it came from. Executable code may not, because
    a subject name in a string literal or an identifier is the beginning of a
    subject-specific branch.
    """
    offences = []
    for path in package_files():
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None:
                    docstrings.add(doc)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in docstrings:
                    continue
                lowered = node.value.casefold()
                for word in SUBJECT_WORDS:
                    if word in lowered:
                        offences.append(f"{path.name}:{node.lineno}: string {node.value!r}")
            elif isinstance(node, ast.Name):
                lowered = node.id.casefold()
                for word in SUBJECT_WORDS:
                    if word.replace(" ", "_") in lowered:
                        offences.append(f"{path.name}:{node.lineno}: name {node.id}")
    assert offences == []


@pytest.mark.parametrize("name", FORBIDDEN_NAMES)
def test_forbidden_name_check_would_actually_catch_something(name: str, tmp_path: Path):
    """the grep proves a negative, so prove the grep works"""
    pattern = re.compile("|".join(FORBIDDEN_NAMES), re.IGNORECASE)
    assert pattern.search(f"# built for {name.upper()}") is not None
