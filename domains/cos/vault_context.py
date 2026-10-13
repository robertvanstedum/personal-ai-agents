"""Bounded Vault evidence at the existing CoS backend boundary.

The canonical user turn stays unchanged. Retrieval contributes source DATA
only to that turn's system context, and its retained ranges check the reply's
citations. Caller grants and transport belong to the Ask consumer/service.
No shelf, credentials or query text is written here. Disabled by default.
"""
from __future__ import annotations

import os
import re

SYNTHESIS = re.compile(r"\[VAULT_SYNTHESIS\](.*?)\[/VAULT_SYNTHESIS\]", re.S)
SYNTHESIS_RULE = (
    "When synthesizing the retrieved Vault passages, wrap only that synthesis "
    "in [VAULT_SYNTHESIS]...[/VAULT_SYNTHESIS] and cite a retained file:line "
    "range for each claim. Keep current tool/web answers and other reasoning "
    "outside this block. These markers are removed before the reply is shown."
)

UNAVAILABLE = (
    "Vault evidence is unavailable for this turn. Do not claim to have "
    "retrieved earlier discussions. Explain this limitation when relevant."
)


def call_with_vault(backend, prompt, context, tool_policy, *, retrieve=None, environ=None):
    environ = os.environ if environ is None else environ
    configured = any(environ.get(k) for k in ("MINIMOI_VAULT_ASK_URL", "MINIMOI_VAULT_ASK_TOKEN"))
    # Private turns must not reach a service that logs retrieval queries.
    if not configured or context.get("record_mode") == "off_record":
        return backend.call_backend(prompt, context, tool_policy)

    built = None
    has_passages = False
    block = UNAVAILABLE
    report = {"hook_status": "unavailable", "code": "unavailable"}
    try:
        if retrieve is None:
            from core.memory_ask.consumer import retrieve
        result = retrieve(prompt, max_chars=6000, max_bytes=12000)
        candidate = result.prompt_block
        if candidate:
            if not isinstance(candidate, str):
                raise ValueError("invalid source block")
            block = candidate
        built = result.built
        report = result.report
        if not isinstance(report, dict):
            raise ValueError("invalid retrieval report")
        has_passages = built is not None and built.report.get("passages_prepared", 0) > 0
    except Exception:
        # A missing consumer, bad configuration or malformed response must
        # not prevent the canonical agent from answering or expose secrets.
        built, has_passages, block = None, False, UNAVAILABLE
        report = {"hook_status": "unavailable", "code": "unavailable"}

    # Context is owned by this turn. Preserve it for runtime-route receipts;
    # do not replace the user prompt or append source evidence to user_text.
    if has_passages:
        block += "\n\n" + SYNTHESIS_RULE
    context["system_prompt"] = f"{context.get('system_prompt', '').rstrip()}\n\n{block}".strip()
    context["vault_retrieval"] = report
    reply = backend.call_backend(prompt, context, tool_policy)
    def mark(match):
        text = match.group(1).strip()
        if not has_passages:
            return text
        try:
            return built.mark_synthesis(text)
        except Exception:
            return text + "\n\nVault citations could not be checked for this synthesis."
    # A general agent answer is not automatically a Vault synthesis. The
    # range checker applies only to the explicitly identified source part.
    return SYNTHESIS.sub(mark, reply).replace("[VAULT_SYNTHESIS]", "").replace("[/VAULT_SYNTHESIS]", "")
