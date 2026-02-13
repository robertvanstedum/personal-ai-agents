#!/usr/bin/env python3
"""
Quick Interest Capture Tool

Usage:
    # From command line
    python capture_interest.py "[THIS-WEEK] Iran sanctions endgame"
    python capture_interest.py "[DEEP-DIVE] China gold strategy - why now?"
    python capture_interest.py "[MUTE] Sports betting content"
    
    # From Python
    from capture_interest import capture
    capture("DEEP-DIVE", "China gold strategy", "Why now? Timeline?")

Appends to: interests/YYYY-MM-DD-thoughts.md
"""

import sys
from pathlib import Path
from datetime import datetime
import re

INTERESTS_DIR = Path(__file__).parent / "interests"
VALID_TAGS = ["DEEP-DIVE", "THIS-WEEK", "BACKLOG", "MUTE"]

def parse_input(text: str) -> tuple:
    """
    Parse input text for tag and content
    
    Examples:
        "[THIS-WEEK] Iran sanctions" → ("THIS-WEEK", "Iran sanctions")
        "China gold" → ("THIS-WEEK", "China gold")  # default tag
    """
    # Try to extract tag
    match = re.match(r'\[([A-Z-]+)\]\s*(.*)', text)
    
    if match:
        tag = match.group(1)
        content = match.group(2).strip()
        
        if tag in VALID_TAGS:
            return (tag, content)
    
    # Default to THIS-WEEK if no valid tag found
    return ("THIS-WEEK", text.strip())

def get_today_file() -> Path:
    """Get path to today's interest file"""
    today = datetime.now().strftime("%Y-%m-%d")
    filepath = INTERESTS_DIR / f"{today}-thoughts.md"
    
    # Create directory if needed
    INTERESTS_DIR.mkdir(exist_ok=True)
    
    # Initialize file if it doesn't exist
    if not filepath.exists():
        template = f"""# Daily Interest Capture - {today}

**Source:** Morning briefing

---

## 🔥 Deep Dives

---

## 📌 This Week's Focus

---

## 📚 Backlog

---

## 🔇 Mute

---

## 💭 General Thoughts

"""
        filepath.write_text(template)
    
    return filepath

def append_to_section(filepath: Path, tag: str, content: str):
    """Append content to appropriate section"""
    
    # Read file
    text = filepath.read_text()
    
    # Find section based on tag
    section_markers = {
        "DEEP-DIVE": "## 🔥 Deep Dives",
        "THIS-WEEK": "## 📌 This Week's Focus",
        "BACKLOG": "## 📚 Backlog",
        "MUTE": "## 🔇 Mute"
    }
    
    marker = section_markers[tag]
    
    # Find insertion point (after section header, before next section or end)
    lines = text.split('\n')
    section_idx = None
    
    for i, line in enumerate(lines):
        if marker in line:
            section_idx = i
            break
    
    if section_idx is None:
        print(f"Warning: Could not find section for {tag}, appending to end")
        lines.append(f"\n[{tag}] {content}")
    else:
        # Find next section or end
        insert_idx = section_idx + 1
        
        # Skip empty lines after header
        while insert_idx < len(lines) and lines[insert_idx].strip() == '':
            insert_idx += 1
        
        # Find next section (starts with ##)
        while insert_idx < len(lines):
            if lines[insert_idx].startswith('##'):
                break
            insert_idx += 1
        
        # Insert before next section
        timestamp = datetime.now().strftime("%H:%M")
        entry = f"- [{timestamp}] {content}"
        lines.insert(insert_idx, entry)
        lines.insert(insert_idx, '')  # blank line
    
    # Write back
    filepath.write_text('\n'.join(lines))

def capture(tag: str, content: str, note: str = None):
    """
    Capture an interest
    
    Args:
        tag: DEEP-DIVE, THIS-WEEK, BACKLOG, or MUTE
        content: The topic/interest
        note: Optional additional note
    """
    if tag not in VALID_TAGS:
        print(f"Invalid tag: {tag}. Must be one of: {', '.join(VALID_TAGS)}")
        return False
    
    filepath = get_today_file()
    
    full_content = content
    if note:
        full_content = f"{content} - {note}"
    
    append_to_section(filepath, tag, full_content)
    
    print(f"✅ Captured [{tag}]: {content}")
    print(f"   File: {filepath}")
    
    return True

def main():
    """CLI interface"""
    if len(sys.argv) < 2:
        print("Usage: python capture_interest.py \"[TAG] Content\"")
        print()
        print("Examples:")
        print('  python capture_interest.py "[THIS-WEEK] Iran sanctions"')
        print('  python capture_interest.py "[DEEP-DIVE] China gold - why now?"')
        print('  python capture_interest.py "[MUTE] Sports content"')
        print()
        print(f"Valid tags: {', '.join(VALID_TAGS)}")
        print("Default tag if omitted: THIS-WEEK")
        sys.exit(1)
    
    # Join all arguments (in case user didn't quote)
    text = ' '.join(sys.argv[1:])
    
    tag, content = parse_input(text)
    capture(tag, content)

if __name__ == "__main__":
    main()
