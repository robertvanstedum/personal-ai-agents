# Tour Capture Utility

This utility creates review-only screenshots from the real dev application.
It supports the original Portuguese reading checkpoint flow plus interactive
desktop capture for Portuguese, German, Curator, Guild, and Chief of Staff.
Capture runs never deploy assets or change the public tour automatically.

## One-time setup

Install the root requirements and Chromium:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

Store the owner credentials in environment variables or the local Keychain
service `minimoi-tour-capture`:

```text
MINIMOI_CAPTURE_OWNER_USERNAME
MINIMOI_CAPTURE_OWNER_PASSWORD
```

Every scenario uses Robert's real owner account so the captured flow matches
normal daily use across Portuguese, German, Curator, Guild, and Chief of Staff.
Credentials remain local and are never written to a scenario, report, or image.
The username defaults to `robert`; store the password once without putting it
in shell history:

```bash
keyring set minimoi-tour-capture minimoi_capture_owner_password
```

## Validate without a browser

```bash
python -m scripts.tools.tour_capture.cli portuguese-reading --dry-run
```

## Run a capture

The structured Portuguese mobile proof of concept captures five declared
checkpoints around three operator pauses:

```bash
python -m scripts.tools.tour_capture.cli portuguese-reading \
  --base-url https://dev.minimoi.ai
```

The browser opens visibly. Follow each short operator instruction and press
Enter in the terminal when the displayed state is ready. Choose a current,
public-safe general-interest article; the scenario does not seed or pin one.

The desktop scenarios use an interactive free-capture step. Browse normally,
press Enter for each frame you want to keep, and type `done` when the sequence
is complete:

```bash
python -m scripts.tools.tour_capture.cli portuguese-desktop --base-url https://dev.minimoi.ai
python -m scripts.tools.tour_capture.cli german-desktop --base-url https://dev.minimoi.ai
python -m scripts.tools.tour_capture.cli curator-desktop --base-url https://dev.minimoi.ai
python -m scripts.tools.tour_capture.cli guild-desktop --base-url https://dev.minimoi.ai
python -m scripts.tools.tour_capture.cli cos-desktop --base-url https://dev.minimoi.ai
```

For a desktop free-capture scenario, `--dry-run` reports zero predetermined
screenshots because the operator chooses the number of frames during the run.

Successful output is written beneath:

```text
_working/tour-capture/portuguese-reading/<UTC timestamp>/
```

Each other scenario uses its own scenario ID in the same directory structure.

Open the printed `review.html` path. The folder also contains raw PNGs,
optimized WebPs, `manifest.json`, `report.json`, and `contact-sheet.webp`.

## Generic review capture

The same runner can capture any local prototype or page set and produce one
PDF to hand to a reviewer. Put a scenario next to the prototype and set
`auth_profile` to `none`: no login runs and no stored session is loaded. The
runner allows `none` only when the base URL host is `localhost` or
`127.0.0.1`, and refuses it for `dev.minimoi.ai`. With `none`, `start_path`
and every `goto` may be any absolute path beginning with `/`; `owner_session`
keeps the `/app/...` or `/guild...` rule.

```json
{
  "id": "prototype-review",
  "domain": "prototype",
  "device_profile": "desktop",
  "auth_profile": "none",
  "start_path": "/index.html",
  "steps": [
    {"goto": "/index.html"},
    {"wait_for": "body"},
    {"screenshot": "landing", "title": "Landing", "description": "First screen.", "alt": "Prototype landing page"},
    {"click": "[data-tab='detail']"},
    {"wait_for": "#detail"},
    {"screenshot": "detail", "title": "Detail", "description": "Detail tab open.", "alt": "Prototype detail tab"}
  ]
}
```

`device_profile` is `desktop` (1440×900 @2x) or `mobile` (390×844 @3x). To
use another size, add a viewport and name the profile with any slug, for
example `"device_profile": "laptop", "viewport": {"width": 1280, "height": 800,
"device_scale_factor": 2}`.

Validate, then run it headless and write `review.pdf` into the run directory:

```bash
python -m scripts.tools.tour_capture.cli --scenario-file path/to/prototype_review.json --dry-run
python -m scripts.tools.tour_capture.cli --scenario-file path/to/prototype_review.json \
  --base-url http://127.0.0.1:8000 --headless --pdf
```

Give either a built-in scenario name or `--scenario-file`, not both.
`--output-root` changes where the run directory is written.

To bundle several runs (for example desktop and mobile) into one PDF:

```bash
python -m scripts.tools.tour_capture.review_pdf \
  _working/tour-capture/prototype-review/<UTC> \
  _working/tour-capture/prototype-review-mobile/<UTC> \
  -o _working/tour-capture/prototype-review.pdf --title "Prototype review"
```

The PDF has a cover page (title, date, source URLs, scenarios, and a note
that local captures may show simulated or sample data), then one page per
scene in run order: the screenshot scaled to fit without distortion and a
caption with title, description, device profile, viewport, and capture time.

**Browser.** Add `--browser-channel chrome` (or set `MINIMOI_CAPTURE_BROWSER_CHANNEL=chrome`) to use the installed Google Chrome instead of Playwright's downloaded Chromium. This avoids a browser download when a venv's Playwright expects a different build than the one cached.

## Safety

- The runner accepts only localhost, `127.0.0.1`, or `dev.minimoi.ai`.
- `auth_profile: none` is refused for any host other than localhost or `127.0.0.1`.
- Authentication state and output remain under ignored `_working/`.
- No production capture or write path exists.
- A failed run retains a diagnostic screenshot and structured report.
- Promotion into `minimoi_portal/static/tour/` remains a separate reviewed
  repository change.
