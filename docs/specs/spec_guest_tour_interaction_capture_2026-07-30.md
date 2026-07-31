# Guest Tour Repeatable Screenshot Capture

- **File:** `docs/specs/spec_guest_tour_interaction_capture_2026-07-30.md`
- **Version:** v1.2
- **Date:** July 31, 2026
- **Status:** Working draft incorporating Robert's post-review decisions
- **Scope:** Repeatable high-quality screenshots for the public `/tour`
- **Reference:** `docs/specs/spec_minimoi_front_door_guest_tour_v2_2026-07-22.md`
- **Capture engine:** Python Playwright with Chromium

## 1. Purpose

Replace manual phone screenshot preparation with a small, understandable
capture script.

The script opens the real dev application, follows an approved flow, and takes
a screenshot at named checkpoints. The resulting images slot into the public
tour in the declared order.

The first implementation is intentionally limited:

- Still images only.
- No public video.
- No voice recording or playback.
- No general user-session recorder.
- No production capture.
- One Portuguese reading flow as the proof of concept.

The utility itself must not be Portuguese-specific. The planned rollout covers
Portuguese, German, Curator, Guild, and Chief of Staff. Each domain supplies its
own scenario, authentication profile, and named checkpoints while sharing the
same capture, validation, optimization, manifest, contact-sheet, and review
pipeline.

The current production tour remains in place until the replacement images and
viewer behavior are approved.

## 2. How a Screenshot Is Captured

The capture utility uses Python Playwright with Chromium. Playwright is already
present in the project requirements and is the required engine for the first
implementation; Puppeteer or a second browser-automation stack should not be
introduced.

The utility runs on the MacBook or in a controlled automation environment.

For each device profile it:

1. Opens the dev site.
2. Signs in using the authentication profile declared by the scenario.
3. Sets a fixed webpage viewport.
4. Performs declared automatic actions and pauses for declared human actions.
5. Waits for the declared checkpoint state to become ready and stable.
6. Captures the webpage viewport at a named checkpoint.
7. Saves the image with a predictable filename.

The browser's screenshot function captures only the webpage. It does not
include:

- Phone time, signal, Wi-Fi, or battery.
- Safari or Chrome address bars.
- Browser back, refresh, tabs, or overflow controls.

The visitor sees their own browser interface when viewing the tour. Removing a
second, captured browser frame gives the application more room and avoids the
current browser-inside-browser effect.

### Authentication profiles

Authentication is scenario-specific; one guest account cannot represent every
domain:

| Profile | Intended domains | Mechanism |
|---|---|---|
| `language_guest` | Portuguese and German | Optional dedicated dev guest created with the portal's existing `create_guest()` mechanism |
| `owner_session` | Curator, Guild, Chief of Staff, and optionally either language domain | Real portal owner login with ignored local Playwright session state |

No new account tier, token bypass, or domain-specific authentication is
introduced.

- Guest creation is a one-time manual/scripted dev setup step. Its expiry is
  bounded and renewable; no commit or capture run writes portal runtime account
  data.
- Credentials use profile-specific environment or Keychain secrets, never
  scenario files or Git.
- Playwright may create one ignored local authentication-state file per profile,
  outside deployed/static directories.
- An invalid or expired session triggers one fresh login attempt. If login or
  authorization still fails, the run stops before capturing any checkpoint.
- Owner scenarios must declare the exact pages and states to capture. The tool
  must not roam, enumerate, or capture unrelated private owner content.
- Every generated owner-domain image receives the same manual privacy review as
  current tour assets before promotion.
- The runner refuses a production base URL. Capture permits localhost or the
  declared `dev.minimoi.ai` origin only.

## 3. Image Quality

The capture uses a CSS viewport and a higher device scale factor.

Initial mobile profile:

```text
CSS viewport: 390 × 844
Device scale factor: 3
Raw output: 1170 × 2532
```

This produces a sharp image comparable to a modern phone screenshot while
keeping the visible layout at a realistic mobile width.

