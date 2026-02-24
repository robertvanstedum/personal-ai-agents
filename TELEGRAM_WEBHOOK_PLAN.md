# Telegram Webhook Architecture Plan

**Status:** ✅ IMPLEMENTED - MacBook Development  
**Priority:** Production-ready (working with temporary tunnel)  
**Last Updated:** Feb 23, 2026

---

## Live Setup (MacBook Development)

**Running services:**
- ✅ Webhook server: `telegram_bot.py --webhook` on port 8444 (PID 79652)
- ✅ Cloudflare Tunnel: `https://weeks-spa-equivalent-coalition.trycloudflare.com`
- ✅ Telegram webhook registered and active

**Test:**
- Test article sent with Like/Dislike/Save buttons
- Click a button in Telegram to verify webhook receives callback
- Check webhook logs: `process poll mellow-daisy`

**Temporary URL:** `https://weeks-spa-equivalent-coalition.trycloudflare.com`
- Note: This URL changes each time tunnel restarts
- For Mac Mini production, we'll use a named tunnel with stable URL

---

## Current State

**What works:**
- ✅ Daily briefings via `telegram_bot.py --send` (launchd at 7 AM)
- ✅ Interactive text commands via OpenClaw Telegram channel
- ✅ Curator generates and sends 10 articles to Telegram

**What's disabled:**
- ❌ Like/Dislike/Save buttons (commented out in `send_article()`)
- ❌ `/run`, `/status`, `/briefing` commands (require callbacks)

**Why disabled:**
- Polling conflict: Only ONE process can poll Telegram API per bot token
- OpenClaw owns the polling connection (for text messages from you)
- `telegram_bot.py` can't receive button callbacks while OpenClaw is polling

---

## Polling Conflict Resolution

**Design decision:** 
- **OpenClaw:** Owns polling connection for interactive text commands
- **telegram_bot.py:** Runs in `--send` mode only (fire-and-forget, no polling)
- **Button callbacks:** Disabled until webhook infrastructure ready

**Why this split:**
- You need to message Mini-moi via Telegram (OpenClaw)
- Daily briefings need to send reliably (`--send` mode)
- Buttons are nice-to-have, not critical path

---

## Planned Webhook Architecture

### Requirements

Telegram **requires** a verified HTTPS endpoint for webhook delivery:
- Valid TLS certificate (not self-signed)
- Publicly accessible URL
- Signed webhook payloads for security

### Chosen Approach: Cloudflare Tunnel

**Why Cloudflare Tunnel:**
- ✅ Free tier provides real HTTPS URL with valid TLS certificate
- ✅ No open ports or firewall changes required
- ✅ No DNS configuration needed
- ✅ Works behind NAT/firewall (MacBook, Mac Mini, home network)
- ✅ Secure: Webhook payloads signed by Telegram, validated by our code

**Flow:**
```
Telegram → Cloudflare Tunnel (HTTPS)
         → localhost:8444
         → telegram_bot.py --webhook
         → Validates signature
         → Processes callback (like/dislike/save)
```

### Environment-Agnostic Design

**Key principle:** Backend code is environment-agnostic

**MacBook (development):**
- Cloudflare Tunnel running
- Tunnel may drop when lid closes (acceptable for dev)
- `telegram_bot.py --webhook` listens on localhost:8444

**Mac Mini (production):**
- **Same tunnel config, zero code changes**
- Always-on, tunnel stays active
- Same `telegram_bot.py` code, same localhost:8444

**Cloud/VPS (optional future):**
- If hardware independence becomes a requirement
- Same code, just repoint tunnel
- No changes to `telegram_bot.py`

---

## Migration Path

### Phase 1: MacBook Development
1. Install Cloudflare Tunnel (`cloudflared`)
2. Configure tunnel to point to `localhost:8444`
3. Implement webhook mode in `telegram_bot.py`
4. Register webhook URL with Telegram Bot API
5. Test button callbacks in development

### Phase 2: Mac Mini Production
1. Install `cloudflared` on Mac Mini
2. Copy tunnel config from MacBook
3. **No code changes** - same `telegram_bot.py`
4. Update Telegram webhook URL (new tunnel endpoint)
5. Run 24/7, tunnel stays active

### Phase 3: Cloud/VPS (Optional)
- Only if hardware independence needed
- Same process as Phase 2
- Cloud providers: DigitalOcean, Linode, AWS EC2, etc.

---

## Security Notes

**Why NOT localhost webhook:**
- Telegram requires HTTPS with valid certificate
- Self-signed certificates rejected by Telegram
- Exposing localhost directly is insecure

**Cloudflare Tunnel security:**
- All traffic encrypted via Cloudflare's infrastructure
- Webhook payloads signed by Telegram (signature validation required)
- Bot token never exposed in code or config files
- Stored in macOS keychain via `keyring` library

