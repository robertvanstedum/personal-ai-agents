#!/usr/bin/env python3
"""
generate_deeper_dive.py — Deeper Dive POC

Two-agent analysis for a research thread:
  Agent 1 (Synthesizer): honest synthesis of what the research found
  Agent 2 (Challenger):  structured counter-analysis (Devil's Advocacy / Red Team)

Usage:
  cd _NewDomains/research-intelligence
  python scripts/generate_deeper_dive.py --topic strait-of-hormuz
  python scripts/generate_deeper_dive.py --topic strait-of-hormuz --dry-run

Output: data/deeper_dives/{topic}-deeper-dive-NNN.md
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path


# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT         = Path(__file__).resolve().parent.parent   # _NewDomains/research-intelligence
TOPICS_DIR   = ROOT / 'topics'
THREADS_DIR  = ROOT / 'data' / 'threads'
ANNOTATIONS_DIR = ROOT / 'data' / 'annotations' / 'research'
OUTPUT_DIR   = ROOT / 'data' / 'deeper_dives'
READING_ROOM = ROOT / 'data' / 'reading_room'
PROMPTS_DIR  = ROOT / 'prompts' / 'v1'

SYNTHESIZER_PROMPT_FILE = PROMPTS_DIR / 'synthesizer_geopolitics_finance.md'
CHALLENGER_PROMPT_FILE  = PROMPTS_DIR / 'challenger_geopolitics_finance.md'

DOMAIN = 'Research'


# ── Session parsing (inlined from research_routes.py) ─────────────────────────

def _parse_session_header(text: str) -> dict:
    """Extract the machine-readable header block from a session .md file."""
    header = {}
    m = re.search(
        r'<!-- MACHINE-READABLE HEADER.*?-->(.*?)<!-- END HEADER -->',
        text, re.DOTALL
    )
    if m:
        for line in m.group(1).strip().splitlines():
            if ':' in line:
                k, _, v = line.partition(':')
                header[k.strip()] = v.strip()
    return header


def _parse_session_sections(text: str) -> dict:
    """Parse a session .md file into structured sections."""
    findings, sources, threads, agent_notes, cost_table = [], [], [], '', ''

    # Split on ## headings
    parts = re.split(r'\n##\s+', '\n' + text)
    for part in parts[1:]:
        lines = part.split('\n')
        heading = lines[0].strip().lower()
        body = '\n'.join(lines[1:]).strip()

        if 'key finding' in heading:
            for line in body.splitlines():
                line = line.strip().lstrip('- •').strip()
                if line and not line.startswith('#'):
                    findings.append(line)

        elif 'source' in heading and 'further' not in heading:
            # Join continuation lines (URL lives on indented line after the [N] line)
            raw_lines = body.splitlines()
            joined: list[str] = []
            for raw in raw_lines:
                if raw and raw[0] in ' \t' and joined and re.match(r'\[\d+\]', joined[-1]):
                    joined[-1] = joined[-1].rstrip() + ' ' + raw.strip()
                else:
                    joined.append(raw)
            for line in joined:
                line = line.strip()
                # Domain may contain dots (e.g. www.eia.gov) — use lazy (.+?) match
                m = re.match(r'\[(\d+)\]\s+(.+?)\.\s+"([^"]+)"\.?\s*(https?://\S*)?', line)
                if m:
                    sources.append({
                        'num':    int(m.group(1)),
                        'domain': m.group(2).strip(),
                        'title':  m.group(3).strip(),
                        'url':    (m.group(4) or '').strip(),
                    })

        elif 'thread' in heading or 'continue' in heading:
            threads = [l.strip().lstrip('- ').strip() for l in body.splitlines()
                       if l.strip() and not l.strip().startswith('#')]

        elif 'agent note' in heading or 'note' in heading:
            agent_notes = body

        elif 'cost' in heading:
            cost_table = body

    return {
        'findings':    findings,
        'sources':     sources,
        'threads':     threads,
        'agent_notes': agent_notes,
        'cost_table':  cost_table,
    }


# ── Data loading ──────────────────────────────────────────────────────────────

def load_thread_data(topic: str) -> dict:
    """Load all data for a topic thread."""
    # Motivation from thread.json
    thread_json = THREADS_DIR / topic / 'thread.json'
    motivation = ''
    if thread_json.exists():
        td = json.loads(thread_json.read_text())
        motivation = td.get('motivation', '').strip()

    # Sessions
    topic_dir = TOPICS_DIR / topic
    if not topic_dir.exists():
        print(f"Error: topics/{topic}/ directory not found. Has a session been run for this topic?")
        sys.exit(1)

    EXCLUDED_STEMS = {'CONTEXT', 'ORIGIN', 'STORY_FOR_CLAUDE_AI'}
    session_files = sorted([
        f for f in topic_dir.glob('*.md')
        if not f.name.startswith('sources-')
        and f.stem not in EXCLUDED_STEMS
        and not f.stem.isupper()
    ])

    sessions = []
    for f in session_files:
        text = f.read_text()
        hdr  = _parse_session_header(text)
        secs = _parse_session_sections(text)
        sessions.append({
            'file':     f.name,
            'header':   hdr,
            'findings': secs['findings'],
            'sources':  secs['sources'],
            'threads':  secs['threads'],
        })

    # Annotations
    annotations = []
    ann_dir = ANNOTATIONS_DIR / topic
    if ann_dir.exists():
        for jf in sorted(ann_dir.glob('*.json')):
            try:
                entries = json.loads(jf.read_text())
                if isinstance(entries, list):
                    annotations.extend(entries)
            except (json.JSONDecodeError, OSError):
                pass

    return {
        'topic':       topic,
        'domain':      DOMAIN,
        'motivation':  motivation,
        'sessions':    sessions,
        'annotations': annotations,
    }


# ── Empty session guard ───────────────────────────────────────────────────────

def guard_has_findings(data: dict) -> None:
    sessions_with_findings = [s for s in data['sessions'] if s['findings']]
    if not sessions_with_findings:
        print(f"Error: topic '{data['topic']}' has no sessions with findings.")
        print("Run a research session first:")
        print(f"  python agent/research.py --topic {data['topic']}")
        sys.exit(1)


# ── Prompt assembly ───────────────────────────────────────────────────────────

def _format_session_findings(sessions: list) -> str:
    """All findings across sessions, labeled by session ID."""
    parts = []
    for s in sessions:
        if not s['findings']:
            continue
        session_id = s['header'].get('session', s['file'])
        parts.append(f"[{session_id}]")
        for f in s['findings']:
            # Strip triage artefacts like "(Target 1, Target 2, ...)"
            clean = re.sub(r'\s*\(Target\s+\d+(?:,\s*Target\s+\d+)*\)\s*', ' ', f).strip()
            parts.append(f"  - {clean}")
    return '\n'.join(parts) if parts else '(no findings)'


def _format_annotations(annotations: list) -> str:
    if not annotations:
        return '(none)'
    lines = []
    for a in annotations:
        note = a.get('note', '').strip()
        if not note:
            continue
        ts   = a.get('timestamp', '')[:10]
        atype = a.get('type', 'reaction')
        ref  = a.get('ref_text', '') or ''
        if ref:
            lines.append(f"[{ts}] ({atype}) re: \"{ref[:80]}\" — {note}")
        else:
            lines.append(f"[{ts}] ({atype}) {note}")
    return '\n'.join(lines) if lines else '(none)'


def build_synthesizer_user_prompt(data: dict) -> str:
    findings_text  = _format_session_findings(data['sessions'])
    annotations_text = _format_annotations(data['annotations'])
    session_count  = len([s for s in data['sessions'] if s['findings']])

    return f"""Research topic: {data['topic']}