Quality rules:

- Capture lossless PNG first.
- Produce an optimized WebP for the deployed tour.
- Default WebP quality target: 92, subject to visual review.
- Never stretch an image.
- Do not apply manual per-image trimming unless declared in the scenario.
- Strip EXIF and unnecessary metadata.
- Reject output with unexpected dimensions.
- Keep the lossless source outside the deployed static directory.

Desktop capture is not part of the Phase 1 proof of concept. When Phase 2
begins, its default profile is a 1440 × 900 CSS viewport at device scale factor
2 (2880 × 1800 raw output), unless Robert approves a different profile before
implementation. It uses the same lossless-first process.

### Known visual state

Every run uses the same declared browser state:

- Chromium color scheme: light.
- Browser locale: `pt-BR` for the Portuguese scenario.
- Reduced motion enabled so transitions do not create intermediate frames.
- Browser zoom: 100 percent.
- No open developer tools or browser extensions.
- No unexpected horizontal overflow or scrollbar.
- Loading indicators, transient toasts, menus, and unrelated modals must be
  absent before capture. The script waits for them to clear or closes them
  through an explicit scenario action; it does not hide real product content
  with broad ad hoc CSS.

## 4. Scenario and Checkpoints

A scenario is a short, readable list of actions and screenshots.

Scenarios use JSON because the repository has no YAML dependency. Illustrative
structure:

```json
{
  "id": "portuguese-reading-general-interest",
  "domain": "portuguese",
  "device": "mobile",
  "auth_profile": "language_guest",
  "start_url": "/app/portuguese",
  "steps": [
    {"open": "landing"},
    {"screenshot": "01-portuguese-landing"},
    {"click": "Leitura"},
    {"wait_for": "reading-categories"},
    {"screenshot": "02-portuguese-reading-categories"},
    {
      "operator": "Choose a current category with a suitable article, then press Enter"
    },
    {"wait_for": "article-list"},
    {"screenshot": "03-portuguese-reading-list"},
    {
      "operator": "Open a current general-interest article, then press Enter"
    },
    {"record_current_article": true},
    {"wait_for": "article-body"},
    {"screenshot": "04-portuguese-reading-article"},
    {
      "operator": "Select a useful phrase and request its translation, then press Enter"
    },
    {"wait_for": "translation-result"},
    {"screenshot": "05-portuguese-translation"}
  ]
}
```

The exact selector names will be based on the existing application. The
scenario should remain readable to someone who is not maintaining the browser
automation code.

A checkpoint is simply a line that says, in effect:

> The page is now in the state we want to show; save an image here.

### Human staging and automatic capture

The automation boundary is intentional:

- Robert may choose a current article, write a real comment or interest, type
  and correct an error, or conduct a real voice interaction.
- A scenario expresses these moments as clear `operator` pauses in a headful
  Playwright browser. The terminal shows one short instruction and resumes when
  Robert presses Enter.
- On resume, the runner validates the expected page state and records relevant
  non-secret context such as the chosen article title and URL in the run report.
- From that point onward, screenshots, dimensions, filenames, optimization,
  manifest generation, contact sheet, review page, and failure diagnostics are
  automatic.

The goal is not to automate judgment or fabricate a fixed demonstration. The
goal is to make everything after the authentic human action smooth and
repeatable. More automation may be added later only where it reduces effort
without making the flow less real.

### Readiness and failure behavior

Each checkpoint declares one stable expected element or state. The
implementation should prefer a dedicated semantic attribute such as
`data-tour-capture="reading-categories"`; when one does not exist, the
implementation may add a narrowly scoped attribute to the application rather
than depend on fragile CSS layout or visible text.

Before saving an image, Playwright must:

1. Wait for the page's initial DOM content to load.
2. Wait for the checkpoint selector to be visible.
3. Wait for web fonts to be ready.
4. Confirm declared loaders, toasts, and blocking overlays are absent.
5. Allow two animation frames after the final state change before capture.

