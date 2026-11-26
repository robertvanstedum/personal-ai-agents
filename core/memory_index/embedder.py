"""The pinned local embedder (v0.5 C4/C7; feasibility 2026-10-03).

One embedder, named and pinned by digest: Ollama ``nomic-embed-text`` with model digest prefix ``0a109f422b47``
(768 dimensions). Before it embeds anything, the client asks Ollama which model digest it would actually use; a
different digest **refuses** (``PinMismatch``) instead of silently mixing vectors from two models. The index records
the identity it was built with; a query against a different identity is refused by ``search`` and says so.

nomic-embed-text expects task prefixes (``search_document:`` / ``search_query:``). Vectors are L2-normalized here,
so cosine distance in sqlite-vec is well defined whatever the server returns.

``Embedder`` is the interface; ``OllamaEmbedder`` the real one; tests use their own deterministic implementation.

The real client's identity check (#660) is stricter than the name and digest prefix: a server reporting a full digest must report
exactly ``PIN_DIGEST_FULL``, and once per process per server a **known-answer probe** embeds a fixed sentence and compares it with
the reference vector made on the workstation's pinned model (``embedder_probe.json``). A server that names the right digest but
serves other weights or another quantization is refused (``PinMismatch``).

``release`` unloads the model (``keep_alive=0``) unless ``MOI_OLLAMA_IDLE_KEEP_ALIVE`` says how long to keep it: the shared
workstation unloads at once; the server's dedicated embedder keeps it loaded between queries.
"""
from __future__ import annotations

from core.jobs import batch_window

import json
import math
import os
import urllib.error
import urllib.request
import re
from dataclasses import dataclass
from pathlib import Path

PIN_NAME = "nomic-embed-text"
PIN_DIGEST = "0a109f422b47"
PIN_DIGEST_FULL = "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"
PROBE_PATH = Path(__file__).with_name("embedder_probe.json")
IDLE_KEEP_ALIVE_ENV = "MOI_OLLAMA_IDLE_KEEP_ALIVE"
_KEEP_ALIVE = re.compile(r"^(0|[1-9][0-9]{0,3}[smh])$")
DIMS = 768
DOC_PREFIX, QUERY_PREFIX = "search_document: ", "search_query: "
DEFAULT_URL = "http://127.0.0.1:11434"
BATCH = 16                        # small batches: the Mac is 8 GB and shared
RUN_KEEP_ALIVE = "2m"             # keep the model loaded between batches of one run...
# ...and unload it when the run ends (``release``), so the ~300 MB embedder is resident only while it works.


class EmbedderUnavailable(RuntimeError):
    """The embedder could not be reached or answered badly. Never carries text."""


class PinMismatch(RuntimeError):
    """The embedder is not the pinned model (or not the model the index was built with)."""


@dataclass(frozen=True)
class Identity:
    name: str
    digest: str               # full digest when known
    dims: int

    @property
    def version(self) -> str:
        return self.digest[:12]

    def label(self) -> str:
        return f"{self.name}@{self.version}"


def load_probe(path: Path = PROBE_PATH) -> tuple[str, list[float], float]:
    """(text, unit reference vector, minimum cosine). A missing or malformed probe file refuses (never skips the check)."""
    try:
        doc = json.loads(path.read_text())
        text, vec, floor = doc["text"], doc["vector"], float(doc["min_cosine"])
        if (doc.get("digest") != PIN_DIGEST_FULL or not isinstance(text, str) or not text or not isinstance(vec, list)
                or len(vec) != DIMS or not 0.9 <= floor < 1):
            raise ValueError
        return text, normalize([float(x) for x in vec]), floor
    except (OSError, ValueError, KeyError, TypeError, EmbedderUnavailable):
        raise PinMismatch("embedder probe reference missing or malformed") from None


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))           # both unit vectors


def idle_keep_alive(environ=None) -> str:
    """How long the server keeps the model after a run or query: ``0`` (unload now, the default) or ``<n>s|m|h``."""
    value = (os.environ if environ is None else environ).get(IDLE_KEEP_ALIVE_ENV) or "0"
    return value if _KEEP_ALIVE.match(value) else "0"


def normalize(vec: list[float]) -> list[float]:
    try:
        norm = math.sqrt(sum(x * x for x in vec))
    except OverflowError:
        raise EmbedderUnavailable("embedder returned a zero or invalid vector") from None
    if not norm or not math.isfinite(norm):
        raise EmbedderUnavailable("embedder returned a zero or invalid vector")
    return [x / norm for x in vec]


