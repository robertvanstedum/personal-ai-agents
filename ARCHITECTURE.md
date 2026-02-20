# ARCHITECTURE.md - System Design & Structure

_Created: 2026-02-06 - Planning phase for integrated AI agent system_

## Vision

**Local Development:** MacBook (current)  
**Production Target:** Mac Mini or cloud server  
**Goal:** Portable, organized, maintainable AI agent infrastructure

---

## System Components

### 1. OpenClaw (Orchestrator)
- **Purpose:** Agent runtime, skills framework, orchestration, messaging
- **Location:** `/opt/homebrew/lib/node_modules/openclaw` (global install)
- **Workspace:** `~/.openclaw/workspace` (your files, skills, memory)
- **Data:** `~/.openclaw/data` (sessions, logs, cache)

### 2. Personal AI Agent (Research & Knowledge)
- **Purpose:** Geopolitics research, knowledge graphs, local LLM
- **Location:** `~/Projects/personal-ai-agents` (current)
- **Git:** Local (to be pushed to GitHub)
- **Services:** Neo4j (context graph), Postgres (structured data), Ollama (local LLM)

### 3. Skills Bridge (Integration Layer)
- **Purpose:** OpenClaw skills that interface with Personal AI Agent
- **Location:** `~/.openclaw/workspace/skills/`
- **Git:** Part of workspace repo

---

## Proposed Directory Structure

```
~/Projects/
├── personal-ai-agents/              # Your Python agent (existing)
│   ├── .git/
│   ├── docker-compose.yml           # Neo4j, Postgres
│   ├── main.py                      # FastAPI server
│   ├── requirements.txt
│   ├── tests/                       # ← NEW: pytest tests
│   ├── .github/                     # ← NEW: CI/CD workflows
│   └── docs/                        # ← NEW: architecture docs
│
└── ai-infrastructure/               # ← NEW: Unified config & deployment
    ├── .git/                        # Separate repo
    ├── docker-compose.yml           # Full stack (including OpenClaw)
    ├── config/
    │   ├── openclaw.yaml           # OpenClaw config
    │   ├── neo4j.env
    │   ├── postgres.env
    │   └── ollama.env
    ├── scripts/
    │   ├── backup.sh               # Backup Neo4j + Postgres
    │   ├── restore.sh
    │   ├── migrate-to-server.sh    # Migration script
    │   └── healthcheck.sh
    ├── volumes/                    # Persistent data (gitignored)
    │   ├── neo4j-data/
    │   ├── postgres-data/
    │   └── openclaw-data/
    └── docs/
        ├── SETUP.md                # Fresh install guide
        ├── MIGRATION.md            # MacBook → Server guide
        └── TROUBLESHOOTING.md

~/.openclaw/
├── workspace/                      # OpenClaw workspace (git tracked)
│   ├── .git/
│   ├── AGENTS.md
│   ├── SOUL.md
│   ├── USER.md
│   ├── MEMORY.md
│   ├── memory/
│   │   └── YYYY-MM-DD.md
│   ├── skills/
│   │   ├── context-graph/          # Interfaces with personal-ai-agents
│   │   │   ├── SKILL.md
│   │   │   ├── query_traces.py
│   │   │   ├── add_trace.py
│   │   │   └── requirements.txt
│   │   ├── geopolitics/            # Research automation
│   │   │   ├── SKILL.md
│   │   │   ├── search_and_analyze.py
│   │   │   └── requirements.txt
│   │   └── postgres-knowledge/     # Postgres interface
│   │       ├── SKILL.md
│   │       └── query_entities.py
│   ├── projects/                   # ← NEW: organized by project
│   │   ├── gmail-cleanup/
│   │   ├── geopolitics-research/
│   │   └── german-learning/
│   └── tests/                      # ← NEW: test OpenClaw skills
│
└── data/                           # OpenClaw runtime (not in git)
    ├── sessions/
    ├── logs/
    └── cache/
```

---

## Git Strategy

### Option A: **Mono-repo** (Single Git Repo)
```
~/Projects/ai-system/
├── openclaw-workspace/     # Your OpenClaw files
├── personal-agent/         # Your Python agent
├── infrastructure/         # Docker, config, scripts
└── docs/                   # Shared docs
```

**Pros:** Single source of truth, easier to sync  
**Cons:** Large repo, mixed concerns

### Option B: **Multi-repo** (Current + New)
```
1. personal-ai-agents       (existing Python agent)
2. openclaw-workspace       (new: your workspace files)
3. ai-infrastructure        (new: deployment config)
```

**Pros:** Separation of concerns, smaller repos  
**Cons:** Need to keep in sync, more complex

