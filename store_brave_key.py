#!/usr/bin/env python3
"""
Store Brave Search API key in macOS Keychain (secure)

Usage:
    python store_brave_key.py

You'll be prompted to enter your API key securely.
"""

import keyring
import getpass

def store_api_key():
    print("🔐 Store Brave Search API Key in macOS Keychain")
    print("=" * 50)
    print()
    print("Your API key will be stored securely in macOS Keychain.")
    print("Service: brave_search")
    print("Account: api_key")
    print()
    
    # Get API key securely (won't echo to screen)
    api_key = getpass.getpass("Enter your Brave Search API key: ")
    
    if not api_key or len(api_key) < 20:
        print("❌ Invalid API key (too short or empty)")
        return
    
    # Store in keychain
    try:
        keyring.set_password("brave_search", "api_key", api_key)
        print()
        print("✅ API key stored successfully in macOS Keychain!")
        print()
        print("To verify, run:")
        print("  python -c 'import keyring; print(keyring.get_password(\"brave_search\", \"api_key\")[:20] + \"...\")'")
        print()
        print("To delete:")
        print("  python -c 'import keyring; keyring.delete_password(\"brave_search\", \"api_key\")'")
    except Exception as e:
        print(f"❌ Error storing key: {e}")

if __name__ == "__main__":
    store_api_key()
