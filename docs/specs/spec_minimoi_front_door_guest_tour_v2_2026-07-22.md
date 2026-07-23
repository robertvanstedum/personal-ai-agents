# mini-moi Front Door and Guest Tour Refresh

**File:** `docs/specs/spec_minimoi_front_door_guest_tour_v2_2026-07-22.md`
**Version:** v2  
**Date:** July 22, 2026  
**Status:** Reviewed — technically confirmed, no blocking findings. Implementation not authorized until Robert reviews the build PR.
**Target:** Registered in the Guild build queue (id 143)
**Implementation authorization:** None until Robert approves the resulting pull request
**Reference mockup:** `_working/2026-07-22-minimoi-landing/mockups/landing-option-2-real-coffee-photo.png`
**Reviewed by:** Claude Code, 2026-07-22 — see § 15 for findings

## 1. Purpose

Refresh the mini-moi front door so that it serves two audiences without becoming two separate sites:

1. Robert and the small group of authenticated users who come to mini-moi to use its workspaces.
2. Visitors who want to understand and explore the system without receiving access to private data or live functionality.

The landing page should feel brighter, more inviting, and consistent with the visual character of the individual domains. It should remain primarily an entry point for use, not become a conventional marketing page.

## 2. Product Direction

The redesigned page should communicate:

> *mini-moi*  
> *Your context. Your goals. Your signal.*

The tagline remains provisional. Its final wording should be approved before implementation is considered complete, but it does not block technical review or registration of this specification.

The page should not state a fixed number of domains because the platform will continue to evolve.

The visual reference is the approved editorial mockup using the real two-coffee photograph. The final implementation should preserve its warm, personal quality while functioning as a real responsive interface.

The left edge of the photograph should blend gently into the page background, similar to the Café Sperl treatment. This should be achieved through CSS masking or a gradient rather than permanent destructive editing of the photograph.

## 3. Primary Experience

### Signed out

A signed-out visitor sees:

- The mini-moi name and approved tagline.
- A clear **Sign in** action.
- A prominent **Explore mini-moi** action.
- A short About explanation.
- Publicly visible workspace names, shown as locked or inactive.
- No Chief of Staff workspace, references, screenshots, or navigation.
- Links to GitHub and LinkedIn in a secondary position.

The workspace names orient the visitor but do not provide access. Locked items must not be presented as dead hyperlinks. They should use disabled elements with an accessible label such as “Sign in to open.”

### Signed in

An authenticated user lands on the same page, but:

- Workspaces permitted for that user become active links.
- Private workspaces appear only when the user is authorized to see them.
- The Chief of Staff workspace may appear for the owner, but never on the public version.
- The page displays the user identity and a sign-out action.
- The user can enter a workspace directly without first visiting a separate dashboard.

### Guest exploration

The **Explore mini-moi** action opens a curated, public screenshot tour.

This is a presentation of the real system, not a simulated live application. It should be simple, fast, and safe:

- Curator
- Mein Deutsch
- Meu Português
- Guild

Chief of Staff is excluded.

Each workspace receives a small number of deliberately selected screenshots with concise captions showing the actual workflow. The tour should not expose private information, political commentary, operational secrets, family information, or mutable production data.

The landing-page teaser is a representative editorial selection, not a complete domain inventory or a ranking of domain maturity. It may show Curator, Meu Português, and Guild as in the approved mockup. Mein Deutsch and the other public workspaces receive appropriate coverage in the full guest tour.

## 4. Navigation and Route Flow

```text
/  mini-moi landing
│
├── Signed out
│   ├── Sign in → /login → /
│   └── Explore mini-moi → /tour
│
├── Signed in
│   ├── Curator
│   ├── Mein Deutsch
│   ├── Meu Português
│   ├── Guild, when authorized
│   └── Chief of Staff, owner only
│
├── /dashboard → /
└── /preview/* → /tour
```

Explicit `next=` destinations used during authentication must continue to work. Only the default successful-login destination changes from the old dashboard to the refreshed landing page.

## 5. Application Wiring

### Authentication-aware landing page

Update the `/` route so the template receives:

- Current authenticated user, if any.
- Workspaces visible to that user.
- Whether each workspace is active, locked, or hidden.
- Sign-in, sign-out, and tour destinations.

