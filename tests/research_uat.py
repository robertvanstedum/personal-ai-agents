"""
research_uat.py — Research Intelligence UAT script

Runs two back-to-back sessions for a topic and validates:
  1. System prompt injection fires
  2. Novelty scoring discounts seen URLs on session 2
  3. Sources are Taiwan-specific (≤20% drift)
  4. Backlog entries exist (B-018, B-019 logged)
  5. Missing motivation field degrades gracefully

Usage:
    cd ~/Projects/personal-ai-agents
    source venv/bin/activate
    python tests/research_uat.py

Corrections from original spec:
  - Paths updated to match actual repo layout under _NewDomains/research-intelligence/
  - research.py invoked with --session-name (not --session)
  - Log messages updated to match actual output strings in research.py
  - Drift check parenthesis fixed (syntax error in original)
  - Backlog check reads BACKLOG.md at repo root (not memory/bugs/ which doesn't exist)
"""

import subprocess
import json
import os
import re

# ── Config ─────────────────────────────────────────────────────────────────────

REPO_DIR     = os.path.expanduser('~/Projects/personal-ai-agents')
RI_DIR       = os.path.join(REPO_DIR, '_NewDomains', 'research-intelligence')
AGENT_DIR    = os.path.join(RI_DIR, 'agent')
VENV_PYTHON  = os.path.join(REPO_DIR, 'venv', 'bin', 'python3')

TOPIC        = 'hellscape-taiwan-porcupine'   # change for other topics
SESSION_BASE = 'uat-100'                      # high name to avoid collisions

THREAD_JSON  = os.path.join(RI_DIR, 'data', 'threads', TOPIC, 'thread.json')
SEEN_URLS    = os.path.join(RI_DIR, 'data', 'seen_urls', f'{TOPIC}.json')
SESSION_LOG  = os.path.join(RI_DIR, 'library', 'session-log.md')
BACKLOG_MD   = os.path.join(REPO_DIR, 'BACKLOG.md')

# ── Helpers ────────────────────────────────────────────────────────────────────

def run_session(session_name):
    """Run one research session. Returns (stdout, stderr, returncode)."""
    cmd = [
        VENV_PYTHON, os.path.join(AGENT_DIR, 'research.py'),
        '--topic', TOPIC,
        '--session-name', session_name,
    ]
    result = subprocess.run(cmd, cwd=RI_DIR, capture_output=True, text=True)
    return result.stdout, result.stderr, result.returncode


def check_output(output, pattern):
    """Return True if pattern found in combined stdout+stderr."""
    return re.search(pattern, output) is not None


def check_drift(output):
    """Return True if no invalid-topic sources appear in session output."""
    invalid_keywords = ['Iraq', 'Mackinder', 'U.S. military culture',
                        'dependency theory', 'Quadrennial Defense Review']
    for kw in invalid_keywords:
        if kw in output:
            return False
    return True


def check_taiwan_relevance(output):
    """Return True if ≥80% of scored sources are Taiwan-relevant."""
    taiwan_keywords = ['Taiwan', 'ODC', 'TSMC', 'cross-strait', 'porcupine',
                       'PRC', 'Taipei', 'NCCU']
    scored_lines = re.findall(r'Score: \d/5.*', output)
    if not scored_lines:
        return False  # No scored sources found — inconclusive
    relevant = sum(
        any(kw in line for kw in taiwan_keywords)
        for line in scored_lines
    )
    ratio = relevant / len(scored_lines)
    return ratio >= 0.80


def check_backlog():
    """Return True if B-018 and B-019 both appear in BACKLOG.md."""
    if not os.path.exists(BACKLOG_MD):
        return False
    content = open(BACKLOG_MD).read()
    return 'B-018' in content and 'B-019' in content


# ── UAT ────────────────────────────────────────────────────────────────────────

def uat():
    results = {}

    # Reset novelty cache so session 1 is a clean baseline
    if os.path.exists(SEEN_URLS):
        os.remove(SEEN_URLS)
        print(f'  Removed seen_urls cache: {SEEN_URLS}')

    # ── Test 1: Session 1 — system prompt injection + cache creation ───────────
    print('\n[1] Running session 1 (baseline)...')
    out1, err1, rc1 = run_session(f'{SESSION_BASE}-a')
    combined1 = out1 + err1
    if rc1 != 0:
        print(f'  Session 1 failed (rc={rc1}). Aborting.')
        print(err1[-2000:])
        return

    prompt_injected = check_output(combined1, r'System prompt: loaded and populated')
    cache_created   = os.path.exists(SEEN_URLS)

    results['1_system_prompt_injected'] = prompt_injected
    results['1_seen_urls_cache_created'] = cache_created
    print(f'  System prompt injected: {prompt_injected}')
    print(f'  seen_urls cache created: {cache_created}')

    # ── Test 2: Session 2 — novelty discounting fires ─────────────────────────
    print('\n[2] Running session 2 (novelty check)...')
    out2, err2, rc2 = run_session(f'{SESSION_BASE}-b')
    combined2 = out2 + err2
    if rc2 != 0:
        print(f'  Session 2 failed (rc={rc2}). Aborting.')
        print(err2[-2000:])
        return

    novelty_fired = check_output(combined2, r'Novelty: \d+ seen URL\(s\) discounted')

    results['2_novelty_discount_fired'] = novelty_fired
    print(f'  Novelty discount fired: {novelty_fired}')

    # ── Test 3: Drift check across both sessions ───────────────────────────────
    no_drift    = check_drift(combined1 + combined2)
    tw_relevant = check_taiwan_relevance(combined1 + combined2)

    results['3_no_topic_drift']          = no_drift
    results['3_taiwan_relevance_gte80pct'] = tw_relevant
    print(f'\n[3] No topic drift: {no_drift}')
    print(f'    Taiwan relevance ≥80%%: {tw_relevant}')

    # ── Test 4: Backlog entries logged ─────────────────────────────────────────
    backlog_ok = check_backlog()
    results['4_backlog_b018_b019_present'] = backlog_ok
    print(f'\n[4] B-018 + B-019 in BACKLOG.md: {backlog_ok}')

    # ── Test 5: Missing motivation degrades gracefully ─────────────────────────
    print('\n[5] Testing missing motivation field...')
    with open(THREAD_JSON, 'r') as f:
        thread_data = json.load(f)
    orig_motivation = thread_data.pop('motivation', None)
    with open(THREAD_JSON, 'w') as f:
        json.dump(thread_data, f, indent=2)

    try:
        out5, err5, rc5 = run_session(f'{SESSION_BASE}-c')
        combined5 = out5 + err5
        # Actual warning string from research.py load_session_system_prompt()
        graceful = check_output(combined5, r"WARNING: thread\.json missing 'motivation'")
        results['5_missing_motivation_graceful'] = graceful
        print(f'  Graceful degradation on missing motivation: {graceful}')
    finally:
        thread_data['motivation'] = orig_motivation
        with open(THREAD_JSON, 'w') as f:
            json.dump(thread_data, f, indent=2)

    # ── Report ─────────────────────────────────────────────────────────────────
    print('\n' + '=' * 60)
    passed = all(results.values())
    for key, val in results.items():
        status = '✓' if val else '✗'
        print(f'  {status}  {key}')
    print('=' * 60)
    print(f'\n  {"UAT PASSED" if passed else "UAT FAILED"}\n')
    return passed


if __name__ == '__main__':
    uat()
