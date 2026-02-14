#!/usr/bin/env python3
"""
Store API keys/tokens in macOS Keychain

Stores:
- Anthropic API key (service: anthropic, account: api_key)
- Telegram bot token (service: telegram, account: bot_token)
"""

import keyring
import getpass
import sys

def store_anthropic():
    """Store Anthropic API key"""
    print("\n1️⃣  Anthropic API Key")
    print("=" * 50)
    key = getpass.getpass("Enter Anthropic API key (or press Enter to skip): ")
    
    if key:
        try:
            keyring.set_password("anthropic", "api_key", key)
            print("✅ Anthropic API key stored in Keychain")
        except Exception as e:
            print(f"❌ Failed: {e}")
    else:
        print("⏭️  Skipped")

def store_telegram():
    """Store Telegram bot token"""
    print("\n2️⃣  Telegram Bot Token")
    print("=" * 50)
    token = getpass.getpass("Enter Telegram bot token (or press Enter to skip): ")
    
    if token:
        try:
            keyring.set_password("telegram", "bot_token", token)
            print("✅ Telegram bot token stored in Keychain")
        except Exception as e:
            print(f"❌ Failed: {e}")
    else:
        print("⏭️  Skipped")

def verify_keys():
    """Verify stored keys"""
    print("\n🔍 Verification")
    print("=" * 50)
    
    try:
        anthropic_key = keyring.get_password("anthropic", "api_key")
        if anthropic_key:
            print(f"✅ Anthropic API key: sk-ant-...{anthropic_key[-6:]}")
        else:
            print("⚠️  Anthropic API key: Not found")
    except Exception as e:
        print(f"⚠️  Anthropic API key: Error reading ({e})")
    
    try:
        telegram_token = keyring.get_password("telegram", "bot_token")
        if telegram_token:
            # Bot tokens format: 123456:ABC-DEF...
            parts = telegram_token.split(':')
            if len(parts) == 2:
                print(f"✅ Telegram bot token: {parts[0]}:...{telegram_token[-6:]}")
            else:
                print(f"✅ Telegram bot token: Present")
        else:
            print("⚠️  Telegram bot token: Not found")
    except Exception as e:
        print(f"⚠️  Telegram bot token: Error reading ({e})")

def main():
    print("\n🔐 Setup API Keys in macOS Keychain")
    print("=" * 50)
    print("This will store your credentials securely.")
    print("You can skip any key by pressing Enter.")
    
    store_anthropic()
    store_telegram()
    verify_keys()
    
    print("\n✨ Done!")
    print("\nTo manually check keys:")
    print('  python3 -c "import keyring; print(keyring.get_password(\\"anthropic\\", \\"api_key\\"))"')
    print('  python3 -c "import keyring; print(keyring.get_password(\\"telegram\\", \\"bot_token\\"))"')
    print()

if __name__ == "__main__":
    main()
