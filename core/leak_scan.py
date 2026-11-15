"""Scan the public repo for material that should have stayed private.

Spec: leak scan v0.2. The private tree is read only on this machine. The report
is written only inside that tree. Stdout is counts. A notification is the count
and the report path, never a matched span.

  python -m core.leak_scan --public REPO --private DIR --baseline
  python -m core.leak_scan --public REPO --private DIR --watch
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from utils.credential_scrub import SECRET

BOILERPLATE_AT = 10
NAME_AT = 3
IDENTIFIER_MIN = 6
QUOTE_MAX = 240
LOCATIONS_MAX = 20
BLOB_MAX = 5_000_000
PRODUCT_WORDS = frozenset({
    "minimoi", "claude", "codex", "grok", "openclaw", "github", "aws", "mc",
})
PROSE_SUFFIX = {".md", ".txt", ".rst", ".text", ".markdown"}
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "vendor", "__pycache__", "leak-scan"}
LOCK_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "composer.lock",
}
_WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")
_CAPWORD = re.compile(r"\b[A-Z][a-z]+\b")


class ScanError(RuntimeError):
    pass


@dataclass
class Criteria:
    passages: set[bytes]
    names: set[str]
    voice: tuple[str, ...]
    identifiers: tuple[str, ...]
    identifiers_loaded: int = 0
    identifiers_rejected: int = 0
    voice_loaded: int = 0


@dataclass
class Found:
    check: str
    digest: str
    quote: str | None = None
    locations: list[str] = field(default_factory=list)
    extra: int = 0


@dataclass
class Summary:
    mode: str
    hits: int
    by_check: dict[str, int]
    suppressed: int
    report: Path | None
    notified: bool
    telegram: str
    mc_note: str
    skipped_large: int
    shallow: bool = False


def span_hash(span: str) -> str:
    return hashlib.sha256(span.encode("utf-8")).hexdigest()


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def _product_span(span: str) -> bool:
    return any(part.lower() in PRODUCT_WORDS for part in span.split())


def _name_spans(text: str) -> list[str]:
    """Every 2- and 3-word capitalized run, including both pairs inside a 3-word run."""
    matches = list(_CAPWORD.finditer(text))
    spans: list[str] = []
    index = 0
    while index < len(matches):
        end = index
        while end + 1 < len(matches) and text[matches[end].end():matches[end + 1].start()].strip() == "":
            end += 1
        words = [matches[pos].group(0) for pos in range(index, end + 1)]
        for size in (2, 3):
            if len(words) < size:
                continue
            for start in range(0, len(words) - size + 1):
                spans.append(" ".join(words[start:start + size]))
        index = end + 1
    return spans


def _rotations(span: str) -> tuple[str, ...]:
    parts = span.split(" ")
    if len(parts) != 8:
        return (span,)
    return tuple(" ".join(parts[shift:] + parts[:shift]) for shift in range(8))


def _lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def load_identifiers(private: Path) -> tuple[tuple[str, ...], int, int]:
    kept, rejected = [], 0
    for line in _lines(private / "leak-scan" / "identifiers.txt"):
        if len(line) < IDENTIFIER_MIN:
            rejected += 1
            continue
        kept.append(line)
    return tuple(kept), len(kept), rejected


def load_voice(private: Path) -> tuple[str, ...]:
    return tuple(_lines(private / "leak-scan" / "voice.txt"))


def load_allowlist(private: Path) -> set[tuple[str, str]]:
    path = private / "leak-scan" / "allowlist.jsonl"
    if not path.is_file():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        check, digest = row.get("check"), row.get("hash")
        if check in ("passage", "name", "voice", "identifier", "key") and isinstance(digest, str) and len(digest) == 64:
            out.add((check, digest))
    return out


def _prose_files(private: Path) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(private):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name in LOCK_NAMES:
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() in PROSE_SUFFIX:
                found.append(path)
    return found


def _is_blob(repo: Path, oid: str) -> bool:
    probe = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", oid],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def build_criteria(private: Path, public: Path) -> Criteria:
    counts: Counter[str] = Counter()
    names: Counter[str] = Counter()
    for path in _prose_files(private):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            continue
        oid = subprocess.check_output(["git", "hash-object", "--no-filters", "--", str(path)], text=True).strip()
        if _is_blob(public, oid):
            continue
        text = raw.decode("utf-8", "replace")
        words = _words(text)
        for i in range(0, len(words) - 7):
            counts[" ".join(words[i:i + 8])] += 1
        for span in _name_spans(text):
            if not _product_span(span):
                names[span] += 1
    boilerplate: set[str] = set()
    for span, n in counts.items():
        if n >= BOILERPLATE_AT:
            boilerplate.update(_rotations(span))
    passages = {hashlib.sha256(span.encode()).digest() for span in counts if span not in boilerplate}
    kept_names = {span for span, n in names.items() if n >= NAME_AT}
    voice = load_voice(private)
    identifiers, loaded, rejected = load_identifiers(private)
    return Criteria(passages, kept_names, voice, identifiers, loaded, rejected, len(voice))


def _add(found: dict[tuple[str, str], Found], check: str, span: str, location: str, quote: str | None) -> None:
    digest = span_hash(span)
    key = (check, digest)
    row = found.get(key)
    if row is None:
        row = Found(check, digest, quote if quote is None else quote[:QUOTE_MAX])
        found[key] = row
    if location in row.locations:
        return
    if len(row.locations) >= LOCATIONS_MAX:
        row.extra += 1
        return
    row.locations.append(location)


def scan_text(text: str, criteria: Criteria, location: str, key_re=SECRET) -> dict[tuple[str, str], Found]:
    found: dict[tuple[str, str], Found] = {}
    if not text:
        return found
    words = _words(text)
    for i in range(0, len(words) - 7):
        span = " ".join(words[i:i + 8])
        if hashlib.sha256(span.encode()).digest() in criteria.passages:
            _add(found, "passage", span, location, span)
    for span in _name_spans(text):
        if span in criteria.names:
            _add(found, "name", span, location, span)
    lowered = text.lower()
    for phrase in criteria.voice:
        if phrase.lower() in lowered:
            _add(found, "voice", phrase, location, phrase)
    for ident in criteria.identifiers:
        if re.search(r"(?<!\w)" + re.escape(ident) + r"(?!\w)", text):
            _add(found, "identifier", ident, location, None)
    for match in key_re.finditer(text):
        _add(found, "key", match.group(0), location, None)
    return found


def _merge(into: dict[tuple[str, str], Found], more: dict[tuple[str, str], Found]) -> None:
    for key, row in more.items():
        have = into.get(key)
        if have is None:
            into[key] = row
            continue
        for loc in row.locations:
            if loc in have.locations:
                continue
            if len(have.locations) >= LOCATIONS_MAX:
                have.extra += 1
            else:
                have.locations.append(loc)
        have.extra += row.extra


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True, errors="replace", stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        raise ScanError(exc.stderr.strip() or "git failed") from exc


def _ref_args(repo: Path) -> list[str]:
    """Public history is what origin has. A repo with no origin (the tests) uses local branches."""
    probe = subprocess.run(
        ["git", "-C", str(repo), "for-each-ref", "--format=%(refname)", "refs/remotes/origin"],
        capture_output=True, text=True,
    )
    if probe.returncode == 0 and probe.stdout.strip():
        return ["--remotes=origin"]
    return ["--branches"]


def assert_full_clone(repo: Path) -> None:
    if _git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise ScanError("The public clone is shallow. Fetch the full history before scanning.")


def walk_git(repo: Path, on_text) -> int:
    """Call on_text(location, text) for commit messages and text blobs. Return skipped blobs."""
    assert_full_clone(repo)
    refs = _ref_args(repo)
    log = _git(repo, "log", *refs, "--pretty=format:%H%x1f%B%x1e")
    for chunk in log.split("\x1e"):
        if "\x1f" not in chunk:
            continue
        oid, body = chunk.split("\x1f", 1)
        oid = oid.strip()
        if oid:
            on_text(f"commit:{oid[:12]}", body)
    listing = _git(repo, "rev-list", "--objects", *refs)
    blobs: list[tuple[str, str]] = []
    for line in listing.splitlines():
        oid, _, path = line.partition(" ")
        if path and Path(path).name not in LOCK_NAMES:
            blobs.append((oid, path))
    skipped = 0
    if not blobs:
        return skipped
    proc = subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout
    try:
        for oid, path in blobs:
            proc.stdin.write(f"{oid}\n".encode())
            proc.stdin.flush()
            header = proc.stdout.readline().decode("utf-8", "replace")
            if header.endswith(" missing\n") or " missing" in header:
                continue
            parts = header.split()
            if len(parts) < 3:
                raise ScanError("git cat-file returned a short header.")
            size = int(parts[2])
            data = proc.stdout.read(size)
            proc.stdout.read(1)  # trailing newline
            if parts[1] != "blob":
                continue
            if size > BLOB_MAX or b"\0" in data[:8192]:
                skipped += 1
                continue
            on_text(f"blob:{path}@{oid[:12]}", data.decode("utf-8", "replace"))
    finally:
        proc.stdin.close()
        proc.wait()
    return skipped


def github_slug(repo: Path) -> str:
    url = _git(repo, "remote", "get-url", "origin").strip()
    if url.endswith(".git"):
        url = url[:-4]
    marker = "github.com"
    if marker not in url:
        raise ScanError("The public repo has no GitHub origin.")
    slug = url.split(marker, 1)[1].lstrip(":/")
    owner, _, name = slug.partition("/")
    if not owner or not name or "/" in name:
        raise ScanError("The public repo has no GitHub origin.")
    return f"{owner}/{name}"


def _gh_pages(path: str) -> list:
    try:
        raw = subprocess.check_output(["gh", "api", "--paginate", "--slurp", path], text=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        raise ScanError("GitHub read failed.") from exc
    pages = json.loads(raw)
    rows = []
    for page in pages:
        if isinstance(page, list):
            rows.extend(page)
        elif isinstance(page, dict):
            rows.append(page)
    return rows


def iter_github_texts(slug: str) -> list[tuple[str, str]]:
    owner, name = slug.split("/", 1)
    texts: list[tuple[str, str]] = []
    for item in _gh_pages(f"repos/{slug}/issues?state=all&per_page=100"):
        number = item.get("number")
        kind = "pull" if isinstance(item, dict) and item.get("pull_request") else "issue"
        body = f"{item.get('title') or ''}\n{item.get('body') or ''}"
        texts.append((f"{kind}:{number}", body))
    for item in _gh_pages(f"repos/{slug}/issues/comments?per_page=100"):
        texts.append((f"issue-comment:{item.get('id')}", item.get("body") or ""))
    for item in _gh_pages(f"repos/{slug}/pulls/comments?per_page=100"):
        texts.append((f"pull-comment:{item.get('id')}", item.get("body") or ""))
    cursor = None
    while True:
        args = ["gh", "api", "graphql", "-f", f"owner={owner}", "-f", f"name={name}", "-f", "query=" + _REVIEW_QUERY]
        if cursor:
            args.extend(["-f", f"cursor={cursor}"])
        try:
            raw = subprocess.check_output(args, text=True, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as exc:
            raise ScanError("GitHub read failed.") from exc
        data = json.loads(raw)["data"]["repository"]["pullRequests"]
        for pr in data["nodes"]:
            for review in pr["reviews"]["nodes"]:
                if review.get("body"):
                    texts.append((f"review:{pr['number']}", review["body"]))
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]
    return texts


_REVIEW_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: 40, after: $cursor, states: [OPEN, CLOSED, MERGED]) {
      pageInfo { hasNextPage endCursor }
      nodes { number reviews(first: 50) { nodes { body } } }
    }
  }
}
"""


