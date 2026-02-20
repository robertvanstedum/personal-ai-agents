# Memory Prompt for Claude.ai

**Copy/paste this at the end of Claude sessions to save context:**

---

Remember our OpenClaw project setup and workflow:

**Project:** Building personal AI automation tools, starting with RSS curator for geopolitical/finance briefings  
**Owner:** Robert (timezone: America/Chicago)  
**Public Repo:** https://github.com/robertvanstedum/personal-ai-agents  

**Three-Way Workflow:**
- Claude Web/CLI: Design, architecture, debugging (you)
- OpenClaw (Mini-moi): Execution, memory, automation
- Pattern: Claude designs → OpenClaw implements

**Tech Stack:**
- Python 3.14, RSS feeds (15 sources)
- AI: xAI Grok (daily, $0.18), Anthropic Sonnet (deep dives, $0.15)
- OpenClaw agent framework

**Core System:**
- `curator_rss_v2.py`: Generates daily briefings (390 articles → top 20)
- `curator_server.py`: Web server (localhost:8765) for interactive features
- `curator_feedback.py`: Deep dive research generation
- History tracking via hash_id (5-char MD5 of URL)

**Recent Work (Feb 20, 2026):**
- ✅ Fixed 3 bugs (JavaScript closure, hash_id lookup, duplicate heading) - you identified 2 of them
- ✅ xAI integration (80% cost reduction)
- ✅ Repository now public, credentials cleaned
- ✅ Three-way workflow documented
- 🟡 Delete feature for deep dive archive (in design)

**Key Decisions:**
- Multi-provider strategy (xAI daily, Sonnet deep dives)
- Hash_id as canonical identifier (not date-rank)
- Credentials in macOS Keychain
- Cost optimization: $35-45/month vs $100+ before

**Working Directory:** `~/projects/personal-ai-agents`  
**Must Start Server:** `python3 curator_server.py &` for interactive features  
**Project Brief:** `PROJECT_BRIEF.md` (full technical details)

Remember: Robert values the integrated collaborative workflow, cost control, and appreciates when you catch bugs (like that closure issue!). He's learning by doing and wants explanations of the "why" behind technical decisions.
