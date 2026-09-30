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

## Scripted local review packs (sample data)

For a review pack of pages that normally need Robert's login, serve this
checkout's portal locally with sample data and capture it with
`auth_profile: "none"`. Nobody signs in to dev or production, no secret is
read, and no model is called.

```bash
# 1. The local sample portal (loopback only; Ctrl-C stops it)
python -m scripts.tools.tour_capture.local_sample --port 8791

# 2. Each scenario (the Guild 1.1 pack: scenarios/guild_1_1_*.json)
for s in desktop narrow tablet phone; do
  python -m scripts.tools.tour_capture.cli guild-1-1-$s \
    --base-url http://127.0.0.1:8791 --headless --browser-channel chrome
done

# 3. One PDF: cover, then one captioned page per scene
python -m scripts.tools.tour_capture.review_pdf RUN_DIR [RUN_DIR ...] \
  -o OUT.pdf --title "..."
```

The Guild pack sits with this tool rather than in `minimoi_portal/guild_ui/`,
whose files must never mention sample data (`test_no_sample_in_real_mode`).

`local_sample` reuses the Shop floor browser harness's set-up
(`tests/guild/shop_floor`: the sample queue, the floor store on SQLite, the
owner): every loopback request is signed in as the sample owner, anything
else gets 403, and the server binds 127.0.0.1 only. Master Craftsman talks to
a scripted relay in the same process; CoS runs behind its real `/app/cos`
proxy against a stand-in cos-scheduler that echoes the message and a stand-in
voice adapter (no microphone, no provider). Captions must say the data is
sample data.

Scenario steps for local captures (refused with `owner_session`, except
`press`):

| Step | What it does |
|---|---|
| `{"sample": "reset"}` | fresh sample: queue, empty floor, no conversations, MC off, seeded Workshop |
| `{"sample": "queue", "args": {"variant": "quiet"}}` | nothing blocked (`default` restores) |
| `{"sample": "mc", "args": {"mode": "stream", "script": [0.3, "## Plan\n", "WAIT", "- two"], "tokens": 64}}` | MC on a scripted relay: `off`, `turns`, `stream` or `down`; `"fail": 502` makes every turn fail; text is a delta, a number a pause, `"WAIT"` holds until `mc_release` or Stop |
| `{"sample": "mc_release"}` | release a held stream |
| `{"sample": "postit", "args": {"text": "...", "author": "mc"}}`, `{"sample": "continue", "args": {"item": 12, "label": "#12 Floor API"}}` | floor content |
| `{"sample": "voice", "args": {"boot": "ok"}}` | the CoS voice bootstrap succeeds (`fail` by default) |
| `{"fill": "#mc-input", "value": "..."}` | type into a field |
| `{"press": "Escape"}` (optional `"selector"`) | press one key |
| `{"evaluate": "document.dispatchEvent(new Event('visibilitychange'))"}` | run a script in the page (a poll now, browser storage) |
| `{"route": {"url": "**/api/v1/floor", "status": 503, "body": {...}}}`, `{"route": {"url": "...", "abort": true}}`, `{"unroute": "**/api/v1/floor"}` | stub or drop one request pattern |

Also: `{"click": "...", "navigates": true}` waits for the page the click
loads; `wait_for` takes `"text"` (an element matching the selector must show
it); `"mobile_emulation": true` on a scenario emulates a touch phone. A
loopback capture refuses every request that is not to localhost or 127.0.0.1
(web fonts, analytics), so system fonts may show.

## Safety

- The runner accepts only localhost, `127.0.0.1`, or `dev.minimoi.ai`.
- `auth_profile: none` is refused for any host other than localhost or `127.0.0.1`,
  and such a capture's browser can reach only localhost or `127.0.0.1`.
- `fill`, `evaluate`, `route`, `unroute` and `sample` steps are refused for
  `owner_session` scenarios, so a scripted run never types into, scripts or
  fakes a real dev page.
- Authentication state and output remain under ignored `_working/`.
- No production capture or write path exists.
- A failed run retains a diagnostic screenshot and structured report.
- Promotion into `minimoi_portal/static/tour/` remains a separate reviewed
  repository change.