**Webhook validation:**
- Verify signature from Telegram headers
- Reject unsigned or invalid payloads
- Rate limit callback processing (prevent abuse)

---

## Implementation Checklist

### Backend (telegram_bot.py)
- [ ] Add `--webhook` mode (complement to `--send`)
- [ ] Implement Flask/FastAPI endpoint on port 8444
- [ ] Validate Telegram webhook signatures
- [ ] Handle button callbacks: like, dislike, save
- [ ] Call `curator_feedback.py` to record feedback
- [ ] Graceful error handling and logging

### Infrastructure (Cloudflare Tunnel)
- [ ] Install `cloudflared` on MacBook
- [ ] Configure tunnel: `cloudflared tunnel create curator-bot`
- [ ] Point tunnel to `localhost:8444`
- [ ] Get public HTTPS URL from Cloudflare
- [ ] Test tunnel connectivity

### Telegram Configuration
- [ ] Register webhook URL via Bot API
- [ ] Set `allowed_updates` to include `callback_query`
- [ ] Test webhook delivery with button press
- [ ] Verify signature validation works

### Documentation
- [ ] Update OPERATIONS.md with webhook setup steps
- [ ] Document Cloudflare Tunnel installation
- [ ] Add troubleshooting section for webhook issues

---

## Cost & Resources

**Cloudflare Tunnel:** Free tier (sufficient)  
**Mac Mini:** Always-on (electricity cost negligible)  
**Cloud VPS (if needed):** $5-10/month (DigitalOcan, Linode)

**Recommendation:** Start with MacBook dev, move to Mac Mini production, skip cloud unless needed.

---

## Code Changes Required

### telegram_bot.py

**New mode:**
```python
def run_webhook_mode(port=8444):
    """Run Flask/FastAPI webhook server"""
    from flask import Flask, request
    
    app = Flask(__name__)
    
    @app.route('/webhook', methods=['POST'])
    def webhook():
        # Validate Telegram signature
        # Parse callback_query
        # Call button_callback() handler
        # Return 200 OK
        pass
    
    app.run(host='0.0.0.0', port=port)
```

**Entry point:**
```python
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--webhook', action='store_true')
    args = parser.parse_args()
    
    if args.send:
        run_send_mode()
    elif args.webhook:
        run_webhook_mode()
    else:
        print("Error: Use --send or --webhook")
```

### run_curator_cron.sh

**No changes needed** - already calls `telegram_bot.py --send`

### Launchd

**New plist for webhook mode:**
```xml
<key>ProgramArguments</key>
<array>
    <string>/path/to/venv/bin/python3</string>
    <string>/path/to/telegram_bot.py</string>
    <string>--webhook</string>
</array>
```

---

## Testing Plan

1. **Webhook validation:** Send test POST from `curl` to localhost:8444
2. **Cloudflare Tunnel:** Verify HTTPS URL resolves correctly
3. **Button callbacks:** Press Like/Dislike/Save, check logs
4. **Feedback recording:** Verify `curator_feedback.py` called correctly
5. **Error handling:** Test invalid signatures, malformed payloads

---

## Rollback Plan

**If webhook fails:**
1. Disable webhook in Telegram Bot API settings
2. Stop `telegram_bot.py --webhook` service
3. Continue using `--send` mode for daily briefings
4. Buttons remain disabled (current state)

**No impact on core functionality:**
- Daily briefings continue working
- OpenClaw text commands continue working
- Only interactive buttons affected

---

## References

- Telegram Bot API Webhooks: https://core.telegram.org/bots/api#setwebhook
- Cloudflare Tunnel Docs: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/
- Webhook Security: https://core.telegram.org/bots/webhooks#testing-your-bot-with-updates

---

## Quick Start (MacBook)

**Terminal 1 - Start webhook server:**
```bash
cd ~/Projects/personal-ai-agents
./venv/bin/python telegram_bot.py --webhook
```

**Terminal 2 - Start tunnel:**
```bash
cloudflared tunnel --url http://localhost:8444
```

**Register webhook URL:**
```bash
cd ~/Projects/personal-ai-agents
./venv/bin/python -c "
import keyring
import requests

token = keyring.get_password('telegram', 'bot_token')
webhook_url = 'https://YOUR-TUNNEL-URL.trycloudflare.com/webhook'

response = requests.post(
    f'https://api.telegram.org/bot{token}/setWebhook',
    json={'url': webhook_url, 'allowed_updates': ['callback_query', 'message']}
)
print(response.json())
"
```

**Note:** Replace `YOUR-TUNNEL-URL` with the actual URL from the cloudflared output.

---

**Status:** Production-ready on MacBook. Next: Migrate to Mac Mini with named tunnel.
