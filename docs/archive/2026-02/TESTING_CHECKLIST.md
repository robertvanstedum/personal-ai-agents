# Testing Checklist - Deep Dive Feature

> SUPERSEDED 2026-07-21. Written for the original "simple version" Deep Dive
> implementation; the feature has since grown into several coexisting scripts
> now tracked for consolidation in ROADMAP.md and ARCHITECTURE.md. Preserved
> for history.

**Date:** 2026-02-16
**Status:** Ready for testing after break
**Implementation:** Simple version (BeautifulSoup + Anthropic Sonnet 4)

---

## Pre-Test Setup

### 1. Verify Dependencies
```bash
cd ~/Projects/personal-ai-agents
source venv/bin/activate
pip list | grep -E "beautifulsoup4|anthropic|requests"
```

**Expected:**
- ✅ beautifulsoup4 >= 4.14.0
- ✅ anthropic >= 0.40.0
- ✅ requests >= 2.31.0

---

### 2. Verify API Key
```bash
python3 << 'EOF'
import keyring
key = keyring.get_password('anthropic', 'api_key')
if key:
    print(f"✅ API key found: ...{key[-4:]}")
else:
    print("❌ API key not found")
EOF
```

**Expected:** `✅ API key found: ...qAAA`

---

### 3. Verify Recent Briefing Exists
```bash
ls -la curator_output.txt
head -20 curator_output.txt
```

**Expected:** File exists with today's articles

---

## Test Sequence

### Test 1: Component - Article Parsing ✓ (Already Tested)
**Purpose:** Verify flag_article.py can parse briefing

**Command:**
```bash
python3 << 'EOF'
from flag_article import parse_latest_briefing

articles = parse_latest_briefing()
if articles:
    for num in sorted(articles.keys())[:3]:
        data = articles[num]
        print(f"\n#{num}: {data['title'][:60]}...")
        print(f"   Category: {data['category']}")
        print(f"   URL: {data['url'][:50]}...")
else:
    print("❌ No articles found")
EOF
```

**Expected Output:**
```
#1: [Article title]...
   Category: geo_major
   URL: https://...
```

**Status:** ✅ Already working (tested earlier)

---

### Test 2: Component - Article Fetching (NEW - Critical Test)
**Purpose:** Verify BeautifulSoup can extract article text

**Command:**
```bash
python3 << 'EOF'
from deep_dive import fetch_article_content

# Test with a simple article from your feed
url = "https://geopoliticalfutures.com/daily-memo-us-iran-developments-israeli-defense-industry/"

print("Testing article fetch...")
content = fetch_article_content(url)

if content:
    print(f"\n✅ Fetch successful!")
    print(f"   Length: {len(content)} characters")
    print(f"\n   First 300 characters:")
    print(f"   {content[:300]}...")
    
    # Check if it looks like article text (not HTML garbage)
    if '<html>' in content.lower() or '<div>' in content.lower():
        print("\n⚠️  WARNING: Looks like HTML, not clean text")
    else:
        print("\n✅ Content appears clean (no HTML tags)")
else:
    print("\n❌ Fetch failed")
EOF
```

**Expected Output:**
```
Testing article fetch...
📡 Fetching article from https://...
✅ BeautifulSoup: 3421 characters

✅ Fetch successful!
   Length: 3421 characters
   
   First 300 characters:
   [Clean article text, no HTML tags]...

✅ Content appears clean (no HTML tags)
```

**What to verify:**
- ✅ Fetches without error
- ✅ Returns reasonable length (>1000 characters)
- ✅ Text is clean (no `<html>`, `<div>`, etc.)
- ✅ Readable article content (not navigation menu garbage)

**If fails:** Check internet connection, try different URL

---

### Test 3: Component - LLM Analysis (NEW - Costs $0.15!)
**Purpose:** Verify Sonnet 4 can analyze article

**⚠️ WARNING: This costs ~$0.15 - only run when ready**

**Command:**
```bash
python3 << 'EOF'
from deep_dive import fetch_article_content, analyze_with_sonnet

url = "https://geopoliticalfutures.com/daily-memo-us-iran-developments-israeli-defense-industry/"

print("Fetching article...")
content = fetch_article_content(url)

if content:
    print(f"✅ Article fetched ({len(content)} chars)\n")
    print("Analyzing with Sonnet 4 (this will take 30-60 seconds)...")
    print("💰 Cost: ~$0.15\n")
    
    analysis = analyze_with_sonnet(content, url)
    
    if analysis:
        print("✅ Analysis complete!\n")
        print("=" * 80)
        print(analysis[:500])
        print("=" * 80)
        print(f"\nFull length: {len(analysis)} characters")
    else:
        print("❌ Analysis failed")
else:
    print("❌ Fetch failed, skipping analysis")
EOF
```

