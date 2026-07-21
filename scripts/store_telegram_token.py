#!/usr/bin/env python3
"""
Store Telegram bot token in macOS Keychain
"""

import keyring
import getpass
import sys

def store_token():
    """Store Telegram bot token in keychain"""
    print("Store Telegram Bot Token in macOS Keychain")
    print("=" * 50)
    print()
    print("This will store your Telegram bot token securely in macOS Keychain.")
    print()
    
    token = getpass.getpass("Enter your Telegram bot token: ")
    
    if not token:
        print("❌ No token provided")
        sys.exit(1)
    
    # Store in keychain
    try:
        keyring.set_password("telegram", "bot_token", token)
        print()
        print("✅ Telegram bot token stored in Keychain!")
        print()
        print("Service: telegram")
        print("Account: bot_token")
        print()
        print("To verify:")
        print("  python3 -c 'import keyring; print(keyring.get_password(\"telegram\", \"bot_token\"))'")
        print()
    except Exception as e:
        print(f"❌ Failed to store token: {e}")
        sys.exit(1)

if __name__ == "__main__":
    store_token()
