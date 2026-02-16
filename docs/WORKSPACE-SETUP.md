# Workspace Setup Guide

Your **workspace** is the agent's persistent context - personality, memory, and daily logs. It lives in `~/.openclaw/workspace/` and should be kept in a **private** git repository.

---

## Why Separate Workspace from Code?

**Public repository (personal-ai-agents):**
- Automation scripts and tools
- Documentation and strategy
- Shareable, portfolio-worthy

**Private workspace repository:**
- Agent personality configuration
- Your personal knowledge graph (MEMORY.md)
- Daily conversation logs
- Private notes and context

**Benefit:** You can share your automation code publicly without exposing your personal information.

---

## Initial Setup

### 1. Create Workspace Directory

OpenClaw creates this automatically on first run:

```bash
~/.openclaw/workspace/
```

### 2. Copy Template Files

```bash
cd ~/.openclaw/workspace

# Copy templates from the public repo
cp ~/Projects/personal-ai-agents/docs/workspace-templates/SOUL.md.template SOUL.md
cp ~/Projects/personal-ai-agents/docs/workspace-templates/USER.md.template USER.md

# Create other required files
touch AGENTS.md
touch TOOLS.md
touch IDENTITY.md
touch MEMORY.md
touch HEARTBEAT.md
mkdir -p memory
```

### 3. Customize Files

**SOUL.md** - Define your agent's personality
- How should it communicate?
- What boundaries should it respect?
- What's its core behavior?

**USER.md** - Information about you
- Your name and timezone
- Your goals and interests
- How you like to work

**IDENTITY.md** - Agent's identity
- Name of your agent
- Its role/purpose
- Any emoji or branding

**AGENTS.md** - Operational guidelines
- Where files are stored
- How to handle different situations
- Group chat behavior
- Heartbeat logic

**TOOLS.md** - Environment-specific notes
- Camera names and locations
- SSH hosts
- Device nicknames
- Custom tool configurations

**MEMORY.md** - Long-term memory
- Lessons learned
- Important decisions
- Preferences and patterns
- Technical discoveries

**HEARTBEAT.md** - Periodic task checklist
- What to check during heartbeats
- How often to check various things
- When to alert you

---

## File Purposes

### Core Identity Files
- **SOUL.md** - Personality and values (read every session)
- **USER.md** - About you (read every session)
- **IDENTITY.md** - Agent name and role

### Operational Files
- **AGENTS.md** - Behavioral guidelines and procedures
- **TOOLS.md** - Environment-specific tool notes
- **HEARTBEAT.md** - Periodic task checklist

### Memory System
- **MEMORY.md** - Curated long-term memory (main session only)
- **memory/YYYY-MM-DD.md** - Daily logs (raw chronological records)

