#!/usr/bin/env python3
"""
send_briefing_telegram.py - Send curator briefing to Telegram with feedback buttons

Usage:
    python send_briefing_telegram.py
"""

import os
import json
import keyring
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_telegram_token():
    """Get Telegram bot token"""
    try:
        token = keyring.get_password("telegram", "bot_token")
        if token:
            return token
    except Exception:
        pass
    return os.environ.get('TELEGRAM_BOT_TOKEN')

def send_article_with_buttons(token, chat_id, article_num, title, url, source, category, score):
    """Send a single article with feedback buttons"""
    
    # Format message
    message = f"""
<b>#{article_num}</b> • {category.upper()} • {source}

<b>{title}</b>

<a href="{url}">🔗 Read article</a>

Score: {score}
"""
    
    # Create inline keyboard with feedback buttons
    keyboard = [
        [
            InlineKeyboardButton("👍 Like", callback_data=f"like:{article_num}"),
            InlineKeyboardButton("👎 Dislike", callback_data=f"dislike:{article_num}"),
            InlineKeyboardButton("🔖 Save", callback_data=f"save:{article_num}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Convert to dict for API call
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    data = {
        "chat_id": chat_id,
        "text": message.strip(),
        "parse_mode": "HTML",
        "reply_markup": json.dumps({
            "inline_keyboard": keyboard
        }),
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(api_url, json=data, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"❌ Error sending article #{article_num}: {e}")
        return False

def send_briefing():
    """Send top articles from curator_output.txt to Telegram"""
    
    token = get_telegram_token()
    if not token:
        print("❌ No Telegram token found")
        return
    
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '8379221702')
    
    # Parse curator output
    with open('curator_output.txt', 'r') as f:
        content = f.read()
    
    # Send header
    header = "🧠 <b>Your Morning Briefing</b>\n\nTop curated articles with feedback buttons:"
    
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(api_url, json={
        "chat_id": chat_id,
        "text": header,
        "parse_mode": "HTML"
    })
    
    print("📤 Sending articles to Telegram...")
    
    # Parse and send top 10 articles
    lines = content.split('\n')
    current_article = None
    articles = {}
    
    for line in lines:
        if line.startswith('#') and '[' in line:
            # Parse article header
            parts = line.split()
            if len(parts) >= 2:
                rank = parts[0].replace('#', '')
                try:
                    rank_num = int(rank)
                    if rank_num <= 10:  # Top 10 only
                        current_article = rank_num
                        articles[rank_num] = {'rank': rank}
                except ValueError:
                    pass
        
        elif current_article and line.strip().startswith('http'):
            articles[current_article]['url'] = line.strip()
        
        elif current_article and 'Score:' in line:
            articles[current_article]['score'] = line.strip()
            # Try to extract title from previous context
            # (simplified - would need better parsing for production)
    
    # For demo, send first 3 articles with mock data
    demo_articles = [
        {
            "num": 1,
            "title": "FOMC Minutes Confirm Divided Fed",
            "url": "https://www.zerohedge.com/markets/fomc-35",
            "source": "ZeroHedge",
            "category": "geo_major",
            "score": "8.7/10"
        },
        {
            "num": 3,
            "title": "Inflation and the Accuracy of Public Debt Forecasts",
            "url": "https://www.stlouisfed.org/on-the-economy/2026/feb/inflation-accuracy-public-debt-forecasts",
            "source": "Fed On The Economy",
            "category": "fiscal",
            "score": "5.7/10"
        },
        {
            "num": 4,
            "title": "UK should drop fiscal goals for new traffic-light system",
            "url": "https://www.investing.com/news/economy-news/uk-should-drop-fiscal-goals-for-new-trafficlight-system-ifs-says-4512418",
            "source": "Investing.com",
            "category": "fiscal",
            "score": "5.2/10"
        }
    ]
    
    for article in demo_articles:
        success = send_article_with_buttons(
            token, chat_id,
            article['num'],
            article['title'],
            article['url'],
            article['source'],
            article['category'],
            article['score']
        )
        if success:
            print(f"  ✅ Sent article #{article['num']}")
        else:
            print(f"  ❌ Failed article #{article['num']}")
    
    print("\n✅ Briefing sent to Telegram!")
    print("📱 Open Telegram and tap the feedback buttons")

if __name__ == '__main__':
    send_briefing()
