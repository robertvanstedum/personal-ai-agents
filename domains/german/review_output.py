"""German tutor response parsing and schema validation, without runtime dependencies."""
import json
import re
import sys

ERROR_TYPES = ["gender", "word_order", "missing_article", "verb_conjugation", "vocabulary", "register"]


def valid_review_output(value: object) -> bool:
    """Validate the tutor's documented shape; preserve extension fields unchanged."""
    if not isinstance(value, dict):
        return False
    if not all(isinstance(value.get(k), str) for k in ("overall_summary", "next_focus")):
        return False
    counts = value.get("error_type_counts")
    if not isinstance(counts, dict) or set(counts) != set(ERROR_TYPES):
        return False
    if not all(type(n) is int and n >= 0 for n in counts.values()):
        return False
    errors = value.get("errors")
    if not isinstance(errors, list):
        return False
    for error in errors:
        if not isinstance(error, dict) or error.get("type") not in ERROR_TYPES:
            return False
        if not all(isinstance(error.get(k), str) for k in ("original", "correction", "explanation", "context")):
            return False
    vocab = value.get("vocabulary_highlights")
    if not isinstance(vocab, list):
        return False
    for card in vocab:
        if not isinstance(card, dict) or not all(isinstance(card.get(k), str) for k in ("german", "english", "note")):
            return False
        tags = card.get("tags")
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            return False
    strengths = value.get("strengths")
    return isinstance(strengths, list) and all(isinstance(s, str) for s in strengths)


def empty_reviewer_output() -> dict:
    return {
        "overall_summary": "Review parse failed — see reviewer_raw_output in session file.",
        "errors": [],
        "error_type_counts": {t: 0 for t in ERROR_TYPES},
        "vocabulary_highlights": [],
        "strengths": [],
        "next_focus": "",
    }


def strip_llm_fences(text: str) -> str:
    """Strip markdown code fences (```json ... ``` or ``` ... ```) from LLM output."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
    return raw.strip()


def parse_llm_response(text: str) -> tuple[dict, bool]:
    """Return (parsed_dict, success). On failure returns fallback struct."""
    try:
        value = json.loads(strip_llm_fences(text))
        if valid_review_output(value):
            return value, True
    except json.JSONDecodeError:
        pass
    # Greedy match — .*? would stop at first } and miss nested structures
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        try:
            value = json.loads(m.group(1))
            if valid_review_output(value):
                return value, True
        except json.JSONDecodeError:
            pass
    print(f"⚠️  LLM response parse failed. Storing raw output for debugging.", file=sys.stderr)
    print(f"   Raw (first 300 chars): {text[:300]}", file=sys.stderr)
    return empty_reviewer_output(), False


