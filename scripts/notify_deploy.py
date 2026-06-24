"""
scripts/notify_deploy.py — Send Telegram notification after CI pipeline run.

Called by CI notify job:
  python3 scripts/notify_deploy.py \
    --deploy-status success \
    --test-status success \
    --sha a1b2c3d \
    --branch main
"""

import argparse
import os
import sys

import requests


def send_telegram(token: str, chat_id: str, text: str):
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    if not r.ok:
        print(f"Telegram send failed: {r.status_code} {r.text}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deploy-status", default="skipped")
    parser.add_argument("--test-status", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set — skipping notification", file=sys.stderr)
        return

    sha = args.sha[:7]
    branch = args.branch
    test_ok = args.test_status == "success"
    deploy_ok = args.deploy_status == "success"
    deploy_skipped = args.deploy_status in ("skipped", "")

    if test_ok and deploy_ok:
        text = (
            f"✅ <b>mini-moi deployed</b>\n"
            f"Branch: {branch} | Commit: {sha}\n"
            f"Tests: passed | Deploy: success"
        )
    elif not test_ok:
        text = (
            f"❌ <b>Deploy blocked — tests failed</b>\n"
            f"Branch: {branch} | Commit: {sha}\n"
            f"GitHub issues filed for each failure."
        )
    elif deploy_skipped:
        text = (
            f"✅ <b>Tests passed</b> (PR — no deploy)\n"
            f"Branch: {branch} | Commit: {sha}"
        )
    else:
        text = (
            f"⚠️ <b>Tests passed but deploy failed</b>\n"
            f"Branch: {branch} | Commit: {sha}"
        )

    send_telegram(token, chat_id, text)
    print(f"Notification sent: {text[:80]}")


if __name__ == "__main__":
    main()
