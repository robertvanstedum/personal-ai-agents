# xAI Budget Tracker

## Current Balance
- **Budget:** $25.00
- **Status:** 🟢 Safe (139 days at $0.18/day)

## Usage Limits
- **Daily Curator (--mode=xai):** ~$0.18/day
- **Monthly:** ~$5.40
- **Quarterly:** ~$16.20
- **Annual:** ~$65.70

## Safe Operating Limits
| Balance | Status | Action |
|---------|--------|--------|
| $25-10 | 🟢 Safe | Use xAI freely for curator |
| $10-5 | 🟡 Caution | Monitor daily, prepare fallback |
| $5-2 | 🔴 Warning | Switch curator to mechanical mode |
| <$2 | 🛑 Critical | Stop xAI usage, use Anthropic only |

## Strategy
1. Use xAI ONLY for curator (--mode=xai)
2. Use Anthropic for chat (better value for reasoning)
3. Monitor balance weekly
4. If balance drops below $10, switch to mechanical

## Fallback Plans
**If xAI runs out:**
- Curator: `--mode=mechanical` (free, keyword-based)
- Chat: Anthropic (fully funded)
- Zero service interruption

## Commands
```bash
# Check xAI balance (manual via dashboard)
# https://console.x.ai/billing

# Run curator with xAI
python curator_rss_v2.py --mode=xai

# Run curator with mechanical (if low on funds)
python curator_rss_v2.py --mode=mechanical

# Switch chat back to Claude
/model anthropic/claude-sonnet-4-5
```
