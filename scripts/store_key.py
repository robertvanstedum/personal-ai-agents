#!/usr/bin/env python3
"""Store an API key in the macOS Keychain.

Usage:
  python3 scripts/store_key.py <service> <account>

Examples:
  python3 scripts/store_key.py openai api_key
  python3 scripts/store_key.py anthropic api_key
  python3 scripts/store_key.py xai api_key
  python3 scripts/store_key.py telegram bot_token
  python3 scripts/store_key.py telegram polling_bot_token
"""
import getpass
import subprocess
import sys

if len(sys.argv) != 3:
    print(__doc__)
    sys.exit(1)

service, account = sys.argv[1], sys.argv[2]
key = getpass.getpass(f"Paste value for {service}/{account} (input hidden): ")

result = subprocess.run(
    ["security", "add-generic-password", "-s", service, "-a", account, "-w", key, "-U"],
    capture_output=True, text=True,
)

if result.returncode == 0:
    print(f"✅ Stored in Keychain: service='{service}', account='{account}'")
else:
    print(f"❌ Error: {result.stderr.strip()}")
    sys.exit(1)
