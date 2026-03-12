# personal-ai-agents

Read _NewDomains/PROJECT_STATE.md first before
starting any work.

Do not modify protected files without explicit
instruction from Robert.

Protected: README.md, CHANGELOG.md, OPERATIONS.md,
WHITEBOARD.md, docs/*

---

## Agent Division of Labor

**Claude Code (you):** Implementation only.
- Read GitHub issues and specs as your brief
- Write code, commit changes
- Do NOT create GitHub issues
- Do NOT update CHANGELOG.md or roadmap docs independently
- Those updates happen after Robert reviews and confirms your work

**OpenClaw:** Planning, documentation, memory layer.
- Creates issues, updates roadmap, CHANGELOG, specs, memory files
- Reads code for context but does not write implementation code

**Robert:** Decision point between agents.
- Reviews OpenClaw output (issue, spec) before handing to Claude Code
- Reviews Claude Code changes before merge/commit when possible
- One agent active on the repo at a time — not both in the same session

**Intent:** OpenClaw plans → Robert approves → Claude Code builds → Robert reviews.
This prevents conflicts, duplicate work, and agents overwriting each other.
