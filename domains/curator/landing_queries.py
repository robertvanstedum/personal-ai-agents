"""Read-only homepage counts with explicit paths and legacy failure semantics."""
import json

def topics_summary(threads_dir) -> dict:
    """Count topics by status from threads directory. Falls back to all zeros."""
    result = {'total': 0, 'active': 0, 'dormant': 0}
    try:
        for f in threads_dir.glob('*/thread.json'):
            t = json.loads(f.read_text())
            status = t.get('status', '')
            result['total'] += 1
            if status in ('active', 'active-pull'):
                result['active'] += 1
            elif status in ('dormant', 'paused'):
                result['dormant'] += 1
    except Exception:
        pass
    return result


def leanings_summary(leanings_path) -> dict:
    """Count leanings by state from leanings.json. Falls back to all zeros."""
    result = {'total': 0, 'question': 0, 'leaning': 0, 'hold': 0}
    try:
        raw = json.loads(leanings_path.read_text())
        leanings = raw.get('leanings', raw) if isinstance(raw, dict) else raw
        for lean in leanings:
            result['total'] += 1
            state = lean.get('state', 'question')
            if state in result:
                result[state] += 1
    except Exception:
        pass
    return result
