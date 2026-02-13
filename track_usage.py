#!/usr/bin/env python3
"""
Daily Usage Tracker - Monitor Anthropic API usage & costs

Tracks:
- API calls
- Tokens (input/output/cache read/write)
- Estimated costs
- Per-session breakdown

Storage: JSON (for now) → migrate to Postgres later
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

# Pricing (as of Feb 2026 - Claude Sonnet 4)
PRICING = {
    "input": 0.003 / 1000,      # $3 per million
    "output": 0.015 / 1000,     # $15 per million
    "cache_read": 0.03 / 1000,  # $0.30 per million
    "cache_write": 0.00375 / 1000,  # $3.75 per million
}

USAGE_LOG = Path.home() / ".openclaw" / "workspace" / "logs" / "usage" / "daily_usage.json"

def load_usage_log() -> Dict:
    """Load existing usage log or create new"""
    if USAGE_LOG.exists():
        with open(USAGE_LOG, "r") as f:
            return json.load(f)
    return {"days": {}, "sessions": {}}

def save_usage_log(data: Dict):
    """Save usage log"""
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE_LOG, "w") as f:
        json.dump(data, f, indent=2)

def get_openclaw_sessions() -> List[Dict]:
    """Parse OpenClaw session transcripts for usage data"""
    from glob import glob
    from datetime import datetime as dt
    
    sessions = []
    
    # Find all session files
    session_dir = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
    session_files = glob(str(session_dir / "*.jsonl"))
    
    for filepath in session_files:
        try:
            with open(filepath, "r") as f:
                for line in f:
                    try:
                        event = json.loads(line.strip())
                        
                        # Only process message events with usage data
                        if event.get("type") == "message":
                            message = event.get("message", {})
                            usage = message.get("usage")
                            timestamp = message.get("timestamp")
                            
                            if usage and timestamp:
                                # Convert timestamp (milliseconds) to date
                                date = dt.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d")
                                
                                sessions.append({
                                    "date": date,
                                    "timestamp": timestamp,
                                    "usage": usage,
                                    "model": message.get("model", "unknown"),
                                    "provider": message.get("provider", "unknown")
                                })
                    except json.JSONDecodeError:
                        continue  # Skip malformed lines
        except Exception as e:
            print(f"Warning: Could not read {filepath}: {e}")
            continue
    
    return sessions

def calculate_daily_cost(tokens: Dict) -> float:
    """Calculate cost from token counts"""
    cost = 0.0
    cost += tokens.get("input", 0) * PRICING["input"]
    cost += tokens.get("output", 0) * PRICING["output"]
    cost += tokens.get("cache_read", 0) * PRICING["cache_read"]
    cost += tokens.get("cache_write", 0) * PRICING["cache_write"]
    return cost

def track_today():
    """Track today's usage and update log"""
    today = datetime.now().strftime("%Y-%m-%d")
    
    log = load_usage_log()
    
    # Get all sessions
    all_sessions = get_openclaw_sessions()
    
    # Group by date
    sessions_by_date = {}
    for session in all_sessions:
        date = session["date"]
        if date not in sessions_by_date:
            sessions_by_date[date] = []
        sessions_by_date[date].append(session)
    
    # Process each date
    for date, sessions in sessions_by_date.items():
        if date not in log["days"]:
            log["days"][date] = {
                "date": date,
                "api_calls": 0,
                "tokens": {
                    "input": 0,
                    "output": 0,
                    "cache_read": 0,
                    "cache_write": 0,
                    "total": 0
                },
                "cost_usd": 0.0,
                "sessions": []
            }
        
        day_data = log["days"][date]
        day_data["api_calls"] = len(sessions)
        
        # Reset counters
        day_data["tokens"] = {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
            "total": 0
        }
        day_data["cost_usd"] = 0.0
        
        # Aggregate usage
        for session in sessions:
            usage = session.get("usage", {})
            cost = usage.get("cost", {})
            
            day_data["tokens"]["input"] += usage.get("input", 0)
            day_data["tokens"]["output"] += usage.get("output", 0)
            day_data["tokens"]["cache_read"] += usage.get("cacheRead", 0)
            day_data["tokens"]["cache_write"] += usage.get("cacheWrite", 0)
            
            # Use actual cost from API if available
            if cost.get("total"):
                day_data["cost_usd"] += cost["total"]
        
        day_data["tokens"]["total"] = sum([
            day_data["tokens"]["input"],
            day_data["tokens"]["output"],
            day_data["tokens"]["cache_read"],
            day_data["tokens"]["cache_write"]
        ])
    
    save_usage_log(log)
    
    # Return today's data
    return log["days"].get(today, {
        "date": today,
        "api_calls": 0,
        "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
        "cost_usd": 0.0
    })

def get_weekly_summary() -> Dict:
    """Get last 7 days summary"""
    log = load_usage_log()
    
    today = datetime.now()
    week_dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    
    summary = {
        "period": f"{week_dates[-1]} to {week_dates[0]}",
        "total_calls": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "daily_average": 0.0,
        "days": []
    }
    
    for date in week_dates:
        if date in log["days"]:
            day = log["days"][date]
            summary["total_calls"] += day["api_calls"]
            summary["total_tokens"] += day["tokens"]["total"]
            summary["total_cost"] += day["cost_usd"]
            summary["days"].append(day)
    
    if len(summary["days"]) > 0:
        summary["daily_average"] = summary["total_cost"] / len(summary["days"])
    
    return summary

def format_report() -> str:
    """Generate human-readable report"""
    today_data = track_today()
    weekly = get_weekly_summary()
    
    # Get yesterday's data if available
    from datetime import timedelta
    log = load_usage_log()
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_data = log["days"].get(yesterday)
    
    report = f"""📊 *Daily Usage Report* - {today_data['date']}

*Today (so far):*
• API Calls: {today_data['api_calls']:,}
• Tokens: {today_data['tokens']['total']:,}
• Cost: ${today_data['cost_usd']:.2f}
"""
    
    if yesterday_data:
        report += f"""
*Yesterday ({yesterday}):*
• API Calls: {yesterday_data['api_calls']:,}
• Tokens: {yesterday_data['tokens']['total']:,}
• Cost: ${yesterday_data['cost_usd']:.2f}
"""
    
    report += f"""
*This Week:*
• Total Calls: {weekly['total_calls']:,}
• Total Tokens: {weekly['total_tokens']:,}
• Total Cost: ${weekly['total_cost']:.2f}
• Daily Average: ${weekly['daily_average']:.2f}

💡 Check balance: https://console.anthropic.com/settings/billing
"""
    
    return report

if __name__ == "__main__":
    print(format_report())