The landing-page navigation is an orientation layer. Existing server-side route authorization remains authoritative and must not be weakened or replaced.

### Shared workspace configuration

Introduce a small portal-level workspace registry or helper so the landing page and the navigation injected into proxied applications do not maintain separate, drifting lists.

Each workspace definition should include only what is required, such as:

- Internal key
- Display label
- Route
- Whether it is publicly visible as a locked workspace
- Applicable access policy

This helper must reuse or reflect the existing authorization rules. It must not become a second authorization system.

### Dashboard retirement

The separate tile-based dashboard becomes redundant once the authenticated landing page provides direct workspace access.

For compatibility:

- Unauthenticated `/dashboard` continues to require sign-in.
- Authenticated `/dashboard` redirects to `/`.
- Existing bookmarks remain functional.
- Removal of the old dashboard template can occur in a later cleanup after production verification.

### Guest-tour route

Add a public `/tour` route with a normal Flask template and static curated assets.

The tour must:

- Require no authentication.
- Make no calls to private or production APIs.
- Contain no forms or live actions.
- Work on desktop and mobile.
- Provide clear next, previous, and return-to-home navigation.
- Never display Chief of Staff.

### Existing preview flow

The current frozen-HTML preview system has duplicated asset locations, capture overhead, stale-content risk, and a larger public surface than the proposed screenshot tour.

For this release:

- Remove links to the old preview experience.
- Redirect `/preview/` and `/preview/<path>` to `/tour`.
- Preserve the old capture assets temporarily for rollback.
- Delete the old preview assets and capture tooling only in a separate cleanup after production verification.

### Proxied workspace navigation

Update the shared portal navigation injected into workspace pages:

- The mini-moi brand should link to `/`, not `/dashboard`.
- Workspace visibility should match the authenticated landing page.
- Chief of Staff remains restricted.
- Existing server-side access checks remain unchanged.

## 6. Visual Implementation

Use the approved mockup as the design reference rather than as a literal background image.

### Required characteristics

- Light parchment or warm neutral background.
- Existing mini-moi typography and understated editorial character.
- Real two-coffee photograph as the principal image.
- Soft left-edge fade into the background.
- Strong but restrained Sign in action.
- Clearly visible Explore mini-moi action.
- Workspace navigation immediately visible.
- Mobile layout that does not simply shrink the desktop composition.
- No invented photographs or generic AI imagery.
- No public Chief of Staff imagery.

### Photograph preparation

Before committing the photograph:

- Strip all EXIF and location metadata.
- Produce an optimized web JPEG or WebP.
- Retain a high-quality source outside the deployed static directory.
- Avoid deploying the current large PNG conversion.
- Verify acceptable quality and loading time on desktop and mobile.

## 7. Expected Code Surface

Likely implementation files include:

- `minimoi_portal/app.py`
- `minimoi_portal/proxy.py`
- `minimoi_portal/templates/landing.html`
- `minimoi_portal/templates/login.html`, only if redirect handling requires it
- New `minimoi_portal/templates/tour.html`
- `minimoi_portal/static/portal.css`
- New optimized landing assets
- New curated tour assets
- Portal authentication and route tests

Protected repository documentation is not part of the implementation change unless separately authorized.

## 8. Content Requirements

The landing page should contain only enough text to orient a visitor.

Proposed structure:

1. mini-moi name and approved tagline.
2. One short paragraph explaining that mini-moi retains context across personal learning and work.
3. Sign in and Explore mini-moi actions.
4. Workspace navigation.
5. Small GitHub and LinkedIn links.

Exact About copy should be approved before implementation is considered complete.

The approved README introduction is the source for the landing-page About language. The landing version may be shorter, but it should retain the personal origin and the phrase “built for my own daily use” rather than drifting into generic product or promotional language.

The page must not:

- Describe mini-moi as having a fixed number of domains.
- Overstate commercial scale or general availability.
- Imply that public visitors can enter live workspaces.
- Name a model that may change through normal operations.
- Expose internal status, operational details, or private content.

## 9. Security and Privacy

The redesign must not change the existing authorization boundary.

Verification must confirm:

- Locked public navigation cannot bypass authentication.
- Direct workspace URLs still enforce their existing policies.
- Guest accounts see only explicitly permitted workspaces.
- Owner-only workspaces remain owner-only.
- Chief of Staff is absent from public HTML and public tour assets.
- Tour images contain no private information.
- The landing photograph contains no embedded device, date, or location metadata.
- No production API credentials or live data are delivered to `/tour`.