Domain: {data['domain']}
Original motivation: {data['motivation'] or '(not recorded)'}
Sessions with findings: {session_count}

Session findings:
{findings_text}

User annotations (if any):
{annotations_text}

Produce:
1. WHAT THE RESEARCH FOUND — honest synthesis (3-5 paragraphs)
2. WHERE THE HYPOTHESIS HOLDS — strongest confirming evidence
3. KEY SOURCES — 3-5 most significant sources and what they establish
4. WHAT REMAINS UNCERTAIN — gaps, unresolved questions, absence of evidence

Be specific. Cite sources by name. Do not generalize."""


def build_challenger_user_prompt(data: dict, synthesizer_output: str) -> str:
    return f"""Research topic: {data['topic']}
Domain: {data['domain']}
Original motivation: {data['motivation'] or '(not recorded)'}

The Synthesizer produced this analysis:
{synthesizer_output}

Your job:
1. WHERE IT DOESN'T HOLD — explicit challenge to the original motivation using disconfirming evidence
2. ALTERNATIVE INTERPRETATIONS — how else could the same evidence be read?
3. WHAT YOU DIDN'T EXPECT — lateral findings the research surfaced outside the original motivation
4. REVISED FRAMING — context-sensitive closing:
   - If motivation was a hypothesis: propose a revised framing
   - If motivation was curiosity: surface the most interesting questions the research opened
   - If mixed: do both briefly
   Frame this as a prompt to the researcher, not a conclusion. The researcher owns the conclusion."""


# ── API calls ─────────────────────────────────────────────────────────────────

def get_anthropic_client():
    """Get Anthropic client using keyring then env fallback (mirrors curator_feedback.py)."""
    api_key = None
    try:
        import keyring as kr
        api_key = kr.get_password('anthropic', 'api_key')
    except Exception:
        pass
    if not api_key:
        import os
        api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("Error: Anthropic API key not found. Set via keyring or ANTHROPIC_API_KEY env var.")
        sys.exit(1)
    from anthropic import Anthropic
    return Anthropic(api_key=api_key)


def run_agent(client, system_prompt: str, user_prompt: str,
              model: str, temperature: float, label: str) -> tuple[str, float]:
    """Run one agent call. Returns (output_text, cost_usd)."""
    print(f"  → Running {label} ({model}, temp={temperature})…", flush=True)
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        temperature=temperature,
        system=system_prompt,
        messages=[{'role': 'user', 'content': user_prompt}],
    )
    text = response.content[0].text
    # Opus pricing: $15/M input, $75/M output
    cost = (response.usage.input_tokens * 0.000015) + (response.usage.output_tokens * 0.000075)
    print(f"     {label}: {response.usage.input_tokens} in + {response.usage.output_tokens} out tokens — ${cost:.4f}")
    return text, cost


# ── Essay assembly ────────────────────────────────────────────────────────────

def assemble_essay(data: dict, s_out: str, c_out: str, total_cost: float) -> str:
    topic      = data['topic']
    motivation = data['motivation'] or '(not recorded)'
    today      = date.today().isoformat()
    n_sessions = len([s for s in data['sessions'] if s['findings']])

    # Deduplicated source list across all sessions
    seen_urls, bib_lines = set(), []
    for s in data['sessions']:
        for src in s['sources']:
            key = src['url'] or src['title']
            if key and key not in seen_urls:
                seen_urls.add(key)
                url_part = f" {src['url']}" if src['url'] else ''
                bib_lines.append(f"[{len(bib_lines)+1}] {src['domain']}. \"{src['title']}\".{url_part}")
    bibliography = '\n'.join(bib_lines) if bib_lines else '(no sources recorded)'

    return f"""# DEEPER DIVE: {topic.replace('-', ' ').title()}
