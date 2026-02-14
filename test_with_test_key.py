#!/usr/bin/env python3
"""
Negative test using the test API key (secure)

Workflow:
1. Switch to test key (stored in keychain)
2. Run curator (should work)
3. YOU revoke test key in console
4. Run curator again (should fail with auth error)
5. Switch back to production key
"""

import keyring
import subprocess
import sys

def switch_to_test_key():
    """Switch curator to use test key"""
    test_key = keyring.get_password("anthropic", "test_key")
    if not test_key:
        print("❌ Test key not found in Keychain")
        print("   Run: python setup_keys.py")
        sys.exit(1)
    
    keyring.set_password("anthropic", "api_key", test_key)
    print(f"✅ Switched to TEST key (starts with: {test_key[:20]}...)")

def switch_to_production_key():
    """Switch curator back to production key"""
    prod_key = keyring.get_password("anthropic", "production_key")
    if not prod_key:
        print("❌ Production key backup not found in Keychain")
        print("   Run: python setup_keys.py")
        return False
    
    keyring.set_password("anthropic", "api_key", prod_key)
    print(f"✅ Restored PRODUCTION key (starts with: {prod_key[:20]}...)")
    return True

def main():
    print("=" * 70)
    print("NEGATIVE TEST: Test API Key Workflow")
    print("=" * 70)
    print()
    
    # Step 1: Switch to test key
    print("STEP 1: Switch to test key")
    print("-" * 70)
    switch_to_test_key()
    print()
    
    # Step 2: Test that it works
    print("STEP 2: Test with valid test key (should work)")
    print("-" * 70)
    print("Running: python curator_rss_v2.py --mode=ai")
    print()
    
    result = subprocess.run(
        ["python", "curator_rss_v2.py", "--mode=ai"],
        capture_output=False
    )
    
    if result.returncode == 0:
        print("\n✅ Test key works!")
    else:
        print("\n⚠️  Test key failed (might already be revoked)")
    
    print()
    
    # Step 3: User revokes key
    print("STEP 3: Revoke the test key")
    print("-" * 70)
    print("Go to: https://console.anthropic.com/settings/keys")
    print("Find: The key starting with", keyring.get_password("anthropic", "test_key")[:20] + "...")
    print("Click: [Revoke]")
    print()
    input("Press Enter when you've revoked the test key...")
    print()
    
    # Step 4: Test with revoked key
    print("STEP 4: Test with revoked test key (should fail)")
    print("-" * 70)
    print("Running: python curator_rss_v2.py --mode=ai")
    print("(This should show authentication error)")
    print()
    
    result = subprocess.run(
        ["python", "curator_rss_v2.py", "--mode=ai"],
        capture_output=False
    )
    
    if result.returncode != 0:
        print("\n✅ Failed as expected (revoked key rejected)")
    else:
        print("\n⚠️  Unexpected: Test key still works (was it revoked?)")
    
    print()
    
    # Step 5: Restore production
    print("STEP 5: Restore production key")
    print("-" * 70)
    if switch_to_production_key():
        print()
        print("=" * 70)
        print("✅ TEST COMPLETE")
        print("=" * 70)
        print()
        print("Your production key is now active.")
        print("Test it: python curator_rss_v2.py --mode=ai --open")
    else:
        print("\n❌ Failed to restore production key")
        print("   You'll need to run: python setup_keys.py")

if __name__ == "__main__":
    main()