`networkidle` may be used as an additional signal, but it is not sufficient by
itself because background requests can make it unreliable.

The default timeout is 20 seconds per action or checkpoint. If the expected
state does not appear, the entire run stops, returns a non-zero exit code, and
reports:

- Scenario and checkpoint ID.
- Last completed action.
- Missing or unexpected selector/state.
- Current URL.
- A diagnostic screenshot and Playwright error text.

Partial output is retained in that run's review directory for diagnosis, but
must never be presented as a complete capture set.

## 5. First Proof of Concept

The first scenario covers Portuguese reading only.

### Article choice

During the run, Robert selects one current article that is:

- General interest.
- Relevant to the language-learning context.
- Not strongly political.
- Long enough to demonstrate reading and translation.
- Free of personal or sensitive information.

The scenario does not pin or seed an article and does not require database
writes. After Robert opens the article, the runner records its title and URL,
verifies that the same article remains open at the following checkpoints, and
fails rather than silently switching content. Staleness is handled by human
selection at capture time, not by preserving an old fixture.

### Initial checkpoints

1. Portuguese landing.
2. Reading categories.
3. Article list with the selected article visible.
4. Open article.
5. Translation result.

The final number may be reduced after reviewing the contact sheet. The goal is
to show a coherent flow, not every interaction.

## 6. How Images Enter the Tour

Each generated image has:

- A stable filename.
- A sequence number.
- A short scene title.
- A short explanation.
- Accessible alt text.
- Mobile or desktop designation.

Filenames follow this exact pattern:

```text
{order:02d}-{domain}-{scene}-{profile}.{extension}
```

Examples:

```text
01-portuguese-landing-mobile.png
01-portuguese-landing-mobile.webp
05-portuguese-translation-mobile.webp
01-portuguese-landing-desktop.webp
```

The sequence number is the tour step order. Scenario IDs and scene slugs use
lowercase ASCII letters, numbers, and single hyphens. The PNG and WebP for one
scene share the same basename. A run must fail on duplicate order numbers,
duplicate output names, skipped required checkpoints, or manifest/file
mismatches.

The capture run generates a small manifest:

```json
{
  "domain": "portuguese",
  "scenes": [
    {
      "order": 1,
      "mobile": "01-portuguese-landing-mobile.webp",
      "title": "Enter the Portuguese immersion space",
      "alt": "Meu Português landing page in a mobile viewport"
    }
  ]
}
```

The future tour viewer reads this manifest rather than requiring every image
and caption to be wired into the template by hand.

For the proof of concept, generated files remain in a review directory. They
do not replace the current tour until Robert approves the image set and the
implementation diff.

## 7. Review Output

One command should produce:

- The lossless screenshots.
- The optimized WebP images.
- The scene manifest.
- A contact sheet showing the complete sequence.
- A simple review page showing each image at mobile and desktop sizes.
- A report with dimensions and file sizes.

Review output is written beneath a run-specific directory such as:

```text
_working/tour-capture/portuguese-reading-general-interest/2026-07-31T120000Z/
  raw/
  optimized/
  diagnostics/
  manifest.json
  report.json
  contact-sheet.webp
  review.html
```

The run directory is review material and is not deployed automatically. Later,
an explicit approved promotion step may copy only manifest-referenced optimized
assets into the tour static directory.

The person running the utility should not need to crop or rename files
manually.

The expected operation should be similar to:

```text
run-tour-capture portuguese-reading
```

The exact command may differ, but it must be documented in a short README and
remain one command after initial setup.

The command prints the absolute review-page path on success and identifies the
failed checkpoint and diagnostics directory on failure.

## 8. Responsive Tour Behavior

This capture work supports the separately reviewed viewer behavior:

### On a phone

- Automatically show the mobile image.
- Do not show a Desktop/Mobile toggle.
- Use the available viewer space without stretching the image.
- Show previous, next, close, and information controls as overlays.
- Put the scene explanation behind the information control.

### On desktop

