# TODO: Multi-Provider Curator Support

## Quick Start (Tomorrow Morning)

```bash
cd ~/Projects/personal-ai-agents
source venv/bin/activate
pip install openai
```

## Goal
Add OpenAI SDK support to enable both OpenAI and xAI models alongside Anthropic.

## Why
- **Cost reduction**: 83% cheaper ($0.90/day → $0.15/day)
- **Flexibility**: Three cost/quality tiers for different tasks
- **Comparison**: A/B test providers for optimal strategy

## Three Tiers

| Provider | Model | Cost (in/out per M) | Use Case |
|----------|-------|---------------------|----------|
| OpenAI | gpt-4o-mini | $0.15 / $0.60 | Bulk pre-filtering (cheapest) |
| xAI | grok-2-vision-1212 | $5 / $15 | Main curation (middle tier) |
| Anthropic | claude-sonnet-4 | $3 / $15 | Final ranking (best quality) |

## Implementation Steps

### 1. Add OpenAI SDK
```python
# In curator_rss_v2.py
from openai import OpenAI

# Initialize client
openai_client = OpenAI(api_key="...")
xai_client = OpenAI(api_key="xai-...", base_url="https://api.x.ai/v1")
```

### 2. Add Scoring Functions
- `score_entries_openai()` - for GPT-4o-mini
- `score_entries_xai()` - for Grok (wrapper to OpenAI SDK)

### 3. Add CLI Flag
```bash
python3 curator_rss_v2.py --provider openai|xai|anthropic
python3 curator_rss_v2.py --provider xai  # Test first (key ready)
```

### 4. Test & Compare
- Run same articles through all three
- Compare: quality, cost, speed
- Document findings

## xAI Key Location
- File: `~/.openclaw/agents/main/agent/auth-profiles.json`
- Profile: `xai:default`
- Model: `grok-2-vision-1212`

## Expected Outcome
- Stage 1 (pre-filter): GPT-4o-mini filters 150 → 50 articles (~$0.02)
- Stage 2 (ranking): Grok-2-vision ranks 50 → 20 (~$0.13)
- Total: ~$0.15/day vs current $0.90/day
- **Portfolio item:** "83% cost reduction via multi-provider optimization"

## Files to Modify
- `curator_rss_v2.py` - Add OpenAI SDK, new scoring functions, CLI flag
- `CHANGELOG.md` - Document multi-provider support
- `docs/MULTI_PROVIDER.md` - Provider comparison guide (new file)

## Status
🟡 Ready to start - xAI key configured, plan documented

## First Test
```bash
# Test xAI with OpenAI SDK
# API key will be loaded from keychain via get_xai_api_key()
python3 << 'PYEOF'
from openai import OpenAI
import keyring

# Get API key from keychain
api_key = keyring.get_password("xai", "api_key")

client = OpenAI(
    api_key=api_key,
    base_url="https://api.x.ai/v1"
)

response = client.chat.completions.create(
    model="grok-2-vision-1212",
    messages=[{"role": "user", "content": "Test"}],
    max_tokens=10
)

print(response.choices[0].message.content)
PYEOF
```