## 10. Testing

### Automated

Add or update tests for:

- Signed-out `/` rendering.
- Signed-in owner `/` rendering.
- Restricted guest `/` rendering.
- Correct active, locked, and hidden workspaces.
- Chief of Staff absent from public output.
- Public `/tour` availability.
- `/preview/*` redirects.
- Default successful login returns to `/`.
- Explicit authentication `next=` continues to work.
- Authenticated `/dashboard` redirects to `/`.
- Existing workspace authorization remains unchanged.

### Visual and manual

Review at minimum:

- Desktop width around 1440 pixels.
- Intermediate width around 1024 pixels.
- Mobile width around 390 pixels.
- Signed-out state.
- Owner state.
- Restricted guest state.
- Keyboard navigation and focus visibility.
- Image loading and crop behavior.
- Full click-through from landing to login, tour, and authorized workspaces.

### Deployment verification

After merge and deployment:

- Confirm the existing Cloudflare route still reaches the portal.
- Confirm `/`, `/login`, `/tour`, and `/dashboard`.
- Confirm each authorized workspace.
- Confirm unauthorized direct access remains blocked.
- Confirm GitHub and LinkedIn links.
- Confirm the old preview URLs redirect as specified.

No Cloudflare dashboard change is expected because the redesign remains within the existing Flask portal and current hostname.

## 11. Delivery Approach

Implement through a clean branch and pull request after the repository-reorganization merges have completed and the local branch is synchronized with `main`.

Recommended slices:

1. Authentication, workspace registry, and route wiring.
2. Landing template, responsive styling, and optimized photograph.
3. Public screenshot tour.
4. Redirects and compatibility behavior.
5. Automated and visual verification.

Do not push directly to production. Robert reviews the pull request and preview before authorizing merge and deployment.

## 12. Rollback

The release should remain easy to reverse:

- Revert the landing-page pull request.
- Preserve existing workspace routes.
- Keep the old dashboard template and preview assets during initial production verification.
- Remove legacy files only after the new flow is confirmed stable.

## 13. Acceptance Criteria

The work is complete when:

- `/` is the useful front door for both signed-out and signed-in users.
- Authenticated users can enter permitted workspaces directly.
- Signed-out visitors can understand and explore the system without a login.
- Chief of Staff is not exposed publicly.
- The real coffee photograph is optimized, sanitized, and displayed with the approved visual treatment.
- The separate dashboard is no longer part of the primary journey.
- The screenshot tour replaces the maintained interactive preview as the public exploration path.
- Existing authorization remains intact.
- Desktop and mobile layouts have been visually approved.
- Production verification passes after an explicitly authorized merge.

## 14. Review Questions Before Registration

Claude Code should confirm:

1. The workspace registry can be introduced without duplicating authorization logic.
2. `/dashboard` has no hidden consumers that prevent redirecting it.
3. All existing `/preview/*` links can safely redirect to `/tour`.
4. The portal image and new static tour assets are included correctly in the Docker build.
5. No code, documentation, screenshot package, LinkedIn material, resume draft, or other current entry point depends on or links directly to `/dashboard` without an intentional compatibility path.

After these points are confirmed and any resulting edits are approved, register the specification in the Guild build queue. Implementation remains held until Robert explicitly authorizes the build.

## 15. Claude Code Technical Review (2026-07-22)

All five review questions in § 14 confirmed against a clean `origin/main`
checkout (commit `6041c69`, current after the repository-reorganization
merges). None of the findings below are blocking — no material security,
routing, or deployment risk was found. Recorded here as implementation
caveats for whoever builds this.

**1. Workspace registry — safe, with one implementation discipline note.**
Current workspace access is decided per-route via `_require_owner`,
`_require_login`, and `_dauth.has_domain_access()`, with small inline guest
path-allowlists (German) or explicit per-guest grants (Portuguese) —
scattered across `minimoi_portal/app.py` and `minimoi_portal/proxy.py`, not
centralized. This scattering has already produced one real drift bug: the
`/dashboard` route's own Portuguese-guest visibility check
(`app.py:487`) is looser than `portuguese_root`'s actual access check
(`app.py:2152`) — a guest with no domain grant currently sees the
Portuguese tile on `/dashboard` but would be denied entry. The proposed
registry is safe and net risk-reducing *if and only if* it delegates 100%
to the existing `_require_owner`/`_dauth.has_domain_access()` calls rather
than re-deriving its own predicate — implemented that way, it fixes this
drift rather than adding a second authorization surface.