*Generated: {today} · {n_sessions} session{'s' if n_sessions != 1 else ''} · {len(seen_urls)} sources · Est. cost: ${total_cost:.2f}*
*Original motivation: "{motivation}"*
*Domain: {data['domain']}*

---

{s_out.strip()}

---

{c_out.strip()}

---

## Bibliography
{bibliography}

---
*Synthesizer: claude-opus-4-5 (temp 0.3) · Challenger: claude-opus-4-5 (temp 0.7)*
*Deeper Dive v1.0 — POC*
"""


# ── Output / stubs ────────────────────────────────────────────────────────────

def next_output_path(topic: str) -> Path:
    """Return next sequential output path: {topic}-deeper-dive-NNN.md"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    existing = list(OUTPUT_DIR.glob(f'{topic}-deeper-dive-*.md'))
    nums = []
    for f in existing:
        m = re.search(r'-deeper-dive-(\d+)\.md$', f.name)
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return OUTPUT_DIR / f'{topic}-deeper-dive-{n:03d}.md'


def create_reading_room_stub(topic: str) -> None:
    READING_ROOM.mkdir(parents=True, exist_ok=True)
    stub_path = READING_ROOM / f'{topic}.json'
    if stub_path.exists():
        return  # Don't overwrite existing
    stub = {
        'topic':   topic,
        'created': date.today().isoformat(),
        'status':  'placeholder',
        'sources': [],
        'note':    'Reading Room deferred — see VISION_DEEPER_DIVE_READING_ROOM',
    }
    stub_path.write_text(json.dumps(stub, indent=2))
    print(f"  Reading room stub: {stub_path.relative_to(ROOT)}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='Generate a Deeper Dive essay for a research thread')
    parser.add_argument('--topic',   required=True, help='Topic slug (e.g. strait-of-hormuz)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print prompts and data summary without calling the API')
    args = parser.parse_args()

    topic = args.topic

    # Load prompts
    if not SYNTHESIZER_PROMPT_FILE.exists():
        print(f"Error: prompt file not found: {SYNTHESIZER_PROMPT_FILE}")
        sys.exit(1)
    if not CHALLENGER_PROMPT_FILE.exists():
        print(f"Error: prompt file not found: {CHALLENGER_PROMPT_FILE}")
        sys.exit(1)
    synthesizer_system = SYNTHESIZER_PROMPT_FILE.read_text().strip()
    challenger_system  = CHALLENGER_PROMPT_FILE.read_text().strip()

    # Load data
    print(f"Loading thread data for: {topic}")
    data = load_thread_data(topic)
    guard_has_findings(data)

    n_sessions   = len([s for s in data['sessions'] if s['findings']])
    n_annotations = len(data['annotations'])
    print(f"  {n_sessions} sessions with findings, {n_annotations} annotation(s)")
    print(f"  Motivation: {data['motivation'][:80]}…" if len(data['motivation']) > 80 else f"  Motivation: {data['motivation']}")

    # Build prompts
    synthesizer_user = build_synthesizer_user_prompt(data)
    # challenger_user built after synthesizer runs

    if args.dry_run:
        print('\n' + '='*60)
        print('SYNTHESIZER SYSTEM PROMPT:')
        print(synthesizer_system)
        print('\nSYNTHESIZER USER PROMPT:')
        print(synthesizer_user)
        print('='*60)
        print('Dry run complete — no API calls made.')
        return

    # Run agents
    client = get_anthropic_client()
    print(f"\nRunning two-agent analysis…")

    s_out, s_cost = run_agent(
        client,
        system_prompt=synthesizer_system,
        user_prompt=synthesizer_user,
        model='claude-opus-4-5',
        temperature=0.3,
        label='Synthesizer',
    )

    challenger_user = build_challenger_user_prompt(data, s_out)
    c_out, c_cost = run_agent(
        client,
        system_prompt=challenger_system,
        user_prompt=challenger_user,
        model='claude-opus-4-5',
        temperature=0.7,
        label='Challenger',
    )

    total_cost = s_cost + c_cost

    # Assemble and write
    essay = assemble_essay(data, s_out, c_out, total_cost)
    out_path = next_output_path(topic)
    out_path.write_text(essay)

    create_reading_room_stub(topic)

    print(f"\n✓ Deeper Dive written: {out_path.relative_to(ROOT)}")
    print(f"  Total cost: ${total_cost:.4f} (synthesizer ${s_cost:.4f} + challenger ${c_cost:.4f})")


if __name__ == '__main__':
    main()
