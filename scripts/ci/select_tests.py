#!/usr/bin/env python3
"""Choose the tests CI runs for a set of changed paths: a focused set, or the whole suite.

The whole suite (``tests/``) runs whenever a focused set could miss something:

* a full release (classify_release.py found an unknown path, the deploy
  pipeline itself, or a rewritten history), a manual run, or the nightly run;
* a change to the test harness itself (the root conftest, requirements.test.txt);
* a changed path that is not documentation and that no test can be tied to.

Otherwise the focused set is the always-run core (the release and deploy
guards) plus, for every changed path:

* a changed test file itself, and a changed conftest's whole directory;
* every test that imports a changed Python module, directly or through other
  repository modules (the reverse import graph, read with ``ast``; bare names
  such as ``from html_server import app`` resolve to every repository module of
  that name, so sys.path tricks over-select rather than miss);
* a fixture in a conftest whose body imports the module selects the tests in
  that directory that name the fixture;
* every test whose text names the changed file (its path, or its basename when
  that is unique in the repository), for tests that read a file, run a script,
  or check a workflow rather than import code;
* for a file that is not Python, the files that use its name (a Dockerfile,
  a template, a workflow, a string in a module; comments and docstrings do not
  count) are its readers: the tests that name a reader or import a reader
  module directly are selected too.

The nightly workflow (.github/workflows/nightly-tests.yml) runs the whole suite
on main every night, so anything a focused set does miss surfaces within a day.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FULL = ("tests/",)

# The release and deploy guards: they read the workflow, the classifier, the
# scoped deploy and production Compose, and scan the repository for ownership,
# so they protect every release whatever it touches.
CORE_TESTS = (
    "tests/test_release_classifier.py",
    "tests/test_select_tests.py",
    "tests/test_release_base.py",
    "tests/test_document_release_pipeline.py",
    "tests/test_immutable_production_deploy.py",
    "tests/test_deploy_keeps_live_queue.py",
    "tests/test_guild_queue_deploy_config.py",
)

# The test harness: every test depends on these, so a change runs everything.
HARNESS = frozenset({"tests/conftest.py", "requirements.test.txt", "pytest.ini", "pyproject.toml", "setup.cfg", "conftest.py"})

# Documentation needs no test of its own; a test that names a document is still selected.
DOC_PREFIXES = ("docs/", "prototype-lab/", "planning-studio/", "_working/")
DOC_SUFFIXES = (".md", ".pdf")

# Files that may name another file and so be its reader (a template naming a
# stylesheet, a Dockerfile naming a script, a workflow naming a Compose file).
# One hop: a reader's own readers rarely depend on the changed file's content
# (every staging script sources lib.sh, which names production Compose).
READER_SUFFIXES = (".py", ".sh", ".yml", ".yaml", ".html", ".js", ".mjs", ".ts", ".css", ".toml", ".ini", ".cfg", ".conf", ".sql")
READER_DEPTH = 1

# Past this share of all test files, the whole suite costs little more.
FULL_SHARE = 0.6


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") and name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def is_doc(path: str) -> bool:
    return path.startswith(DOC_PREFIXES) or path.endswith(DOC_SUFFIXES)


def module_name(path: str) -> str:
    dotted = path[:-3].replace("/", ".")
    return dotted[: -len(".__init__")] if dotted.endswith(".__init__") else dotted


def _prefixes(dotted: str) -> list[str]:
    parts = dotted.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


class Repo:
    """The tracked files at HEAD, their imports, and their text (read once, on demand)."""

    def __init__(self, root: Path = ROOT, files: list[str] | None = None):
        self.root = root
        self.files = files if files is not None else subprocess.run(
            ["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()
        self.tests = sorted(path for path in self.files if is_test_file(path) and not path.startswith("tests/smoke/"))
        python = [path for path in self.files if path.endswith(".py")]
        self.modules = {module_name(path): path for path in python}
        self.top_level = {name.split(".")[0] for name in self.modules}
        self.by_stem: dict[str, set[str]] = defaultdict(set)
        for name in self.modules:
            self.by_stem[name.rsplit(".", 1)[-1]].add(name)
        self.basename_count: dict[str, int] = defaultdict(int)
        for path in self.files:
            self.basename_count[path.rsplit("/", 1)[-1]] += 1
        self._text: dict[str, str] = {}
        self._code: dict[str, str] = {}
        # importers[module] = {(file, fixture-or-None)}: file loads module (a conftest per fixture).
        self.importers: dict[str, set[tuple[str, str | None]]] = defaultdict(set)
        for path in python:
            for fixture, names in self._imports(path).items():
                for name in names:
                    self.importers[name].add((path, fixture))

    def text(self, path: str) -> str:
        if path not in self._text:
            try:
                self._text[path] = (self.root / path).read_text(errors="ignore")
            except OSError:
                self._text[path] = ""
        return self._text[path]

    def code_text(self, path: str) -> str:
        """What a reader actually uses: a module's string literals (not its docstrings), or a file without its comment lines."""
        if path not in self._code:
            text = self.text(path)
            if path.endswith(".py"):
                try:
                    tree = ast.parse(text)
                except (SyntaxError, ValueError):
                    self._code[path] = text
                    return text
                docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                              if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                              and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
                text = "\n".join(node.value for node in ast.walk(tree)
                                  if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings)
            elif path.endswith((".sh", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf")) or "/Dockerfile" in "/" + path:
                text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
            self._code[path] = text
        return self._code[path]

    def _imports(self, path: str) -> dict[str | None, set[str]]:
        """Module names the file may load, keyed by conftest fixture (None: the module itself)."""
        try:
            tree = ast.parse(self.text(path))
        except (SyntaxError, ValueError):
            return {}
        package = module_name(path) if path.endswith("__init__.py") else module_name(path).rpartition(".")[0]
        directory = path.rpartition("/")[0]
        units: dict[str | None, list[ast.AST]] = {None: [tree]}
        if path.endswith("conftest.py"):
            units = {None: [node for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]}
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    units[node.name] = [node]
        found: dict[str | None, set[str]] = {}
        for unit, nodes in units.items():
            names: set[str] = set()
            for top in nodes:
                for node in ast.walk(top):
                    for raw in self._raw_names(node, package):
                        names.update(self._resolve(raw, directory))
            found[unit] = names
        return found

    @staticmethod
    def _raw_names(node: ast.AST, package: str) -> list[str]:
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(part for part in (*parts, base) if part)
            return [base, *(f"{base}.{alias.name}" if base else alias.name for alias in node.names)]
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            arg = node.args[0]
            if called in ("import_module", "__import__") and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return [arg.value]
        return []

    def _resolve(self, raw: str, directory: str) -> set[str]:
        if not raw:
            return set()
        head = raw.split(".")[0]
        if head in self.top_level:
            return set(_prefixes(raw))
        if head in sys.stdlib_module_names:
            return set()
        # A bare name: a sibling module, or any repository module of that name
        # (a sys.path insert elsewhere); over-selecting is the safe direction.
        names: set[str] = set()
        rest = raw.split(".")[1:]
        candidates = set(self.by_stem.get(head, ()))
        sibling = (directory.replace("/", ".") + "." + head) if directory else head
        if sibling in self.modules:
            candidates.add(sibling)
        for candidate in candidates:
            names.update(_prefixes(".".join([candidate, *rest])))
        return names

    def names_file(self, text: str, path: str) -> bool:
        """The text names the file: its path, or its basename as a whole word (beside its folder's name when shared)."""
        if path in text:
            return True
        base = path.rsplit("/", 1)[-1]
        if base not in text or not re.search(rf"(?<![\w.-]){re.escape(base)}(?![\w-])", text):
            return False
        if self.basename_count.get(base, 0) <= 1:
            return True
        parent = path.rpartition("/")[0].rsplit("/", 1)[-1]
        return bool(parent) and re.search(rf"(?<![\w.-]){re.escape(parent)}(?![\w-])", text) is not None


def _fixture_users(repo: Repo, conftest: str, fixture: str) -> set[str]:
    directory = conftest.rpartition("/")[0]
    word = re.compile(rf"\b{re.escape(fixture)}\b")
    return {test for test in repo.tests if test.startswith(directory + "/") and word.search(repo.text(test))}


def tests_for(repo: Repo, path: str) -> tuple[set[str], bool]:
    """(tests, needs_full) for one changed path."""
    if path in HARNESS:
        return set(), True
    selected: set[str] = set()
    if is_test_file(path):
        if not (repo.root / path).exists():
            return set(), False                    # a deleted test runs nowhere
        selected.add(path)
    elif path.endswith("conftest.py"):
        selected.add(path.rpartition("/")[0] + "/")
    # Walk the readers: modules import the changed module, files name the changed file.
    seen_modules: set[str] = set()
    seen_files = {path}
    frontier = [(path, 0)]
    while frontier:
        current, depth = frontier.pop()
        for test in repo.tests:
            if test != current and repo.names_file(repo.text(test), current):
                selected.add(test)
        if current.endswith(".py"):
            stack = [module_name(current)]
            while stack:
                name = stack.pop()
                if name in seen_modules:
                    continue
                seen_modules.add(name)
                for importer, fixture in repo.importers.get(name, ()):
                    if importer.endswith("conftest.py"):
                        if fixture is None:
                            selected.add(importer.rpartition("/")[0] + "/")
                        else:
                            selected.update(_fixture_users(repo, importer, fixture))
                    elif is_test_file(importer):
                        selected.add(importer)
                    elif depth == 0 or importer.startswith("tests/"):
                        # A module that only names a changed file (a reader) is
                        # covered by the tests that import it; its own importers
                        # are not, or a message naming a file selects a domain.
                        stack.append(module_name(importer))
        elif depth < READER_DEPTH and not is_doc(current):
            for reader in repo.files:
                if reader in seen_files or is_doc(reader) or reader.startswith("tests/"):
                    continue
                if not (reader.endswith(READER_SUFFIXES) or reader.rsplit("/", 1)[-1].startswith("Dockerfile")):
                    continue
                if repo.names_file(repo.code_text(reader), current):
                    seen_files.add(reader)
                    frontier.append((reader, depth + 1))
    if "tests/" in selected:                       # the root conftest loads it: every test may
        return set(), True
    return selected, not selected and not is_doc(path)


def select(paths: list[str], release_class: str, event: str = "push", repo: Repo | None = None) -> dict:
    """{"test_scope": "full"|"focused", "test_targets": [...], "reason": str}."""
    paths = [item.strip() for item in paths if item.strip()]
    if event in ("schedule", "workflow_dispatch"):
        return _full(f"{event} run")
    if release_class == "full":
        return _full("full release")
    repo = repo or Repo()
    selected = {test for test in CORE_TESTS if (repo.root / test).is_file()}
    for path in paths:
        tests, needs_full = tests_for(repo, path)
        if needs_full:
            return _full(f"no focused tests for {path}" if path not in HARNESS else f"test harness changed: {path}")
        selected |= tests
    files = {entry for entry in selected if not entry.endswith("/")}
    dirs = sorted(entry for entry in selected if entry.endswith("/"))
    files = {entry for entry in files if not any(entry.startswith(d) for d in dirs)}
    covered = sum(1 for test in repo.tests if test in files or any(test.startswith(d) for d in dirs))
    if repo.tests and covered > FULL_SHARE * len(repo.tests):
        return _full(f"focused set is {covered} of {len(repo.tests)} test files")
    return {"test_scope": "focused", "test_targets": dirs + sorted(files),
            "reason": f"{covered} of {len(repo.tests)} test files for {len(paths)} changed paths"}


def _full(reason: str) -> dict:
    return {"test_scope": "full", "test_targets": list(FULL), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--release-class", required=True)
    parser.add_argument("--event", default="push")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    paths = args.paths or sys.stdin.read().splitlines()
    result = select(paths, args.release_class, args.event)
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"test_scope={result['test_scope']}\n")
            stream.write(f"test_targets={' '.join(result['test_targets'])}\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
