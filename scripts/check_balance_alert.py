#!/usr/bin/env python3
"""
Check balance and only output if low.
Exit codes: 0=OK, 1=WARNING, 2=CRITICAL, 3=SEVERE
"""

import sys
import os

# Add project to path
sys.path.insert(0, os.path.expanduser("~/Projects/personal-ai-agents"))

try:
    from core.track_usage import check_balance, BALANCE_THRESHOLDS
    
    balance, warning_level = check_balance()
    
    if balance is None:
        sys.exit(0)  # Could not fetch, skip silently
    
    if warning_level:
        warning_msgs = {
            'SEVERE': f"🚨🚨🚨 SEVERELY LOW BALANCE: ${balance:.2f} (threshold: ${BALANCE_THRESHOLDS['severely_low']})\n🔗 Top up: https://console.anthropic.com/settings/billing",
            'CRITICAL': f"⚠️⚠️ CRITICALLY LOW BALANCE: ${balance:.2f} (threshold: ${BALANCE_THRESHOLDS['critically_low']})\n🔗 Top up: https://console.anthropic.com/settings/billing",
            'WARNING': f"⚡ Getting Low Balance: ${balance:.2f} (threshold: ${BALANCE_THRESHOLDS['getting_low']})\n💡 Consider topping up: https://console.anthropic.com/settings/billing"
        }
        print(warning_msgs.get(warning_level, f"Low balance: ${balance:.2f}"))
        sys.exit({'WARNING': 1, 'CRITICAL': 2, 'SEVERE': 3}.get(warning_level, 1))
    else:
        # Balance OK, no output
        sys.exit(0)
        
except Exception as e:
    # Fail silently on errors
    sys.exit(0)
