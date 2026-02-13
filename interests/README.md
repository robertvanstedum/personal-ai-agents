# Interest Capture System - Quick Start

**Purpose:** Track what you want to learn, prioritize briefing content, mute noise.

---

## The Four Signals

| Tag | Meaning | Briefing Boost | Duration |
|-----|---------|----------------|----------|
| `[DEEP-DIVE]` | Research now | +50 points | 3 days |
| `[THIS-WEEK]` | Keep seeing this | +30 points | 7 days |
| `[BACKLOG]` | Explore eventually | +10 points | No expiry |
| `[MUTE]` | Less of this | -20 points | 7 days |

---

## Three Ways to Capture

### 1. Command Line (Fastest)

```bash
cd /Users/vanstedum/Projects/personal-ai-agents

# Quick capture
python3 capture_interest.py "[THIS-WEEK] Iran sanctions endgame"

# Deep dive
python3 capture_interest.py "[DEEP-DIVE] China gold strategy - why now?"

# Mute noise
python3 capture_interest.py "[MUTE] Sports betting content"
```

### 2. Edit File Directly (Most Control)

```bash
# Open today's file
code interests/2026-02-13-thoughts.md

# Or use any editor
nano interests/2026-02-13-thoughts.md
```

**Add under appropriate section:**
```markdown
## 📌 This Week's Focus

- [10:30] Iran sanctions escalation
  - US considering tanker seizures
  - Legal basis? Historical precedents?
```

### 3. Via Telegram (Coming Tonight)

```
Me (8pm): 📝 Daily Review - What interested you today?

You: [THIS-WEEK] Iran tanker seizure legality

Me: ✅ Captured to interests/2026-02-13-thoughts.md
```

---

## Typical Workflow

### Morning (After Briefing)

1. Read your 7am briefing
2. Note interesting topics:
   ```bash
   python3 capture_interest.py "[THIS-WEEK] China dumping US debt"
   python3 capture_interest.py "[DEEP-DIVE] Gold accumulation patterns"
   ```

### During Day

Add thoughts as they come:
```bash
python3 capture_interest.py "[BACKLOG] Check Fed meeting minutes in March"
```

### Evening (Optional)

Review and refine:
```bash
code interests/2026-02-13-thoughts.md
# Add questions, links, context
```

---

## When AI Curation Is Added (Phase 2.2)

**Current:** Keywords + recency + source weights  
**Future:** Keywords + recency + **your interests**

**How it works:**
1. Parse `interests/` files daily
2. Extract active tags (not expired)
3. Match article topics to your interests
4. Boost/suppress scoring:
   - Article about Iran + you have `[DEEP-DIVE] Iran sanctions` → +50 points
   - Article about sports + you have `[MUTE] Sports` → -20 points

**Result:** Briefings adapt to what you're thinking about, without manual tuning.

---

## Examples

### After Reading Briefing

```bash
# Top article was fascinating
python3 capture_interest.py "[DEEP-DIVE] China's gold buying - what's the target?"

# Want to track this trend
python3 capture_interest.py "[THIS-WEEK] Iranian sanctions escalation"

# Not interested right now
python3 capture_interest.py "[MUTE] Cryptocurrency market commentary"
```

### From Conversation

```bash
# Discussed geopolitics with colleague
python3 capture_interest.py "[THIS-WEEK] Russia-China trade dynamics"

# Random thought
python3 capture_interest.py "[BACKLOG] Research historical gold-to-debt ratios"
```

---

## Tips

**Start simple:**
- Just use `[THIS-WEEK]` for everything at first
- Get comfortable with the workflow
- Add other tags as needed

**Be honest:**
- Mute what doesn't serve you (you can unmute later)
- Deep-dive = real commitment (limits: 2-3 max)
- Backlog = guilt-free "maybe someday"

**Review weekly:**
- Check what graduated (THIS-WEEK → BACKLOG)
- Promote if still interested (BACKLOG → THIS-WEEK)
- Delete what's no longer relevant

**Don't overthink:**
- Quick capture > perfect categorization
- Can always refine in file later
- The system learns from your patterns

---

## File Structure

```
interests/
├── README.md              (this file)
├── TEMPLATE.md            (daily file template)
├── 2026-02-13-thoughts.md (today's captures)
├── 2026-02-14-thoughts.md (tomorrow)
└── ...
```

**Each daily file:**
- Section per tag
- Timestamped entries
- Optional notes/questions
- Links to articles

---

## Commands Reference

```bash
# Capture interest
python3 capture_interest.py "[TAG] Topic"

# See today's file
cat interests/$(date +%Y-%m-%d)-thoughts.md

# Edit today's file
nano interests/$(date +%Y-%m-%d)-thoughts.md

# List all interest files
ls -lt interests/*.md

# Search across all files
grep -r "Iran" interests/
```

---

## What's Next

**Tonight (1-2 hour session):**
- Add evening Telegram reminder (8pm)
- Test both capture methods
- Refine based on what feels natural

**This Week:**
- Build habit of daily capture
- Populate 3-5 interests
- See what workflow fits

**Phase 2.2 (Future):**
- Connect to curator scoring
- Automatic article boosting
- Learning loop (tracks what you actually read)

---

_Foundation for context-aware curation - briefings that adapt to your evolving interests._