- Start with the desktop image.
- Show the Desktop/Mobile toggle when both images exist.
- Keep the scene explanation visible when space permits.

The viewer is not part of the first capture proof of concept. The first phase
only proves that the images can be generated cleanly and repeatedly.

## 9. Later Interaction Flows

Writing correction and voice-derived results are deferred until the reading
capture is accepted.

When added:

- A writing script may intentionally type an approved error, capture it,
  correct it, submit it, and capture the result at declared checkpoints.
- A voice session may be performed live against dev, but the public tour shows
  only the resulting transcript and coaching screenshots.
- Robert's voice is never retained, committed, or published.

No event recorder or video pipeline is required to implement these later
flows. Each flow remains an explicit scenario with named screenshot
checkpoints.

## 10. Privacy and Safety

- Capture only against dev or an approved local environment.
- Never place credentials in the scenario.
- Never capture login or password fields.
- Use only approved public-safe content.
- Keep cookies and authentication state out of Git.
- Verify every generated image before deployment.
- Do not capture private notifications, names, messages, or operational data.
- Do not change production as part of a capture run.

## 11. Tests

Testing is deliberately split so the normal repository suite remains fast and
does not unexpectedly require a running dev server or installed browser.

### CI-safe unit tests

These run in the default test suite with no network, database, live server, or
Playwright browser:

- Scenario format is valid.
- Filename pattern, ordering, and duplicate detection are correct.
- Output dimensions match the selected device profile.
- All expected files were generated.
- Images contain no embedded EXIF metadata.
- Manifest references real generated files.
- Filenames and ordering are stable between runs.
- Every mobile PNG and WebP is exactly 1170 × 2532 for the approved profile.
- Every optimized image decodes successfully and preserves the raw aspect
  ratio without stretching.
- Small checked-in fixture images exercise conversion and metadata validation.

### Dev-only smoke test

The end-to-end capture is an explicitly named manual/dev smoke test excluded
from default test collection. It requires the dev portal, an authentication
profile appropriate to the scenario, and installed Chromium. It verifies:

- Real portal login and the proxied domain route succeed.
- Operator pauses resume cleanly and the selected article identity remains
  consistent across later checkpoints.
- Expected page elements appear before every screenshot.
- A failed action or checkpoint stops the run with a clear explanation and
  diagnostics.
- The page has no horizontal overflow at capture time.
- The active color scheme, locale, viewport, and device scale match the
  scenario profile.
- No incomplete spinner, toast, blocking modal, or error banner is visible.

The README documents the separate smoke-test command and its prerequisites.
CI must not silently skip a test presented as end-to-end; the smoke test is
openly classified as a local acceptance gate.

### Manual review

Manual review of the smoke-test output confirms:

- Text is sharp and readable.
- No browser chrome is embedded.
- No important content is cut off and no unexpected scrollbar is visible.
- WebP quality at the target of approximately 92 is visually indistinguishable
  from the source PNG at normal viewing size.
- The sequence tells a coherent story.
- One representative sequence looks correct on iPhone and Galaxy.

## 12. Delivery Phases

### Phase 1 — Capture proof of concept

- Implement the one-command capture utility.
- Implement the Portuguese reading scenario.
- Generate the five initial mobile checkpoints.
- Generate contact sheet and review page.
- Do not change the public viewer.

### Phase 2 — All-domain mobile rollout

- Add German, Curator, Guild, and Chief of Staff mobile scenarios.
- Use `language_guest` where the real guest experience is appropriate and
  `owner_session` where the domain is owner-only.
- Allow declared operator pauses for current content, comments, corrections,
  and other authentic state preparation.
- Generate one consistent review package across all five domains.
- Continue to leave the public viewer unchanged until the complete image set is
  approved.

### Phase 3 — Desktop and viewer

- Add the matching desktop scenario.
- Add manifest-driven tour images.
- Hide the format toggle on mobile.
- Retain the toggle on desktop.
- Add the information control.
- Deploy to dev for iPhone and Galaxy review.

