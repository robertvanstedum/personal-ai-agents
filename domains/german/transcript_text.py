"""Text-only German transcript parsing, without state or provider initialization.

Import parsing normalizes dashes/whitespace and splits inline speakers. Review
parsing retains continuation text and a fallback speaker. These are intentionally
separate contracts; callers own defaults, identifiers and persistence.
"""
import re


HEADER_FIELDS = {"date", "persona", "scenario", "duration", "mode", "drill", "drill-session"}


def parse_header(lines: list) -> dict:
    # Expand lines that contain multiple fields inline (e.g. Grok single-line format:
    # "Date: 2026-04-26 Persona: Klaus Scenario: ..." all on one line)
    field_re = re.compile(
        r'\b(' + '|'.join(re.escape(f) for f in HEADER_FIELDS) + r')\s*:',
        re.IGNORECASE,
    )
    expanded = []
    for line in lines:
        matches = list(field_re.finditer(line))
        if len(matches) <= 1:
            expanded.append(line)
        else:
            for i, m in enumerate(matches):
                end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
                expanded.append(line[m.start():end].strip())

    fields = {}
    for line in expanded:
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().lower()
            if key in HEADER_FIELDS:
                fields[key] = val.strip()
    return fields


# Speaker-name boundaries in compact import transcripts; titles remain optional.
INLINE_SPEAKER_RE = re.compile(
    r'(?:^|(?<=[\s.?!]))'
    r'(?:(?:Dr|Frau|Herr|Prof)\.\s+)?'
    r'(?:[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?)'
    r':'
)


def split_inline_turns(line: str) -> list:
    """Split a single-line conversation dump on speaker-name boundaries."""
    matches = list(INLINE_SPEAKER_RE.finditer(line))
    if len(matches) < 2:
        return [line]
    segments = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
        segments.append(line[m.start():end].strip())
    return segments


def parse_import_turns(lines: list) -> list:
    turns = []
    for line in lines:
        # Normalize em-dash and en-dash to hyphen; collapse Unicode whitespace
        line = line.replace('\u2014', '-').replace('\u2013', '-')
        line = ' '.join(line.split())
        if not line:
            continue
        # Detect single-line dump (multiple speaker prefixes on one line)
        segments = split_inline_turns(line)
        for seg in segments:
            if ":" in seg:
                speaker, _, text = seg.partition(":")
                speaker = speaker.strip()
                text = text.strip()
                if speaker and text:
                    turns.append({"speaker": speaker, "text": text})
    return turns


def parse_review_turns(text: str) -> list:
    """Parse raw transcript into list of {speaker, text} dicts."""
    turns = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if ':' in line:
            speaker, _, body = line.partition(':')
            speaker = speaker.strip()
            body = body.strip()
            if speaker and body and len(speaker) < 40:
                turns.append({"speaker": speaker, "text": body})
                continue
        if turns:
            turns[-1]["text"] += " " + line
        else:
            turns.append({"speaker": "Transcript", "text": line})
    return turns or [{"speaker": "Transcript", "text": text.strip()}]
