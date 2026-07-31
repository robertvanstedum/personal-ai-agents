# Tour Capture Utility

Phase 1 creates review-only screenshots from the real dev application. It does
not change the public tour or deploy assets.

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

## Run the Portuguese proof of concept

```bash
python -m scripts.tools.tour_capture.cli portuguese-reading \
  --base-url https://dev.minimoi.ai
```

The browser opens visibly. Follow each short operator instruction and press
Enter in the terminal when the displayed state is ready. Choose a current,
public-safe general-interest article; the scenario does not seed or pin one.

Successful output is written beneath:

```text
_working/tour-capture/portuguese-reading/<UTC timestamp>/
```

Open the printed `review.html` path. The folder also contains raw PNGs,
optimized WebPs, `manifest.json`, `report.json`, and `contact-sheet.webp`.

## Safety

- The runner accepts only localhost, `127.0.0.1`, or `dev.minimoi.ai`.
- Authentication state and output remain under ignored `_working/`.
- No production capture or write path exists.
- A failed run retains a diagnostic screenshot and structured report.
