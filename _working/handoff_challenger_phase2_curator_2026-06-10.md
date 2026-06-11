# Challenger Phase 2 — Curator Integration
*mini-moi · Guild · Challenger pattern*
*Authored: 2026-06-10 — Claude.ai*
*Spec: `_working/spec_challenger_pattern_2026-06-10.md`*
*Phase 1 confirmed: test_schema.py 25/25, id=1 in guild.challenger_exchanges, robert_ro readable*

---

## What changes

Two files touched. Nothing else.

| File | Change |
|---|---|
| `_NewDomains/research-intelligence/scripts/generate_dive.py` | Insert ChallengerService after primary synthesis |
| Portal dive template | Add collapsed challenger process section |

---

## `generate_dive.py` integration

The existing primary synthesis call becomes Round 1. ChallengerService handles 2 and 3.

**Insertion point:** after the primary Sonnet synthesis produces its output, before the
result is written to file or returned to the portal.

```python
from domains.guild.services.challenger import ChallengerService

# --- existing code produces synthesis ---
# synthesis = ... (whatever the current synthesis variable is)

# --- add after synthesis ---
_challenger = ChallengerService()
_result = _challenger.run(
    domain="curator_deep_dive",
    feature="deeper_dive",
    first_pass=synthesis,
    context={
        "topic_name":        thread_slug,         # current thread slug/name
        "related_threads":   related_thread_slugs, # list of other active thread slugs
        "sources_summary":   sources_text[:1000],  # first 1000 chars of source material
        "entity_id":         thread_id,            # research.topics id if available
        "entity_description": thread_name,
    }
)

# Use final text (unchanged from synthesis when enabled=False)
synthesis = _result.final

# Pass result to template/output for portal rendering
# (see portal section below for what to do with _result)
```

**When `enabled: false`:** `_result.final == synthesis` (original), `_result.enabled == False`.
The caller code path is unchanged. The portal section simply doesn't render.

---

## Regression requirement

Run one existing dive with `enabled: false` in `challenger_config.json`.
Confirm the output text is byte-for-byte identical to what the existing code produces today.
This is the regression gate — do not proceed to the portal changes until this passes.

---

## Portal UI — challenger process section

Add below the existing dive output. Uses the existing design system (parchment background,
copper accent, Georgia headings — same as the rest of the Curator portal).

**When `show_process: false` OR `enabled: false`:** section is absent entirely.
The page looks exactly as it does today.

**When `show_process: true`:** add two collapsible sections below the dive output.
Both collapsed by default. One click to expand.

```html
{% if challenger_result and challenger_result.show_process %}

<div class="challenger-process" style="margin-top: 2rem; border-top: 1px solid #C68A5E44;">

  <!-- Section 1: Challenger review digest -->
  <details>
    <summary style="cursor:pointer; padding: 0.75rem 0; color: #C68A5E; font-size: 0.85rem; letter-spacing: 0.05em; text-transform: uppercase;">
      Challenger review
      <span style="color: #888; font-weight: normal; text-transform: none; letter-spacing: 0;">
        ({{ challenger_result.challenge_points | length }} points ·
         {{ challenger_result.accepted_count }} accepted ·
         {{ challenger_result.rejected_count }} rejected)
      </span>
    </summary>
    <div style="padding: 0.75rem 0 1rem 0; font-size: 0.9rem; line-height: 1.6;">
      {% for point in challenger_result.challenge_points %}
      <div style="margin-bottom: 0.5rem; padding-left: 1rem;
                  border-left: 2px solid {{ '#C68A5E' if point.accepted else '#888' }};">
        <span style="font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;
                     color: {{ '#C68A5E' if point.accepted else '#888' }};">
          {{ '✓' if point.accepted else '✗' }} {{ point.type | replace('_', ' ') }}
          {% if point.impact == 'high' %} · high impact{% endif %}
        </span><br>
        {{ point.description }}
      </div>
      {% endfor %}
      {% if challenger_result.key_change %}
      <div style="margin-top: 0.75rem; font-style: italic; color: #666;">
        Key change: {{ challenger_result.key_change }}
      </div>
      {% endif %}
    </div>
  </details>

  <!-- Section 2: Initial draft -->
  <details>
    <summary style="cursor:pointer; padding: 0.75rem 0; color: #888; font-size: 0.85rem; letter-spacing: 0.05em; text-transform: uppercase;">
      Initial draft
    </summary>
    <div style="padding: 0.75rem 0 1rem 0; font-size: 0.9rem; line-height: 1.6;
                color: #666; font-style: italic;">
      {{ challenger_result.first_pass_summary or first_pass }}
    </div>
  </details>

</div>
{% endif %}
```

**Pass `_result` to the template:**
```python
# In the route or render call, add challenger_result to the template context
return render_template("...", challenger_result=_result, ...)
```

---

## Definition of done

- [ ] `generate_dive.py` imports and calls `ChallengerService`
- [ ] Regression: dive output with `enabled: false` identical to today's output
- [ ] Portal: collapsed challenger process section renders when `show_process: true`
- [ ] Portal: section is absent when `show_process: false` or `enabled: false`
- [ ] Run one real dive with `show_process: true` — confirm challenge digest appears correctly
- [ ] `guild.challenger_exchanges` gains a new row for the real dive
- [ ] Robert visual sign-off on the challenger process UI before merge

---

## Pre-release test protocol (from spec — required before merge)

Run 5 Curator deep dives in transparent mode. Review each against:
- Challenge types correctly classified (not generic observations)
- Accepted challenges visibly incorporated in the final
- Rejected challenges have clear in-scope justification
- `outputs_differ = true` in ≥ 3 of 5

Gate: 4 of 5 pass all criteria. Robert reviews all 5 before merge.

---

## Commit

```bash
git add _NewDomains/research-intelligence/scripts/generate_dive.py \
        [portal dive template path]
git commit -m "phase2: Curator challenger integration — ChallengerService wired into generate_dive.py"
git push origin main
```

---

*Challenger Phase 2 · Curator · 2026-06-10 · Claude.ai*
*Gate: 5 real dives reviewed in transparent mode, 4/5 pass, Robert signs off*
