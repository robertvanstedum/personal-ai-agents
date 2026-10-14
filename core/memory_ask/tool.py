"""The memory tool both agents share (owner direction 4 Oct 2026: Master Craftsman and Chief of Staff, one tool).

``SPEC`` is the model-facing contract (a JSON-schema function tool); ``call`` runs it. The two rules that make it safe
for more than one agent:

* **Identity is the host's, not the model's.** ``call`` takes ``agent``, ``entry_point`` and ``scopes`` from the code that wires the
  tool into a runtime (``"mc"``, ``"cos"``), never from the arguments. The supported host is the Ask service (``service.py``), which derives
  all three from the caller's registered token (``identity.py``). The model cannot pick a scope, a canary or another
  agent's name; unknown arguments are refused (``bad_arguments``).
* **Same procedure, same answer.** It is ``ask`` underneath, so for the same query the two agents get identical hits;
  only the log line differs (who asked).

Failures are returned as ``{"error": code}``, never raised into the model's turn, and carry no record text.
The tool only reads: it writes the one query-log line and nothing else.
"""
from __future__ import annotations

from core.memory_ask import ask as ask_mod
from core.memory_index import policy, search, store
from core.memory_ask.citation import check, mark_unsupported  # noqa: F401  (the host applies these to the agent's synthesis)

TOOL_NAME = "memory_ask"
CITATION_RULE = ("Answer only from the passages returned. Cite every claim as file:line or file:start-end of a returned "
                 "passage. A sentence with no such citation is invalid and will be marked unsupported. If the passages do "
                 "not answer the question, say so.")
SPEC = {
    "name": TOOL_NAME,
    "description": ("Search Robert's memory (his own conversations and records) by exact words and by meaning. Returns two "
                    "labelled lists, keyword and meaning, each passage read from the file on disk with its file, lines, "
                    "date and provenance, plus one labelled Adjacent result from elsewhere. By default only curated records "
                    "are searched; set include_raw only when Robert asks to search the raw record or nothing curated is "
                    "found. Use created_after/created_before (YYYY-MM-DD) for questions about a period. Write the query as the topic "
                    "words that name the subject (for example 'Grok review adopted not adopted'), not Robert's whole sentence, and "
                    "run several differently worded queries for a broad question: the keyword list matches words, and a full "
                    "question is mostly filler. The date filter is the conversation's creation date, not each turn's."),
    "input_schema": {
        "type": "object", "additionalProperties": False, "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": ask_mod.querylog.MAX_QUERY,
                      "description": "The question in Robert's own words, with the topic and any time context."},
            "include_raw": {"type": "boolean", "default": False},
            "created_after": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
            "created_before": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
            "k": {"type": "integer", "minimum": 1, "maximum": ask_mod.MAX_K, "default": search.DEFAULT_K}}},
}
_ALLOWED = set(SPEC["input_schema"]["properties"])


def call(arguments: dict, *, agent: str, entry_point: str = "tool", shelf_root, embedder, index_path=None, log_path=None,
         clock=None, scopes: tuple[str, ...] | None = None) -> dict:
    try:
        if not isinstance(arguments, dict) or set(arguments) - _ALLOWED or "query" not in arguments:
            return {"error": "bad_arguments"}
        include_raw = arguments.get("include_raw", False)
        if not isinstance(include_raw, bool):
            return {"error": "bad_arguments"}
        for key in ("created_after", "created_before"):
            if key in arguments and not isinstance(arguments[key], str):
                return {"error": "bad_arguments"}
        caller = policy.Caller(tier="all" if include_raw else "curated", scopes=scopes, agent=agent, entry_point=entry_point)
        result = ask_mod.ask(arguments["query"], shelf_root=shelf_root, embedder=embedder, caller=caller,
                             k=arguments.get("k", search.DEFAULT_K), index_path=index_path, log_path=log_path,
                             created_after=arguments.get("created_after"), created_before=arguments.get("created_before"),
                             clock=clock)
    except ask_mod.BadQuery as exc:
        return {"error": str(exc)}
    except ValueError:                                       # a malformed caller token (host bug), never the model's
        return {"error": "bad_caller"}
    except store.IndexMissing:
        return {"error": "index_missing"}
    except store.IndexIncompatible:
        return {"error": "index_incompatible"}
    except search.QueryRefused as exc:
        return {"error": exc.code}
    return {**result, "citation_rule": CITATION_RULE}


__all__ = ["SPEC", "TOOL_NAME", "CITATION_RULE", "call"]