### Private Notes
- **SCRATCH.md** - Temporary brainstorming (gitignored, disposable)
- **interests/** - Captured thoughts and research notes

---

## Git Setup (Private Repository)

### 1. Initialize Git

```bash
cd ~/.openclaw/workspace
git init
```

### 2. Create .gitignore

Already included with OpenClaw, but verify:

```bash
cat .gitignore
```

Should exclude:
- `*.key`, `*.pem`, `*secret*`, `*token*`
- API keys and credentials
- `SCRATCH.md` (private brainstorming)

### 3. Create Private Repository

**Option A: GitHub Private Repo**
```bash
# On GitHub: Create new private repository (e.g., "rvs-openclaw-workspace")

git remote add origin https://github.com/YOUR_USERNAME/YOUR_WORKSPACE_REPO.git
git add -A
git commit -m "Initial workspace setup"
git branch -M main
git push -u origin main
```

**Option B: Local Only**

If you don't want cloud backup, just commit locally:
```bash
git add -A
git commit -m "Initial workspace setup"
```

---

## Security Best Practices

### ✅ Safe to Commit
- Personality files (SOUL.md, AGENTS.md)
- User preferences (USER.md)
- Memory and daily logs (MEMORY.md, memory/*.md)
- Documentation and notes

### ❌ Never Commit
- API keys or tokens
- Passwords or credentials
- Private encryption keys
- OAuth secrets

### 🔐 Credential Management

**Use system keyring instead of files:**

```python
import keyring

# Store
keyring.set_password("anthropic", "api_key", "sk-ant-...")

# Retrieve
api_key = keyring.get_password("anthropic", "api_key")
```

**See:** [CREDENTIALS_SETUP.md](../CREDENTIALS_SETUP.md) in the main repo

---

## Daily Workflow

### Agent Startup (Every Session)

The agent automatically reads:
1. **SOUL.md** - Who it is
2. **USER.md** - Who you are
3. **memory/YYYY-MM-DD.md** - Today's log
4. **memory/YYYY-MM-DD.md** (yesterday) - Recent context
5. **MEMORY.md** - Long-term memory (main session only)

### Memory Recording

**Daily logs** (`memory/YYYY-MM-DD.md`):
- Raw chronological records
- Decisions made
- Things learned
- Events that happened

**Long-term memory** (`MEMORY.md`):
- Curated insights from daily logs
- Technical lessons learned
- Cost management strategies
- Architecture decisions
- Behavioral patterns

The agent periodically reviews daily logs and updates MEMORY.md with what's worth keeping long-term.

---

## Maintenance

### Weekly Review
```bash
cd ~/.openclaw/workspace

# Review recent daily logs
ls -lt memory/*.md | head -7

# Commit updates
git add -A
git commit -m "Weekly memory update"
git push
```

### Monthly Cleanup
- Archive old daily logs
- Update MEMORY.md with significant learnings
- Prune outdated information
- Review .gitignore for new secret patterns

---

## Migrating to a New Machine

### From Laptop to Mac Mini (Example)

**On old machine:**
```bash
cd ~/.openclaw/workspace
git push  # Ensure everything is backed up
```

**On new machine:**
```bash
# Install OpenClaw
npm install -g openclaw@latest

# Clone workspace
git clone https://github.com/YOUR_USERNAME/YOUR_WORKSPACE_REPO.git ~/.openclaw/workspace

# Restore credentials from keyring backup or re-enter
python setup_credentials.py

# Install automation scripts
git clone https://github.com/YOUR_USERNAME/personal-ai-agents.git ~/Projects/personal-ai-agents
```

**All your context migrates instantly** because it's in git.

---

## Troubleshooting

### Agent seems to forget things
- Check that MEMORY.md is being read (main session only)
- Verify daily logs are being created in `memory/`
- Ensure workspace files are in `~/.openclaw/workspace/`

### Sensitive data committed by accident
```bash
# Remove from git history
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch path/to/file" \
  --prune-empty --tag-name-filter cat -- --all

# Rotate the exposed credential immediately
```

### Merge conflicts after manual edits
```bash
# Back up current state
cp MEMORY.md MEMORY.md.backup

# Pull remote changes
git pull

# Manually merge if needed
# Then commit
git add MEMORY.md
git commit -m "Merge manual and agent updates"
```

---

## Example Workspace Structure

```
~/.openclaw/workspace/
├── .git/                   # Git repository
├── .gitignore             # Excludes secrets
├── SOUL.md                # Agent personality
├── USER.md                # About you
├── IDENTITY.md            # Agent name/role
├── AGENTS.md              # Operational guidelines
├── TOOLS.md               # Environment-specific notes
├── MEMORY.md              # Long-term memory
├── HEARTBEAT.md           # Periodic tasks
├── SCRATCH.md             # Private brainstorming (gitignored)
├── memory/                # Daily logs
│   ├── 2026-02-13.md
│   ├── 2026-02-14.md
│   └── 2026-02-15.md
├── interests/             # Captured research
│   └── 2026-02-14-geopolitics.md
└── scripts/               # Workspace-specific scripts
    └── check_balance_alert.sh
```

---

## Templates

Full template files are in `docs/workspace-templates/`:
- `SOUL.md.template`
- `USER.md.template`

Customize them to match your preferences and working style.

---

**Remember:** The workspace is YOUR agent's memory. Keep it private, back it up, and maintain it regularly.