def write_report(private: Path, mode: str, rows: list[Found], *, suppressed: int, skipped_large: int,
                 criteria: Criteria, telegram: str, mc_note: str, now: datetime) -> Path:
    folder = private / "leak-scan" / "reports"
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = folder / f"{now.strftime('%Y-%m-%dT%H%M%SZ')}.md"
    lines = [
        "# Leak scan",
        "",
        f"mode: {mode}",
        f"at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"hits: {len(rows)}",
        f"suppressed: {suppressed}",
        f"skipped_large: {skipped_large}",
        f"identifiers: {criteria.identifiers_loaded}",
        f"identifiers_rejected: {criteria.identifiers_rejected}",
        f"voice_phrases: {criteria.voice_loaded}",
        f"telegram: {telegram}",
        f"mc_note: {mc_note}",
        "",
    ]
    for row in rows:
        lines.append(f"## {row.check} {row.digest}")
        lines.append("")
        for loc in row.locations:
            lines.append(f"- {loc}")
        if row.extra:
            lines.append(f"- {row.extra} more locations")
        if row.quote:
            lines.append("")
            lines.append(row.quote)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def notice(count: int, report: Path) -> str:
    return f"Leak scan: {count} new hits. Report: {report}"


def send_telegram(text: str) -> bool:
    """Send by the system bot for this role. Never logs the token or the chat id."""
    from utils.telegram import get_chat_id, get_system_token
    token, chat = get_system_token(), get_chat_id()
    if not token or not chat:
        return False
    payload = json.dumps({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def write_mc_note(text: str, root: str | None) -> bool:
    from core.mc_notes import register
    try:
        register.add(root, by="grok", said_by="grok", what_for="remember", topic="leak scan", text=text)
    except Exception:
        return False
    return True


def _counts(rows: list[Found]) -> dict[str, int]:
    out = {name: 0 for name in ("passage", "name", "voice", "identifier", "key")}
    for row in rows:
        out[row.check] = out.get(row.check, 0) + 1
    return out


def run(public: Path, private: Path, mode: str, *, github: bool = True, now: datetime | None = None,
        key_re=SECRET, telegram=None, mc_note=None, notes_root: str | None = None) -> Summary:
    if mode not in ("baseline", "watch"):
        raise ScanError("Mode is baseline or watch.")
    if not (public / ".git").exists() and not (public / ".git").is_file():
        raise ScanError("The public path is not a git repo.")
    if not private.is_dir():
        raise ScanError("The private tree is not on this machine. It was not copied.")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    criteria = build_criteria(private, public)
    found: dict[tuple[str, str], Found] = {}

    def take(location: str, text: str) -> None:
        _merge(found, scan_text(text, criteria, location, key_re))

    skipped = walk_git(public, take)
    if github:
        for location, text in iter_github_texts(github_slug(public)):
            _merge(found, scan_text(text, criteria, location, key_re))
    allow = load_allowlist(private)
    suppressed = 0
    rows = []
    for key, row in found.items():
        if mode == "watch" and key in allow:
            suppressed += 1
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r.check, r.digest))
    telegram_state, mc_state, notified = "no", "no", False
    report_path: Path | None = None
    if mode == "watch" and rows:
        # The report path is known before the file exists so the note can name it.
        report_path = private / "leak-scan" / "reports" / f"{now.strftime('%Y-%m-%dT%H%M%SZ')}.md"
        text = notice(len(rows), report_path)
        send = telegram if telegram is not None else send_telegram
        note = mc_note if mc_note is not None else (lambda body: write_mc_note(body, notes_root))
        telegram_state = "sent" if send(text) else "skipped"
        mc_state = "written" if note(text) else "skipped"
        notified = True
    report = write_report(
        private, mode, rows, suppressed=suppressed, skipped_large=skipped, criteria=criteria,
        telegram=telegram_state, mc_note=mc_state, now=now,
    )
    return Summary(mode, len(rows), _counts(rows), suppressed, report, notified, telegram_state, mc_state, skipped)


def _print(summary: Summary) -> None:
    counts = " ".join(f"{name}={summary.by_check[name]}" for name in ("passage", "name", "voice", "identifier", "key"))
    print(
        f"mode={summary.mode} hits={summary.hits} {counts} suppressed={summary.suppressed} "
        f"notified={str(summary.notified).lower()} telegram={summary.telegram} mc_note={summary.mc_note} "
        f"report={summary.report}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan the public repo for private material.")
    parser.add_argument("--public", required=True, type=Path)
    parser.add_argument("--private", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--baseline", action="store_true")
    mode.add_argument("--watch", action="store_true")
    parser.add_argument("--no-github", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run(args.public, args.private, "watch" if args.watch else "baseline", github=not args.no_github)
    except ScanError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