### Option C: **Hybrid** (Recommended)
```
1. personal-ai-agents       (Python agent only)
2. ai-infrastructure        (workspace + deployment + config)
   ├── openclaw-workspace/  (symlinked to ~/.openclaw/workspace)
   ├── docker/
   ├── config/
   └── scripts/
```

**Pros:** Clean separation, single deployment repo  
**Cons:** Symlinks need care during migration

**My Recommendation:** **Option C** - Keep Python agent separate, everything else in `ai-infrastructure`

---

## Docker Strategy

### Current State
- **personal-ai-agents:** Uses Docker (Neo4j, Postgres)
- **OpenClaw:** Runs natively (npm global install)

### Proposed: **Unified Docker Compose**

**Location:** `~/Projects/ai-infrastructure/docker-compose.yml`

```yaml
version: '3.8'

services:
  # Databases
  neo4j:
    image: neo4j:latest
    container_name: ai-neo4j
    env_file: config/neo4j.env
    ports:
      - "7474:7474"
      - "7687:7687"
    volumes:
      - ./volumes/neo4j-data:/data
    restart: unless-stopped

  postgres:
    image: postgres:latest
    container_name: ai-postgres
    env_file: config/postgres.env
    ports:
      - "5432:5432"
    volumes:
      - ./volumes/postgres-data:/var/lib/postgresql/data
    restart: unless-stopped

  # Local LLM
  ollama:
    image: ollama/ollama:latest
    container_name: ai-ollama
    ports:
      - "11434:11434"
    volumes:
      - ./volumes/ollama-models:/root/.ollama
    restart: unless-stopped

  # Your Python Agent
  personal-agent:
    build:
      context: ../personal-ai-agents
      dockerfile: Dockerfile
    container_name: ai-personal-agent
    ports:
      - "8000:8000"
    depends_on:
      - neo4j
      - postgres
      - ollama
    environment:
      - NEO4J_URI=bolt://neo4j:7687
      - POSTGRES_HOST=postgres
      - OLLAMA_HOST=http://ollama:11434
    restart: unless-stopped

  # OpenClaw (optional - can run natively)
  # openclaw:
  #   image: openclaw/openclaw:latest  # if official image exists
  #   container_name: ai-openclaw
  #   volumes:
  #     - ./openclaw-workspace:/workspace
  #     - ./volumes/openclaw-data:/data
  #   restart: unless-stopped
```

### Docker for OpenClaw?

**Pros:**
- ✅ Portable (same environment everywhere)
- ✅ Isolated dependencies
- ✅ Easy backup (just volumes)
- ✅ Clean migration to server

**Cons:**
- ⚠️ OpenClaw might not have official Docker image
- ⚠️ More complexity for development
- ⚠️ Node modules can be large

**Decision Point:** Let's check if OpenClaw supports Docker or if native is better.

---

## Data Persistence Strategy

### What Needs Backup

1. **Neo4j** - Your decision traces (context graph)
2. **Postgres** - Structured knowledge (entities, events)
3. **Ollama** - Downloaded models (large, can re-download)
4. **OpenClaw workspace** - Git tracked (MEMORY.md, skills, etc.)
5. **OpenClaw data** - Sessions, logs (ephemeral, can recreate)

### Backup Script

**Location:** `~/Projects/ai-infrastructure/scripts/backup.sh`

```bash
#!/bin/bash
# Backup all data to timestamped archive

DATE=$(date +%Y-%m-%d-%H%M%S)
BACKUP_DIR="$HOME/Backups/ai-system/$DATE"

mkdir -p "$BACKUP_DIR"

# Backup databases
docker exec ai-neo4j neo4j-admin dump --to=/data/backup.dump
docker cp ai-neo4j:/data/backup.dump "$BACKUP_DIR/neo4j.dump"

docker exec ai-postgres pg_dump -U postgres personal_agents > "$BACKUP_DIR/postgres.sql"

# Backup OpenClaw workspace (already in git, but snapshot anyway)
cp -r ~/.openclaw/workspace "$BACKUP_DIR/openclaw-workspace"

# Create archive
cd "$HOME/Backups/ai-system"
tar -czf "ai-system-$DATE.tar.gz" "$DATE"
rm -rf "$DATE"

echo "Backup complete: $HOME/Backups/ai-system/ai-system-$DATE.tar.gz"
```

---

## Migration Path: MacBook → Server

### Phase 1: **Preparation** (on MacBook)
1. Push all repos to GitHub
2. Run backup script
3. Document environment variables
4. Test full backup/restore locally

### Phase 2: **Server Setup**
1. Install Docker + Docker Compose
2. Install OpenClaw (npm or Docker)
3. Clone repos
4. Restore volumes from backup

