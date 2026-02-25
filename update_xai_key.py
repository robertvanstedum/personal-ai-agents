#!/usr/bin/env python3
"""
Update xAI API key in OpenClaw auth-profiles.json
"""
import json
from pathlib import Path

def main():
    print("=== Update xAI API Key ===\n")
    
    # Prompt for key
    new_key = input("Paste your xAI key (starts with xai-, ends with jj): ").strip()
    
    if not new_key.startswith('xai-'):
        print("❌ Error: Key should start with 'xai-'")
        return
    
    # Update auth-profiles.json
    config_path = Path.home() / '.openclaw/agents/main/agent/auth-profiles.json'
    
    if not config_path.exists():
        print(f"❌ Error: Config file not found at {config_path}")
        return
    
    config = json.loads(config_path.read_text())
    old_key = config['profiles']['xai:default']['key']
    
    config['profiles']['xai:default']['key'] = new_key
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    
    print(f"\n✅ Updated xai:default key")
    print(f"   Old: ...{old_key[-4:]}")
    print(f"   New: ...{new_key[-4:]}")

if __name__ == '__main__':
    main()
