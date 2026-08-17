# Release Notes — Chief of Staff Agent Beta

*mini-moi · personal-ai-agents*
*Production milestone: 2026-08-16*

## What this is

Chief of Staff is now a genuine bounded-agent experience in production. It is
still a beta—not CoS 1.0—but it establishes the first complete agentic loop in
mini-moi: Robert can converse by text or realtime voice, ask the agent to research
within a bounded tool contract, and save a durable note through a verified
platform-owned mutation.

## What shipped

- **COS Agent A:** an isolated OpenClaw runtime in its own container. The domain
  and integration call it Agent A because OpenClaw is the current shell, not a
  permanent architectural dependency.
- **Typed Confer:** authenticated turns pass through the COS platform service to
  Agent A and return the actual served model/provider receipt.
- **Realtime voice:** the same provider-swappable conversation pattern proven in
  Gespräche and Conversas, selectable between OpenAI and Grok, with interruption,
  voice-first response, and transcript insertion after the session stops.
- **Bounded tools:** voice can consult Agent A and save a COS note. Agent A can use
  a quality-oriented search adapter returning up to 20 cited sources. General
  browser, arbitrary fetch, filesystem, runtime, messaging, and subagent access
  remain denied.
- **Truthful notes:** note success is reported only after the application-owned
  memory path returns a write receipt; OpenClaw does not own or merely claim the
  mutation.
- **Shared model gateway:** LiteLLM provides configurable provider order,
  fallback metadata, health, routing receipts, and the basis for cost reporting.
  Production currently uses cloud routes; local Ollama remains a development and
  testing option.
- **Durable identity:** COS Agent A has its own persistent identity and working
  style, including a natural multilingual relationship with Robert, without
  granting unrestricted access to personal or domain information.

## Production acceptance

The deployed containers, authenticated Agent A round trip, model route, and note
receipt were checked on August 16. Robert then completed the final acceptance on
the real production interface: a microphone conversation and explicit note save
both worked well.

## What remains beta

- raw conversation retention, condensation, archive, purge, and off-record/delete
  policy (Spec #150);
- natural capture phrases and follow-up tags beyond the explicit note path;
- broader read/consult interfaces across mini-moi domains;
- daily-use evaluation of whether the relationship behaves like a useful working
  partner rather than a well-prompted tool;
- wider LiteLLM adoption and a recurring COS model-cost checkpoint.

This release is a milestone because the agent is real, bounded, replaceable, and
connected to an application-owned action path. The remaining work is intentionally
visible rather than hidden behind a 1.0 label.