**Expected Output:**
```
Fetching article...
📡 Fetching article from https://...
✅ BeautifulSoup: 3421 characters
✅ Article fetched (3421 chars)

Analyzing with Sonnet 4 (this will take 30-60 seconds)...
💰 Cost: ~$0.15

🔍 Analyzing with Claude Sonnet 4...
✅ Analysis complete (~600 tokens, ~$0.02)

✅ Analysis complete!

================================================================================
# Analysis: [Article Title]

## Key Implications

• [Implication 1 with time horizons]
• [Implication 2]
...
================================================================================

Full length: 4523 characters
```

**What to verify:**
- ✅ Analysis completes without error
- ✅ Response includes all sections (Key Implications, Contrarian, Challenge, Connections)
- ✅ Quality: Specific insights, not generic fluff
- ✅ Contrarian angle: Challenges mainstream view
- ✅ Length: 500-800 words (~3000-5000 characters)

**If fails:** Check API key, Anthropic account balance

---

### Test 4: Integration - Full Deep Dive (NEW - Final Test)
**Purpose:** End-to-end test of complete workflow

**⚠️ WARNING: This costs ~$0.15 and saves files**

**Command:**
```bash
# Pick an interesting article from today's briefing
python flag_article.py 1 DEEP-DIVE "Testing full integration"
```

**Expected Process:**
1. Parses briefing ✓
2. Stores interest in `interests/2026-02-16-flagged.md` ✓
3. Triggers deep dive subprocess ✓
4. Fetches article (BeautifulSoup) - NEW
5. Analyzes with Sonnet 4 (~30-60s) - NEW
6. Saves to `interests/deep-dives/2026-02-16-*.md` - NEW
7. Sends to Telegram (if OpenClaw running) - OPTIONAL

**Verify each step:**
```bash
# 1. Check interest was stored
cat interests/2026-02-16-flagged.md

# 2. Check deep dive file created
ls -la interests/deep-dives/

# 3. Read the analysis
cat interests/deep-dives/2026-02-16-*.md | head -100

# 4. Check Telegram (if delivered)
# Look in your Telegram for the deep dive message
```

**What to verify:**
- ✅ All files created
- ✅ Analysis quality is good (read it fully)
- ✅ No errors in console output
- ✅ Telegram delivery (optional, if OpenClaw running)

---

### Test 5: Curator Integration (Future Test)
**Purpose:** Verify next briefing applies interest boost

**Command:**
```bash
# Run curator and check for interest loading
python curator_rss_v2.py --mode=mechanical 2>&1 | grep -A 5 "Loaded.*interest"
```

**Expected Output:**
```
📌 Loaded 3 active interests from interests/ directory
   geo_major: 2 flagged articles (+60 total boost)
   fiscal: 1 flagged articles (+50 total boost)
```

**What to verify:**
- ✅ Curator finds and loads interests
- ✅ Counts match flagged articles
- ✅ Score modifiers correct

**Note:** This won't affect today's briefing (already run), test tomorrow morning

---

## Success Criteria

**Minimum (Must Work):**
- ✅ Article fetching extracts clean text
- ✅ Sonnet analysis completes without error
- ✅ Analysis quality is high (specific, insightful)
- ✅ Files saved correctly

**Nice to Have:**
- ✅ Telegram delivery works
- ✅ Processing time <60 seconds
- ✅ Cost under $0.20 per analysis

**Quality Bar:**
- Analysis includes contrarian perspectives
- Challenge factors are specific (not generic)
- Historical connections are relevant
- Overall: Would you read this and find it valuable?

---

## If Something Fails

### Fetch Fails
- Check internet connection
- Try different URL
- Verify URL is accessible in browser
- Check for bot-blocking (User-Agent might be blocked)

### Analysis Fails
- Check API key: `keyring.get_password('anthropic', 'api_key')`
- Check Anthropic account balance
- Verify model name: `claude-sonnet-4-20250514`
- Try shorter article (truncate to 5000 chars)

### File Save Fails
- Check directory exists: `mkdir -p interests/deep-dives`
- Check permissions: `ls -la interests/`
- Verify disk space: `df -h`

### Telegram Fails
- Check OpenClaw running: `openclaw gateway status`
- Check bot token in keyring
- Not critical - analysis still saved locally

---

## After Testing - Next Steps

**If all tests pass:**
1. Commit changes
2. Update README with test results
3. Document any issues found
4. Plan Phase 2 (xAI integration)

**If tests fail:**
1. Debug specific failure
2. Fix implementation
3. Re-test
4. Document what was wrong

---

**Ready to start? Begin with Test 2 (Article Fetching) when back from break.**