**2 & 5. `/dashboard` redirect — no hidden blockers, but real dependents
needing lockstep updates**, beyond the ~10 template files with plain
`<a href="/dashboard">` links (all safe, just need consistent same-PR
updates):
- `scripts/tools/capture_snapshot.py:87` (`LINK_MAP["/dashboard"]`) and its
  `_replace_dashboard_btn` helper (~line 313) key off `/dashboard` existing
  as the authenticated landing target — needs updating alongside the route
  change.
- `tests/test_auth.py:25` asserts current `/dashboard` behavior directly —
  needs updating to match the new redirect semantics.
- No LinkedIn material, resume draft, or screenshot package was found with
  a hardcoded `/dashboard` dependency — the spec's own claim (handoff § "Technical
  confirmation requested," item 5) holds.
- `docs/specs/spec_landing_page_uptime_2026-06-13.md` is an older spec built
  around keeping `/dashboard` alive as the authenticated app when the
  landing page moves to a static host. Its premise is superseded by this
  v2 spec's approach (workspace access folded into `/` directly). Recommend
  marking it superseded once this ships, so a future reader isn't misled.

**3. `/preview/*` redirect — safe, but surfaces a real pre-existing drift
bug worth knowing before touching this code.** Flask's `/preview` route
(`app.py:213-225`) serves from `minimoi_portal/static/preview/`
(`BASE_DIR / "static" / "preview"`). But `scripts/tools/capture_snapshot.py`
— the tool meant to populate what `/preview/*` serves — writes to
`static/public/preview/` at the repo root instead (a different directory;
its `REPO_ROOT` and `PREVIEW_DIR` constants were fixed for the `tools/` →
`scripts/tools/` move earlier tonight, but the *target* directory itself
has apparently been wrong relative to what Flask serves for some time,
independent of that move). Both directories currently exist with matching
subfolder structure, so nothing is broken today, but: (a) don't assume
re-running the capture script updates live `/preview/*` content, and (b)
for this spec's § 5 requirement to "preserve the old capture assets
temporarily for rollback," the directory that actually matters to preserve
is `minimoi_portal/static/preview/` — the one Flask actually serves — not
`static/public/preview/`.

**4. Docker build asset inclusion — confirmed safe.**
`docker/Dockerfile.portal` uses `COPY . .` (whole-repo copy); `.dockerignore`
excludes `docs/screenshots/` and `_working/` but has no pattern touching
`minimoi_portal/static/` or `minimoi_portal/templates/`. New landing/tour
assets placed under either will ship correctly. One caveat: `docs/screenshots/2026-07-21/`
already has curated, current screenshots for Curator, German, Portuguese,
and Guild that could plausibly seed `/tour` without a fresh capture pass —
but since `docs/screenshots/` itself is excluded from the Docker build,
any images used from there need to be copied into `minimoi_portal/static/`
(or wherever `/tour` ends up serving from) as part of implementation.

**Additional, non-blocking observations:**
- German's guest access is an inline hardcoded path-allowlist per tier
  (`app.py:632`), not per-guest explicit grants like Portuguese's
  `has_domain_access()` table. Both are real and functioning, but not
  literally the same mechanism — worth representing faithfully in the new
  registry rather than assuming uniformity.
- `proxy.py`'s injected nav bar hides the CoS link only for guest tier
  (`is_guest`, ~line 100), not for other non-owner authenticated tiers
  (e.g. `admin`). The actual `/app/cos` route is correctly `@_require_owner`-gated
  (`app.py:592-604`) — no security gap — just a cosmetic nav-visibility
  inconsistency. Not required by this spec (which only requires hiding CoS
  from signed-out/public/guest contexts), but worth tightening to
  owner-only while touching this logic for the registry anyway.

**Repository state confirmed:** PRs #107, #108, #109 (repository root
consolidation) are merged and deployed; `origin/main` is clean and current
for implementation to branch from.
