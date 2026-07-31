# Look Inside Capture Guide

## Purpose

This guide explains how to create a fresh, repeatable set of **Look Inside**
screenshots from mini-moi as it is actually used. The capture utility does not
simulate the meaningful parts of a session. Robert still performs each human
action declared by a scenario. The Portuguese proof of concept preserves
content and phrase selection; later writing and voice scenarios can preserve
typing, correction, and speech in the same way. The utility handles the
repetitive work around those moments: consistent viewport, checkpoint capture,
file naming, image optimization, and review packaging.

The first available scenario is the Portuguese reading flow. The same pattern
is intended for German, Curator, Guild, and Chief of Staff.

## What the run produces

Each successful run creates one timestamped folder under:

```text
_working/tour-capture/<scenario>/<UTC timestamp>/
```

The folder contains:

- `review.html` — the first file to open when reviewing the flow
- `contact-sheet.webp` — the complete flow at a glance
- `raw/` — lossless PNG captures at the approved device resolution
- `optimized/` — metadata-free WebP files ready for tour review
- `manifest.json` — stable titles, descriptions, filenames, and dimensions
- `report.json` — run status and any failure details
- `diagnostics/` — retained evidence when a run stops early

Nothing is copied into the public tour automatically. Captures remain review
assets until Robert approves a later promotion step.

## One-time setup

Run these commands from the repository root:

```bash
source venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

The tool signs in with Robert's normal owner account so every domain is shown
as it is really used. The username defaults to `robert`. Store the password in
the local Keychain once:

```bash
keyring set minimoi-tour-capture minimoi_capture_owner_password
```

The password remains in Keychain or the environment. Saved browser state and
captures remain under ignored local storage and are never committed to Git.
Secrets are never written to a scenario, screenshot, manifest, or report.

## Before each capture

Use this short checklist:

1. Confirm the intended flow works normally on `https://dev.minimoi.ai`.
2. Choose a current, public-safe subject that represents normal use.
3. Avoid personal, confidential, or overly political material in the scene.
4. Close unrelated prompts or transient messages before continuing a scene.
5. Decide which human moments the scenario should preserve. The current
   Portuguese flow uses selection; later scenarios can declare typing,
   correction, speech, or other authentic interaction.

The content does not need to be permanently pinned. Choosing it during the run
keeps the example current while the automated checkpoints keep the image
production repeatable.

## Validate the Portuguese scenario

Check the scenario before opening a browser:

```bash
python -m scripts.tools.tour_capture.cli portuguese-reading --dry-run
```

Expected result:

```text
valid: portuguese-reading · 5 screenshots · 3 operator pauses
```

## Run the capture

Start the visible browser session:

```bash
python -m scripts.tools.tour_capture.cli portuguese-reading \
  --base-url https://dev.minimoi.ai
```

The browser uses a fixed mobile viewport and signs in with the locally stored
owner session. At each operator pause:

1. Read the instruction shown in the terminal.
2. Use the page normally and create the intended state.
3. Wait for loading, translation, analysis, or other transient UI to finish.
4. Press Enter in the terminal only when the scene is ready to preserve.

For Portuguese reading, the current sequence is:

1. Portuguese landing page
2. Reading categories
3. Current article list chosen by Robert
4. Selected article open with full text loaded
5. A selected phrase with its completed English translation

The article title and URL are recorded after selection and checked again before
the translation scene so the flow cannot silently switch articles.

## Review the result

The completed command prints the absolute path to `review.html`. Open it and
review the scenes in order.

Confirm that:

- every mobile image is exactly `1170 × 2532`
- the webpage is visible without phone status bars or external browser chrome
- text is readable and no horizontal scrollbar appears
- the selected content remains consistent across related scenes
- no loading state, toast, error, or unfinished translation is visible
- the scene titles and descriptions tell a coherent story
- the WebP images look visually equivalent to the raw PNG files

Keep the run folder intact during review. The manifest is the mechanical map
for later placing approved images into the Look Inside sequence.

## If a run fails

The tool stops at the checkpoint that did not become ready and reports the
failed scene. Review `report.json` and the `diagnostics/` folder. The latest raw
capture is retained when possible.

Common recovery steps:

- confirm or update the Keychain password if automatic owner reauthentication
  fails
- confirm the dev page and selected content load without an error
- rerun after a temporary network or model delay
- choose another current article if the original source is unavailable
- do not bypass a failed readiness check just to obtain a screenshot

A failed run does not alter production or replace existing tour assets.

## Adding the other domains

Each additional domain should receive its own small scenario file with:

- one device profile
- stable checkpoint selectors supplied by the application
- short operator instructions only where real human action matters
- ordered scene titles, descriptions, and alt text
- explicit waits for completed—not merely visible—results

The operator experience should stay the same across domains: start one command,
perform the authentic actions at a few declared pauses, then review one packaged
output folder. Additional automation can be introduced later without removing
the human actions that make the Look Inside flow credible.

## Safety boundaries

- Capture only from localhost or `https://dev.minimoi.ai`.
- The tool refuses production and unknown hosts.
- HTTP is permitted only for localhost; dev requires HTTPS.
- Owner credentials stay in Keychain or the environment; browser state stays
  under local ignored storage.
- Generated images remain under ignored `_working/` until explicitly approved.
- Never add credentials, session files, or private captured content to Git.
