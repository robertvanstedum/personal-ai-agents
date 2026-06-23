# Spec — minimoi.ai Always-On Landing Page (Investigation + Roadmap Entry)
*mini-moi · Guild*
*Created: 2026-06-13 — Claude.ai*
*For: Claude Code*

---

## Why this matters now

minimoi.ai currently runs via Cloudflare tunnel to Robert's MacBook. The
landing page we just polished (What this is / What's running / About) is
the front door for job search — LinkedIn and GitHub both point here, and
interviewers may check it at any time, including when the Mac is asleep or
off (e.g., travel). A portfolio site that's sometimes down undercuts the
work just done on it.

This is **investigation + roadmap entry only** — not an implementation spec.
The actual fix depends on findings below, and would be a follow-up spec.

---

## Investigation checklist

1. **How is the landing page currently served?** Part of the portal Flask
   app (port 5001)? A static template? Does it require the backend/DB at
   all, or is it pure HTML/CSS/text?
2. **Any dynamic content on the landing page?** E.g., does "~700 candidates
   scored daily" or similar ever pull live numbers, or is everything
   hand-written copy (as it is now)? If dynamic, that's a dependency that
   would need to move too, or be dropped/hardcoded for the static version.
3. **Current DNS / Cloudflare setup for minimoi.ai** — apex domain routing
   through the tunnel to the Mac. What would it take to route the root
   (landing page) to a static host while keeping `/dashboard` (and any
   other authenticated routes) routed to the tunnel? Options likely
   include: Cloudflare Pages for the static site + tunnel only for
   `/dashboard` subpath or subdomain (e.g. `app.minimoi.ai`), or similar.
4. **Candidate static hosts** — Cloudflare Pages is a natural fit given
   Cloudflare is already in the stack (tunnel + DNS), but GitHub Pages /
   Netlify / Vercel are also options. Note tradeoffs if any stand out
   (e.g., Cloudflare Pages keeping everything in one provider vs. simplest
   to set up).
5. **"View source on GitHub" and "LinkedIn" links** — confirm these are
   plain external links with no backend dependency (should be trivial, but
   confirm as part of the audit).

---

## Working hypothesis (for Claude Code to confirm or correct)

Split the landing page into a static page (no Flask, no backend) hosted
separately — up 24/7 regardless of the Mac's state. "Dashboard →" continues
to link to the live Flask app via the Cloudflare tunnel, which is up
whenever the Mac is on. Interviewers/recruiters always see the front door;
the Dashboard being occasionally unavailable just means a logged-in feature
is offline, not the whole site.

If investigation finds this is harder than expected (e.g., landing page is
tightly coupled to the Flask app's templating/data), note that and propose
alternatives — including the previously-deferred Mac Mini migration
(`v1.2` on the roadmap) as a different way to solve "always on" if the
static-split approach isn't clean.

---

## Definition of Done

- [ ] Investigation checklist above completed, findings written up (in this
      file or a follow-up doc — Claude Code's call)
- [ ] Recommendation given: static-split (with rough plan), Mac Mini
      migration, or other — with reasoning
- [ ] Entry added to `docs/ROADMAP.md` under "Next" (or "Platform-Portfolio"
      if that's still the better-fitting section), along the lines of:

      > **minimoi.ai always-on landing page** — Public landing page
      > currently depends on Cloudflare tunnel + MacBook uptime. For job
      > search, needs to be reliably available independent of Mac state.
      > See `spec_landing_page_uptime_2026-06-13.md` for investigation
      > findings and recommended approach.

- [ ] If recommendation is clear and small, a follow-up implementation spec
      can be written in the same pass — Robert's call whether to do that
      now or treat as separate spec-ready item

---

*Spec · Landing Page Uptime · 2026-06-13*
