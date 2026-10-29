#!/usr/bin/env python3
"""Check balance and only output if low.

Exit codes: 0=OK, 1=WARNING, 2=CRITICAL, 3=SEVERE.
Importing this command does not resolve credentials or query an account.
"""
import sys
from pathlib import Path


def main() -> int:
    # Use the reviewed checkout containing this command, regardless of cwd.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from core.track_usage import check_balance, BALANCE_THRESHOLDS

        balance, warning_level = check_balance()

        if balance is None:
            return 0  # Could not fetch, skip silently

        if warning_level:
            warning_msgs = {
                'SEVERE': f"🚨🚨🚨 SEVERELY LOW BALANCE: ${balance:.2f} (threshold: ${BALANCE_THRESHOLDS['severely_low']})\n🔗 Top up: https://console.anthropic.com/settings/billing",
                'CRITICAL': f"⚠️⚠️ CRITICALLY LOW BALANCE: ${balance:.2f} (threshold: ${BALANCE_THRESHOLDS['critically_low']})\n🔗 Top up: https://console.anthropic.com/settings/billing",
                'WARNING': f"⚡ Getting Low Balance: ${balance:.2f} (threshold: ${BALANCE_THRESHOLDS['getting_low']})\n💡 Consider topping up: https://console.anthropic.com/settings/billing"
            }
            print(warning_msgs.get(warning_level, f"Low balance: ${balance:.2f}"))
            return {'WARNING': 1, 'CRITICAL': 2, 'SEVERE': 3}.get(warning_level, 1)
        else:
            # Balance OK, no output
            return 0

    except Exception as e:
        # Fail silently on errors
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
