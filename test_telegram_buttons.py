#!/usr/bin/env python3
"""Quick test: Send one article with feedback buttons to Telegram"""

import os
import json
import keyring
import requests

def get_telegram_token():
    try:
        return keyring.get_password("telegram", "bot_token")
    except:
        return os.environ.get('TELEGRAM_BOT_TOKEN')

token = get_telegram_token()
chat_id = os.environ.get('TELEGRAM_CHAT_ID', 'YOUR_TELEGRAM_CHAT_ID')

if not token:
    print("❌ No Telegram token found")
    exit(1)

# Test message with buttons
message = """<b>#3</b> • FISCAL • Fed On The Economy

<b>Inflation and the Accuracy of Public Debt Forecasts</b>

<a href="https://www.stlouisfed.org/on-the-economy/2026/feb/inflation-accuracy-public-debt-forecasts">🔗 Read article</a>

Score: 5.7/10 (mechanical)"""

# Inline keyboard buttons
keyboard = {
    "inline_keyboard": [[
        {"text": "👍 Like", "callback_data": "like:3"},
        {"text": "👎 Dislike", "callback_data": "dislike:3"},
        {"text": "🔖 Save", "callback_data": "save:3"}
    ]]
}

url = f"https://api.telegram.org/bot{token}/sendMessage"
data = {
    "chat_id": chat_id,
    "text": message,
    "parse_mode": "HTML",
    "reply_markup": keyboard,
    "disable_web_page_preview": True
}

print("📤 Sending test article to Telegram...")
response = requests.post(url, json=data, timeout=10)

if response.status_code == 200:
    print("✅ Sent! Check your Telegram")
    print("📱 Tap the buttons to test feedback")
    print("\n⚠️  Note: You need to run telegram_feedback_bot.py to handle button clicks")
else:
    print(f"❌ Error: {response.status_code}")
    print(response.text)