class Embedder:
    def identity(self) -> Identity:
        raise NotImplementedError

    def _embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def check_pin(self, name: str = PIN_NAME, digest: str = PIN_DIGEST) -> Identity:
        ident = self.identity()
        if ident.name.split(":", 1)[0] != name or not ident.digest.startswith(digest) or ident.dims != DIMS:
            raise PinMismatch(f"embedder is {ident.label()}, pinned {name}@{digest}")
        return ident

    def embed_documents(self, texts: list[str], max_chars: int) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            batch_window.remaining()
            batch = [DOC_PREFIX + t[:max_chars] for t in texts[i:i + BATCH]]
            out.extend(normalize(v) for v in self._embed(batch))
        return out

    def embed_query(self, text: str) -> list[float]:
        return normalize(self._embed([QUERY_PREFIX + text])[0])

    def release(self) -> None:
        """Unload the model now (end of a run or a one-off query). Best effort; never raises."""


_PROBED: set[tuple[str, str]] = set()     # (server url, digest) that passed the known-answer probe in this process


class OllamaEmbedder(Embedder):
    def __init__(self, url: str = DEFAULT_URL, model: str = f"{PIN_NAME}:latest", timeout: float = 120.0):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self._ident: Identity | None = None
        self._used = False

    def check_pin(self, name: str = PIN_NAME, digest: str = PIN_DIGEST) -> Identity:
        ident = super().check_pin(name, digest)
        if name != PIN_NAME or digest != PIN_DIGEST:
            return ident                              # a caller checking another pin gets the base check only
        if ident.digest != PIN_DIGEST_FULL:
            raise PinMismatch(f"embedder digest is {ident.digest[:12]}..., not the pinned full digest")
        key = (self.url, ident.digest)
        if key not in _PROBED:
            text, reference, floor = load_probe()
            got = normalize(self._embed([text])[0])
            if cosine(got, reference) < floor:
                raise PinMismatch("embedder failed the known-answer probe: another model behind the pinned name")
            _PROBED.add(key)
        return ident

    def _post(self, path: str, doc: dict | None, *, cleanup=False) -> dict:
        data = None if doc is None else json.dumps(doc).encode()
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"},
                                     method="GET" if doc is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout if cleanup else batch_window.remaining(self.timeout)) as resp:
                value = json.loads(resp.read())
                if not isinstance(value, dict):
                    raise EmbedderUnavailable("embedder answered with a non-object response")
                return value
        except (urllib.error.URLError, OSError, ValueError):
            raise EmbedderUnavailable("embedder not reachable") from None

    def identity(self) -> Identity:
        if self._ident is None:
            tags = self._post("/api/tags", None).get("models") or []
            if not isinstance(tags, list) or any(not isinstance(model, dict) for model in tags):
                raise EmbedderUnavailable("embedder answered with an invalid model list")
            found = next((m for m in tags if m.get("name") == self.model or m.get("model") == self.model), None)
            if not found or not isinstance(found.get("digest"), str):
                raise EmbedderUnavailable("embedder model not installed")
            self._ident = Identity(self.model, found["digest"], DIMS)
        return self._ident

    def _embed(self, texts: list[str]) -> list[list[float]]:
        self._used = True
        doc = self._post("/api/embed", {"model": self.model, "input": texts, "truncate": True,
                                        "keep_alive": RUN_KEEP_ALIVE})
        vecs = doc.get("embeddings")
        try:
            valid = (isinstance(vecs, list) and len(vecs) == len(texts)
                     and all(isinstance(v, list) and len(v) == DIMS
                             and all(type(n) in (int, float) and math.isfinite(n) for n in v)
                             for v in vecs))
        except OverflowError:  # JSON integers can exceed the finite float range.
            valid = False
        if not valid:
            raise EmbedderUnavailable("embedder answered with the wrong shape")
        return vecs

    def release(self) -> None:
        if not self._used:
            return
        try:
            keep = idle_keep_alive()
            self._post("/api/embed", {"model": self.model, "input": ["release"], "keep_alive": 0 if keep == "0" else keep},
                       cleanup=True)
        except EmbedderUnavailable:
            pass
        self._used = False


__all__ = ["Embedder", "OllamaEmbedder", "Identity", "EmbedderUnavailable", "PinMismatch", "PIN_NAME", "PIN_DIGEST",
           "PIN_DIGEST_FULL", "DIMS", "normalize", "load_probe", "idle_keep_alive"]