### Phase 3: **Migration Script**

**Location:** `~/Projects/ai-infrastructure/scripts/migrate-to-server.sh`

```bash
#!/bin/bash
# Migrate to new server

SERVER_USER="your_username"
SERVER_HOST="macmini.local"  # or IP address

# 1. Copy infrastructure
rsync -avz ~/Projects/ai-infrastructure/ "$SERVER_USER@$SERVER_HOST:~/ai-infrastructure/"

# 2. Copy personal agent
rsync -avz ~/Projects/personal-ai-agents/ "$SERVER_USER@$SERVER_HOST:~/personal-ai-agents/"

# 3. Copy backup data
rsync -avz ~/Backups/ai-system/latest.tar.gz "$SERVER_USER@$SERVER_HOST:~/backup.tar.gz"

# 4. SSH and restore
ssh "$SERVER_USER@$SERVER_HOST" << 'EOF'
  cd ~/ai-infrastructure
  tar -xzf ~/backup.tar.gz -C volumes/
  docker-compose up -d
  # Setup OpenClaw
  npm install -g openclaw
  ln -s ~/ai-infrastructure/openclaw-workspace ~/.openclaw/workspace
EOF

echo "Migration complete! Verify services on $SERVER_HOST"
```

---

## Testing Strategy

### 1. **Unit Tests** (Python Agent)
```
~/Projects/personal-ai-agents/tests/
├── test_api.py              # FastAPI endpoints
├── test_neo4j.py            # Context graph operations
├── test_ollama.py           # LLM integration
└── test_scheduler.py        # Background jobs
```

### 2. **Integration Tests** (OpenClaw Skills)
```
~/.openclaw/workspace/tests/
├── test_context_graph_skill.py
├── test_geopolitics_skill.py
└── test_postgres_skill.py
```

### 3. **CI/CD** (GitHub Actions)

**File:** `~/Projects/personal-ai-agents/.github/workflows/test.yml`

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      neo4j:
        image: neo4j:latest
        env:
          NEO4J_AUTH: neo4j/testpass
        ports:
          - 7687:7687
      
      postgres:
        image: postgres:latest
        env:
          POSTGRES_PASSWORD: testpass
        ports:
          - 5432:5432
    
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: pip install -r requirements.txt pytest
      
      - name: Run tests
        run: pytest tests/ -v
      
      - name: Create issue on failure
        if: failure()
        uses: actions/github-script@v6
        with:
          script: |
            github.rest.issues.create({
              owner: context.repo.owner,
              repo: context.repo.repo,
              title: 'Test failure on ' + context.sha.substring(0,7),
              body: 'Tests failed. See run: ' + context.serverUrl + '/' + context.repo.owner + '/' + context.repo.repo + '/actions/runs/' + context.runId
            })
```

---

## Development Workflow

### Day-to-Day

1. **Start services:**
   ```bash
   cd ~/Projects/ai-infrastructure
   docker-compose up -d
   ```

2. **Work in OpenClaw:**
   - Chat via web/Telegram/WhatsApp
   - Skills auto-loaded from workspace
   - Memory auto-updated

3. **Develop Python agent:**
   ```bash
   cd ~/Projects/personal-ai-agents
   # Make changes
   pytest tests/
   git commit -m "feat: ..."
   ```

4. **Test integration:**
   - Ask OpenClaw to query context graph
   - Verify Neo4j traces appear
   - Check Postgres for new entities

5. **End of day:**
   ```bash
   # Optional: stop services
   docker-compose down
   
   # Backup if significant changes
   ./scripts/backup.sh
   ```

### Adding New Features

1. **Create skill in workspace**
2. **Write tests**
3. **Update ARCHITECTURE.md** (this file)
4. **Commit to git**
5. **Push to GitHub**

---

## Bug Tracking: GitHub Issues

### Repositories

1. **personal-ai-agents repo:**
   - Issues: Python bugs, API issues, DB schema
   - Labels: `bug`, `enhancement`, `neo4j`, `postgres`, `ollama`
   - Auto-create on test failure (via Actions)

2. **ai-infrastructure repo:**
   - Issues: Deployment, Docker, migration, skills
   - Labels: `bug`, `docker`, `deployment`, `skill`, `openclaw`

### Issue Template

```markdown
## Bug Report

**Component:** [Neo4j / Postgres / Ollama / FastAPI / Skill]
**Severity:** [Low / Medium / High / Critical]

**Description:**
[Clear description]

**Steps to Reproduce:**
1. 
2. 
3. 

**Expected:**
[What should happen]

**Actual:**
[What actually happened]

