# Codex: first review of spec 161 (MiniMoi observability platform)

**From:** Claude Code.
**Requested by:** Robert, 20 Oct 2026: "Send spec 161 to Codex for the first review now; second reviewer after."

**What to review:**
- **File:** `docs/specs/spec_161_minimoi_observability_platform_2026-10-20.md`
- **Branch:** `claude/spec-161-observability`
- **Commit:** `1cce1865751514554a266d82889fab358b6f2cb1`
- **File SHA-256:** `f17c3d57854da6dedf826c736339c2ce8421046063e624ac2931a3df5dc85c9d`
- **Issue:** https://github.com/robertvanstedum/personal-ai-agents/issues/364
- **Status:** draft v0.1. It authorizes no build.

## Why this spec exists

Robert wants observability as one reusable platform capability for **all of MiniMoi**, not only MC:
- components report facts as telemetry;
- a tool (built or bought: Grafana, Netcool, Splunk and so on) consumes it;
- thresholds and alarms are configured in that tool, never in code.

**The bar:** "a production quality solution with engineering principles that stand up to review."

## Please review especially

1. **Platform fit:**
   - the Collector, Prometheus and Alertmanager containers on EC2;
   - compose ownership;
   - pinned digests and resource limits;
   - where it runs on dev (the Mac) versus production.
2. **The status-file bridge:** should it be a Collector receiver or a small sidecar? How should it read `config/scheduled_jobs.json` and the status contract?
3. **The `core/observability` library contract** (§5.1): the API, the non-blocking export, the local copy first, and vocabulary validation. Does it fit the portal, cos-scheduler, curator and gateway processes?
4. **CI:** running `promtool` and `amtool` checks on `config/observability/`.
5. **The Guild Operate tile:** reading the same data (O2).
6. **Security:**
   - OTLP only on the private network;
   - TLS and token across hosts;
   - secrets placement;
   - nothing exposed through nginx.
7. **Ownership split** in §11, and anything missing for production quality.
8. **Impact on MC 1.2:**
   - MC's standing checks become the first adopter;
   - their thresholds move to rules;
   - the 05:40 brief becomes an optional digest;
   - the site chat-posting ingress is no longer needed for P0.

   Does that change anything you have queued?

## Output

Please write findings to `docs/specs/reviews/spec_161/02_codex_review_v0.1_<date>.md` on branch `claude/spec-161-observability` (versioned; commit it there):
- numbered findings, each with a severity;
- the exact spec section for each;
- a verdict: ready for the second review, or changes first.

## Next steps

1. I fold your findings into v0.2.
2. A second, industry-alignment reviewer (Claude Chat or Grok) reviews v0.2.
3. Robert approves.
4. Only then does building start.
