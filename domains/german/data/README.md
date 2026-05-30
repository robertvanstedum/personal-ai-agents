# German Language Practice Domain

Daily spoken German practice using AI personas. Voice sessions on iPhone via Grok → transcript reviewed by Claude Sonnet → feedback, Anki flashcards, and next-day lesson plan generated automatically.

**Target:** B1 spoken confidence for Vienna trip (May 2026)  
**Status:** Active — see `progress.json` for current stats

## Quick Commands

```bash
# Parse a new transcript
python3 ../../parse_transcript.py --input ../../test_fixtures/sample_transcript.txt --base-dir .

# Review latest session
python3 ../../reviewer.py --latest --base-dir .

# Check status
python3 ../../status.py --base-dir .
```

## Folder Structure

- `config/` — domain settings and persona definitions
- `sessions/` — raw transcripts + reviewer output (gitignored)
- `anki/` — daily Anki CSV exports (gitignored)
- `lessons/` — next-day lesson plans (gitignored)
- `progress.json` — cumulative stats (gitignored)
