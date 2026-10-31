"""The pinned local embedder (v0.5 C4/C7; feasibility 2026-10-03).

One embedder, named and pinned by digest: Ollama ``nomic-embed-text`` with model digest prefix ``0a109f422b47``
(768 dimensions). Before it embeds anything, the client asks Ollama which model digest it would actually use; a
different digest **refuses** (``PinMismatch``) instead of silently mixing vectors from two models. The index records
the identity it was built with; a query against a different identity is refused by ``search`` and says so.

nomic-embed-text expects task prefixes (``search_document:`` / ``search_query:``). Vectors are L2-normalized here,
so cosine distance in sqlite-vec is well defined whatever the server returns.

``Embedder`` is the interface; ``OllamaEmbedder`` the real one; tests use their own deterministic implementation.
"""
from __future__ import annotations

from core.jobs import batch_window

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass

PIN_NAME = "nomic-embed-text"
PIN_DIGEST = "0a109f422b47"
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


class OllamaEmbedder(Embedder):
    def __init__(self, url: str = DEFAULT_URL, model: str = f"{PIN_NAME}:latest", timeout: float = 120.0):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self._ident: Identity | None = None
        self._used = False

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
            self._post("/api/embed", {"model": self.model, "input": ["release"], "keep_alive": 0}, cleanup=True)
        except EmbedderUnavailable:
            pass
        self._used = False


__all__ = ["Embedder", "OllamaEmbedder", "Identity", "EmbedderUnavailable", "PinMismatch", "PIN_NAME", "PIN_DIGEST",
           "DIMS", "normalize"]
