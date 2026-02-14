#!/usr/bin/env python3
"""
Test error handling without breaking the real setup

Shows what happens when:
1. API key is missing
2. API returns authentication error
3. API returns insufficient credits error
4. With and without --fallback flag
"""

import sys

def simulate_no_api_key():
    """Simulate missing API key"""
    print("\n" + "="*70)
    print("SCENARIO 1: No API key found")
    print("="*70)
    
    error_msg = """
❌ Anthropic API key not found!

Checked:
  1. macOS Keychain (service: anthropic, account: api_key)
  2. Environment variable: ANTHROPIC_API_KEY
  3. .env file

To fix:
  1. Store key in Keychain:
     python store_api_key.py
  
  2. OR set environment variable:
     export ANTHROPIC_API_KEY='your-key-here'
  
  3. OR add to .env file:
     echo "ANTHROPIC_API_KEY=your-key-here" > .env

To test with mechanical mode instead:
  python curator_rss_v2.py --mode=mechanical
"""
    print(error_msg)
    print("\n💥 Curation failed: Anthropic API key not found")
    print("\nTip: Run with --mode=mechanical to test everything except API")
    print("\nExit code: 1 (error)")

def simulate_auth_error():
    """Simulate authentication error"""
    print("\n" + "="*70)
    print("SCENARIO 2: Invalid/expired API key")
    print("="*70)
    
    error_msg = """
❌ Haiku API Error: AuthenticationError

Details: Invalid API key provided

Common causes:

  • Invalid or expired API key
  
  Fix: Update your API key
    python store_api_key.py

To test with mechanical mode instead:
  python curator_rss_v2.py --mode=mechanical

To enable automatic fallback (for cron jobs):
  python curator_rss_v2.py --mode=ai --fallback
"""
    print(error_msg)
    print("\n💥 Curation failed: Haiku API failed: AuthenticationError: Invalid API key provided")
    print("\nTip: Run with --mode=mechanical to test everything except API")
    print("\nExit code: 1 (error)")

def simulate_insufficient_credits():
    """Simulate out of credits"""
    print("\n" + "="*70)
    print("SCENARIO 3: Insufficient credits")
    print("="*70)
    
    error_msg = """
❌ Haiku API Error: InsufficientCreditsError

Details: Insufficient credits in your account

Common causes:

  • Insufficient credits / out of funds
  
  Fix: Add credits at https://console.anthropic.com/settings/billing
  Check balance: https://console.anthropic.com/settings/usage

To test with mechanical mode instead:
  python curator_rss_v2.py --mode=mechanical

To enable automatic fallback (for cron jobs):
  python curator_rss_v2.py --mode=ai --fallback
"""
    print(error_msg)
    print("\n💥 Curation failed: Haiku API failed: InsufficientCreditsError: Insufficient credits in your account")
    print("\nTip: Run with --mode=mechanical to test everything except API")
    print("\nExit code: 1 (error)")

def simulate_with_fallback():
    """Simulate with --fallback flag"""
    print("\n" + "="*70)
    print("SCENARIO 4: API error WITH --fallback flag (cron mode)")
    print("="*70)
    
    print("\n📡 Calling Haiku to score 140 articles...")
    print("\n❌ Haiku API Error: InsufficientCreditsError")
    print("Details: Insufficient credits in your account")
    print("\n⚠️  Falling back to mechanical scoring...")
    print("\n🧠 Using mechanical scoring for all 140 articles...")
    print("   ✅ Mechanical scoring complete")
    print("\n📊 Source distribution in top 20:")
    print("   ZeroHedge: 4 articles")
    print("   ...")
    print("\nExit code: 0 (success with fallback)")

if __name__ == "__main__":
    print("\n" + "="*70)
    print("ERROR HANDLING TEST - curator_rss_v2.py")
    print("="*70)
    print("\nThis shows how the curator handles different error scenarios.")
    print("Your actual setup is NOT affected by this test!")
    
    simulate_no_api_key()
    simulate_auth_error()
    simulate_insufficient_credits()
    simulate_with_fallback()
    
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print("""
Without --fallback (default, recommended for manual runs):
  • Stops immediately on API error
  • Shows detailed error message with fix instructions
  • Exit code 1 (error)
  • YOU decide whether to run mechanical mode

With --fallback (for cron jobs):
  • Continues with mechanical scoring on API error
  • Still shows error message but doesn't stop
  • Exit code 0 (success)
  • Best-effort delivery even if API is down

Manual testing workflow:
  1. Run: python curator_rss_v2.py --mode=ai
  2. If it fails: diagnose error (key? credits? rate limit?)
  3. Test: python curator_rss_v2.py --mode=mechanical
  4. Fix API issue, try again

Cron job workflow:
  1. Use: python curator_rss_v2.py --mode=ai --fallback --telegram
  2. If API fails: falls back to mechanical, still delivers
  3. Check logs to see what went wrong
  4. Fix API issue for next run
""")