### Phase 4 — Additional flows and selective automation

- Add Portuguese writing.
- Add Portuguese transcript and coaching stills.
- Add further approved domain flows one at a time.
- Automate additional setup actions only after the operator-assisted process is
  proven smooth and repeatable.

## 13. Definition of Done

- [ ] One documented command runs the Portuguese reading capture.
- [ ] The operator can choose a current general-interest article without
      editing the scenario.
- [ ] The runner records and validates the chosen article through the remaining
      checkpoints.
- [ ] Screenshots are taken at named checkpoints.
- [ ] Mobile PNG and WebP images are 1170 × 2532 unless the approved profile
      changes.
- [ ] WebP images pass visual quality review.
- [ ] No browser chrome is captured.
- [ ] The run uses the declared light theme, Portuguese locale, and fixed
      profile.
- [ ] No unexpected horizontal overflow, scrollbar, loading state, toast, or
      blocking overlay appears in an approved image.
- [ ] No manual cropping or renaming is required.
- [ ] A manifest, contact sheet, and review page are generated.
- [ ] A failed checkpoint produces a clear error.
- [ ] Current production tour remains unchanged.
- [ ] Robert approves the generated images and implementation diff before any
      viewer or production change.

## 14. Post-review Implementation Direction

Claude Code reviewed v1.1 against the actual routes, templates, authentication,
dependencies, test conventions, and existing Playwright scripts. Robert then
clarified that authentic content selection may remain human while capture and
image production should become smooth and repeatable.

### Accepted technical direction

- Python Playwright with Chromium.
- A new self-contained `scripts/tools/tour_capture/` package rather than changing
  the existing documentation/snapshot utilities.
- JSON scenarios; no YAML dependency.
- Pillow added explicitly for WebP conversion, metadata removal, dimension
  validation, and contact-sheet generation.
- Additive `data-tour-capture` attributes only where stable selectors do not
  already exist.
- CI-safe logic/image tests separated from a manual dev browser smoke test.
- Run output remains under ignored `_working/tour-capture/` until approved.

### Decisions changed after review

- Authentication is not one universal capture guest. Portuguese and German may
  use a guest profile; Curator, Guild, and Chief of Staff require an owner
  profile.
- The runner does not pin, seed, or preserve an article in Postgres. Robert
  chooses a current article during the run.
- Real comments, interests, writing errors/corrections, and later voice sessions
  may be staged by Robert through explicit operator pauses.
- Repeatability applies to validation, screenshot checkpoints, image quality,
  naming, manifests, contact sheets, review pages, and diagnostics—not to
  replacing authentic human choices.

### Recommended implementation layout

```text
scripts/tools/tour_capture/
  __init__.py
  cli.py
  scenario.py
  auth.py
  runner.py
  readiness.py
  imaging.py
  manifest.py
  scenarios/
    portuguese_reading.json
tests/
  test_tour_capture_scenario.py
  test_tour_capture_imaging.py
_working/tour-capture/
```

The documented command should follow the repository's existing module pattern:

```text
python -m scripts.tools.tour_capture.cli portuguese-reading
```

### Reviewable build sequence

1. Add the capture package skeleton, JSON validation, imaging pipeline, manifest
   generation, Pillow dependency, and CI-safe unit tests.
2. Add the scenario authentication profiles, operator-pause support, and
   ignored local session handling.
3. Add only the Portuguese selectors and reading scenario, then run the dev
   smoke test and review its generated contact sheet/page.
4. After the proof of concept is accepted, add German, Curator, Guild, and Chief
   of Staff scenarios one at a time without changing the shared pipeline.

The current public tour and viewer remain outside the Phase 1 implementation
diff. Robert reviews the implementation diff and generated assets before any
promotion or viewer work.

### Future Curator flow reference

The working draft `_working/curator-depth-ladder-example.md` is compatible with
the same ordered scene/manifest model. It becomes a Curator scenario in the
all-domain rollout, not part of the Portuguese proof of concept.
