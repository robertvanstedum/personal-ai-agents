# Guest Tour Repeatable Screenshot Capture

- **File:** `docs/specs/spec_guest_tour_interaction_capture_2026-07-30.md`
- **Version:** v1
- **Date:** July 30, 2026
- **Status:** Approved by Robert — spec ready; implementation not started
- **Scope:** Repeatable high-quality screenshots for the public `/tour`
- **Reference:** `docs/specs/spec_minimoi_front_door_guest_tour_v2_2026-07-22.md`

## 1. Purpose

Replace manual phone screenshot preparation with a small, understandable
capture script.

The script opens the real dev application, follows an approved flow, and takes
a screenshot at named checkpoints. The resulting images slot into the public
tour in the declared order.

The first version is intentionally limited:

- Still images only.
- No public video.
- No voice recording or playback.
- No general user-session recorder.
- No production capture.
- One Portuguese reading flow as the proof of concept.

The current production tour remains in place until the replacement images and
viewer behavior are approved.

## 2. How a Screenshot Is Captured

The capture utility runs a normal browser on the MacBook or in a controlled
automation environment.

For each device profile it:

1. Opens the dev site.
2. Signs in using a dedicated capture account or approved local session.
3. Sets a fixed webpage viewport.
4. Clicks or types the actions declared in the scenario.
5. Waits for the expected page element to appear.
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

Desktop capture uses one approved fixed viewport and the same lossless-first
process.

## 4. Scenario and Checkpoints

A scenario is a short, readable list of actions and screenshots.

Illustrative structure:

```yaml
id: portuguese-reading-general-interest
domain: portuguese
device: mobile
start_url: /app/portuguese

article:
  title: "Approved general-interest article"
  topic: culture

steps:
  - open: landing
  - screenshot: 01-portuguese-landing

  - click: Leitura
  - wait_for: reading-categories
  - screenshot: 02-portuguese-reading-categories

  - choose_article: "Approved general-interest article"
  - screenshot: 03-portuguese-reading-list

  - open_article: "Approved general-interest article"
  - wait_for: article-body
  - screenshot: 04-portuguese-reading-article

  - select_text: "approved phrase"
  - request_translation: true
  - wait_for: translation-result
  - screenshot: 05-portuguese-translation
```

The exact selector names will be based on the existing application. The
scenario should remain readable to someone who is not maintaining the browser
automation code.

A checkpoint is simply a line that says, in effect:

> The page is now in the state we want to show; save an image here.

## 5. First Proof of Concept

The first scenario covers Portuguese reading only.

### Article choice

Before running the scenario, Robert selects or approves one article that is:

- General interest.
- Relevant to the language-learning context.
- Not strongly political.
- Long enough to demonstrate reading and translation.
- Stable enough to remain available while the tour assets are reviewed.
- Free of personal or sensitive information.

The article title or stable identifier is stored in the scenario. The script
must fail clearly if it cannot find the selected article; it must not silently
choose a different one.

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

The capture run generates a small manifest:

```json
{
  "domain": "portuguese",
  "scenes": [
    {
      "order": 1,
      "mobile": "01-portuguese-landing.webp",
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

The person running the utility should not need to crop or rename files
manually.

The expected operation should be similar to:

```text
run-tour-capture portuguese-reading
```

The exact command may differ, but it must be documented in a short README and
remain one command after initial setup.

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

The utility must verify:

- Scenario format is valid.
- Selected article exists.
- Expected page elements appear before each screenshot.
- Output dimensions match the selected device profile.
- All expected files were generated.
- Images contain no embedded EXIF metadata.
- Manifest references real generated files.
- Filenames and ordering are stable between runs.
- A failed step stops the run with a clear explanation.

Manual review confirms:

- Text is sharp and readable.
- No browser chrome is embedded.
- No important content is cut off.
- The sequence tells a coherent story.
- One representative sequence looks correct on iPhone and Galaxy.

## 12. Delivery Phases

### Phase 1 — Capture proof of concept

- Implement the one-command capture utility.
- Implement the Portuguese reading scenario.
- Generate the five initial mobile checkpoints.
- Generate contact sheet and review page.
- Do not change the public viewer.

### Phase 2 — Desktop and viewer

- Add the matching desktop scenario.
- Add manifest-driven tour images.
- Hide the format toggle on mobile.
- Retain the toggle on desktop.
- Add the information control.
- Deploy to dev for iPhone and Galaxy review.

### Phase 3 — Additional flows

- Add Portuguese writing.
- Add Portuguese transcript and coaching stills.
- Convert other approved domains one flow at a time.

## 13. Definition of Done

- [ ] One documented command runs the Portuguese reading capture.
- [ ] The script selects the approved general-interest article.
- [ ] Screenshots are taken at named checkpoints.
- [ ] Mobile raw images are 1170 × 2532 unless the approved profile changes.
- [ ] WebP images pass visual quality review.
- [ ] No browser chrome is captured.
- [ ] No manual cropping or renaming is required.
- [ ] A manifest, contact sheet, and review page are generated.
- [ ] A failed checkpoint produces a clear error.
- [ ] Current production tour remains unchanged.
- [ ] Robert approves the generated images and implementation diff before any
      viewer or production change.