**Environment:**
- OS: macOS 14.x / Ubuntu 22.04
- Docker: Yes/No
- OpenClaw version: 
- Python version:

**Logs:**
```
[paste relevant logs]
```

**Related:**
- Linked PR: 
- Linked commit:
```

---

## Architecture Decisions (Locked 2026-02-07)

### 1. **Git Strategy** ✅
**Decision:** Mono-repo now, multi-repo later for public portfolio version

**Current:**
- `personal-ai-agents` repo (contains everything)
- Private GitHub repo under personal account

**Future:**
- Split into public-safe repo (sanitized, portfolio-ready)
- Separate private repo for personal data/config
- Timeline: After Phase 2 (geopolitics curator) is working

### 2. **Docker Strategy** ✅
**Decision:** Unified docker-compose for modern container architecture

**Services:**
- Neo4j (context graph)
- Postgres (research artifacts)
- Ollama (local LLM)
- personal-agent (FastAPI server)
- OpenClaw: Run natively for now (development flexibility)

**Rationale:** Containers for databases/services, native for active development tools

### 3. **Server Target** ✅
**Decision:** Mac Mini (next step), keep optionality for cloud migration

**Path:**
1. Development: MacBook (current)
2. Production: Mac Mini (local, always-on)
3. Future option: Cloud VPS if needed (portability maintained)

**Benefits:** Local control, privacy, no recurring cloud costs

### 4. **Naming Convention** ✅
**Decision:** `ai-infrastructure` for deployment repo

**Pattern:**
- Repo: `ai-infrastructure`
- Containers: `ai-neo4j`, `ai-postgres`, `ai-ollama`, `ai-personal-agent`
- Services consistent with `ai-` prefix

**Rationale:** Professional, clear purpose, scales to portfolio use

### 5. **Backup Strategy** ✅
**Decision:** Daily automated backups, 30-day retention, monthly cloud archive

**Implementation:**
- **Frequency:** Daily at 2am (after overnight jobs)
- **Location:** `~/Backups/ai-system/YYYY-MM-DD/`
- **Retention:** Keep 30 days local
- **Archive:** Monthly snapshot to cloud (iCloud/Backblaze)
- **Method:** Cron job running `scripts/backup.sh`

**What gets backed up:**
- Neo4j decision traces (critical)
- Postgres research data (critical)
- OpenClaw workspace (git tracked, but snapshot anyway)
- Ollama models (optional, can re-download)

---

## Open Questions (Future Phases)

### Phase 2+ Considerations
- [ ] Public repo structure (what to include/exclude)
- [ ] Cloud backup provider (iCloud vs Backblaze vs S3)
- [ ] Remote access method for Mac Mini (SSH, Tailscale, VPN)
- [ ] Monitoring/alerting for production (Uptime Kuma, Grafana)

---

## Implementation Plan (Post-Decisions)

### Phase 1: Immediate (This Weekend)
1. ✅ **Review & refine this document together** — DONE
2. ✅ **Make decisions on open questions** — DONE (2026-02-07)
3. ✅ **Push `personal-ai-agents` to GitHub** — DONE
4. ⏳ **Complete Gmail cleanup** — In progress
5. ⏳ **Create unified docker-compose** — Next after Gmail

### Phase 1b: Infrastructure Setup (Next Week)
6. **Create `ai-infrastructure` structure:**
   - Unified docker-compose.yml
   - Backup scripts
   - Migration scripts
7. **Setup automated backups:**
   - Cron job for daily backup
   - Test backup/restore process
8. **Write first test:** Simple pytest for personal-ai-agents API
9. **Build first skill:** `context-graph` skill in OpenClaw workspace

### Phase 2: Geopolitics Curator (Following Week)
- See PROJECT_ROADMAP.md for detailed implementation path

---

## Future Enhancements

### Short Term (1-2 weeks)
- [ ] Unified docker-compose with all services
- [ ] GitHub repos with CI/CD
- [ ] Basic testing for Python agent
- [ ] First OpenClaw skill (context-graph)

### Medium Term (1-2 months)
- [ ] Postgres schema for geopolitics entities
- [ ] Geopolitics research automation (cron + skills)
- [ ] German learning skill (xAI Grok personas)
- [ ] Migration to Mac Mini or server

### Long Term (3-6 months)
- [ ] Semantic search (pgvector + embeddings)
- [ ] Advanced knowledge graph (Neo4j + Postgres hybrid)
- [ ] Multi-agent collaboration (sub-agents for research)
- [ ] Voice interface (ElevenLabs + OpenClaw)

---

_This document is a living architecture. Update as decisions are made and system evolves._
