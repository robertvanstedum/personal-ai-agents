# Portable Personal Instance Hypothesis

**Version:** 0.1  
**Date:** 2026-09-02  
**Status:** Exploratory  
**Commercial scope:** Excluded

## Hypothesis

A reusable mini-moi distribution and a person-owned repository may allow a
second person to initialize an independent mini-moi instance without receiving
Robert's personal content.

This is not ordinary multiuser support. It is a separate instance with its own
owner, context, history, configuration, and domain state.

## Proposed model

```text
mini-moi distribution
  core services + CoS + Guild + domain packs + deployment tools
                              +
new personal repository
  identity + context + memory + planning + domain state
                              =
independent personal instance
```

## Domain pack separation

Each domain should distinguish reusable baseline from personal accumulation.

For a Portuguese domain:

| Reusable baseline | Person-specific state |
|---|---|
| Workflows and exercises | Assessed ability |
| Conversation modes | Vocabulary and repeated errors |
| Generic prompts and evaluation | Interests and goals |
| Schema and migrations | Session and feedback history |
| Initial setup | Learned preferences |

Software upgrades must not overwrite accumulated personal state.

## Portability criteria to explore

- Installation does not require Robert's filesystem or credentials.
- A new instance starts from clean, generic seeds.
- The owner can read and export durable context without the original agent.
- Models and hosting environments can change.
- Domain state has versioned export and restore behavior.
- Backup restoration is tested rather than assumed.
- No Robert-specific record appears in a second-person instance.

## Evidence sequence

The second-person proof should not be the first build. First establish that
Robert actually uses and benefits from the repository. If that happens, a clean
initialization can test whether the architecture is truly portable.

The proof should use synthetic or consenting test content and verify isolation
explicitly.

## Non-goals

- Multi-tenant SaaS.
- A shared household memory by default.
- Commercial packaging.
- Automatic sharing between personal instances.
- Declaring a 2.0 release before sustained use.

