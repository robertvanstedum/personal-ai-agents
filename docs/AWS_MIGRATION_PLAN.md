# mini-moi AWS Migration Plan
*Created: 2026-06-18 — Claude.ai*
*Status: Pre-spec — review before building*
*Location: docs/AWS_MIGRATION_PLAN.md*
*Replaces: Mac Mini migration (deferred indefinitely)*

---

## Why AWS

**Three honest reasons, in order of importance:**

**1. Avoid the Mac Mini capital cost.**
The original plan was to migrate from MacBook to a dedicated Mac Mini
for always-on production. A Mac Mini with enough RAM to run local
models costs ~$2,000 upfront, requires physical setup, and locks
mini-moi into Apple hardware. AWS achieves
the same goal — always-on production, no tunnel dependency — for
$5-10/month in the first year on free tier, then ~$36/month after.
No capital expenditure, no hardware to manage, no single point of
failure sitting on a desk. The Mac Mini purchase is deferred
indefinitely.

**2. Build real AWS skills through actual use.**
Reading about AWS and building on AWS are different things. mini-moi
is a production system in daily use — the right environment to learn
infrastructure skills that actually stick. Containerization, CI/CD
pipelines, IAM, RDS, S3, CloudWatch — these are learned by doing, not
by following a tutorial. The migration is the learning experience.
The goal is to be able to speak to every architectural decision from
direct experience, not from documentation.

**3. mini-moi is designed for enterprise applicability.**
The long-term vision for mini-moi is not a personal tool that stays
personal. The patterns — agent coordination, institutional memory,
domain-specific AI, structured decision capture — are applicable in
any organization. An enterprise deployment means cloud infrastructure,
a CI/CD pipeline, proper IAM, monitored operations. Building on AWS
now means the architecture is already enterprise-ready when that
conversation happens. A DevOps pipeline with automated testing and
deployment is not a nice-to-have in that context — it's table stakes.

The Mac stays as the dev environment. Everything built and tested
locally first, deployed to AWS on green tests. That's the right
development discipline and it reflects how a professional engineering
team would run this.

**Replaces:** Mac Mini purchase for production hosting. Same operational
goal, no capital expenditure, better long-term architecture.

**On the Mac Mini for local models:** A Mac Mini with sufficient RAM
to run serious local models (Ollama at 30B-70B quantized) costs ~$2,000.
This purchase is now deferred indefinitely — a GPU EC2 instance achieves
the same capability with no upfront cost.

**Two-instance AWS architecture:**

| Instance | Purpose | Cost |
|----------|---------|------|
| t3.small (always on) | Web apps — Portal, Curator, Mein Deutsch | ~$17/month |
| g4dn.xlarge spot (on demand) | Local LLM — Ollama, RAG, LoRA | ~$0.16/hr spot |

The g4dn.xlarge has a T4 GPU with 16GB VRAM — runs 13B-30B quantized
models well. Spot pricing at ~$0.16/hour means 4 hours/day costs ~$19/month,
8 hours/day ~$38/month. Total AWS budget comfortably under $60/month.

The GPU instance runs on demand — start it for design sessions, German
practice with the local model, RAG queries, LoRA training runs. Stop it
when done. No idle cost, no hardware to manage, no $2,000 upfront.

This also means the Learning System roadmap (Phase 1 RAG, Phase 2 LoRA)
no longer depends on a Mac Mini purchase. The GPU instance is the local
LLM infrastructure. Better model quality than a Mac Mini entry model,
more flexible, enterprise-appropriate architecture.

The spot instance pattern is itself a useful AWS skill — using spot
pricing for interruptible workloads at 70% discount vs. on-demand,
with graceful handling of spot reclamation.

---

## Current stack inventory

Three Flask applications:
- **Portal** (port 5001) — auth, routing, Guild dashboard, CoS
- **Curator** (port 8766) — briefing, scoring pipeline, reading room
- **Mein Deutsch** (port 8767) — German domain, Gespräche, Lesen,
  Schreiben, Wörter

Supporting infrastructure:
- Postgres — installed locally, not yet primary data store
- Neo4j — installed, not yet active
- Cloudflare tunnel — minimoi.ai → portal (to be replaced)
- Cloudflare Pages — minimoi.ai static (stays, no change)
- Scheduler / launchd — Curator scoring loops, career scout
- Local files: `cos_context.json`, persona `.txt` files,
  `_working/` directory, session data, `curator_preferences.json`
- API keys in macOS Keychain

What stays local permanently:
- Ollama / local LLM (needs local hardware)
- Dev environment (MacBook)
- OpenClaw memory files (can sync to S3 but local is primary)

---

## Target architecture

```
Developer (MacBook)
    ↓ git push
GitHub
    ↓ GitHub Actions (CI/CD)
    ↓ tests pass
AWS EC2 (t3.small)
    ├── Portal Flask app (Docker container)
    ├── Curator Flask app (Docker container)
    ├── Mein Deutsch Flask app (Docker container)
    └── Nginx (reverse proxy, SSL termination)
         ↕
AWS RDS (Postgres, t3.micro)
         ↕
AWS S3 (static assets, config files, session data)
         ↕
Cloudflare (DNS → EC2 public IP)
```

Cloudflare Pages remains for `minimoi.ai` static.
`app.minimoi.ai` DNS record updated to point to EC2 instead of tunnel.

---

## Phase 0 — Containerization (local, no AWS yet)

**Goal:** Every app runs in Docker locally. No local file path
dependencies. Dev environment confirmed working before touching AWS.

**Why first:** Containerization forces every dependency to be explicit.
A Docker container cannot read from `~/Projects/personal-ai-agents/`
— you must solve the file dependency problem. Solving it locally means
the cloud deployment is clean from day one. This is the hardest
conceptual step; everything after it is mechanical.

### 0.1 — Dockerfile per app

Write a `Dockerfile` for each Flask app:

```dockerfile
# Example — Portal
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV FLASK_ENV=production
EXPOSE 5001
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "app:app"]
```

Key decisions:
- Use `gunicorn` not Flask dev server in production
- `python:3.11-slim` base image — smaller, faster builds
- One Dockerfile per app — they're separate services
- Build context is each app's directory, not the repo root

### 0.2 — Docker Compose for local dev

`docker-compose.yml` at repo root runs all three apps together:

```yaml
version: '3.8'
services:
  portal:
    build: ./minimoi_portal
    ports:
      - "5001:5001"
    env_file: .env
    volumes:
      - ./data:/app/data  # shared data directory

  curator:
    build: ./curator
    ports:
      - "8766:8766"
    env_file: .env
    volumes:
      - ./data:/app/data

  mein_deutsch:
    build: ./mein_deutsch
    ports:
      - "8767:8767"
    env_file: .env
    volumes:
      - ./data:/app/data

  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: minimoi
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

### 0.3 — Solve the local file dependency problem

This is the key work in Phase 0. Every local file path must become
a configurable path driven by environment variables:

**Files to migrate from hardcoded paths to env-var paths:**
- `cos_context.json` → `DATA_DIR/cos_context.json`
- `curator_preferences.json` → `DATA_DIR/curator_preferences.json`
- Persona `.txt` files → `DATA_DIR/personas/`
- `_working/` directory → `DATA_DIR/working/`
- Session data → `DATA_DIR/sessions/`

`DATA_DIR` is an environment variable. In local dev it points to
`./data/`. In production it will point to an S3-mounted directory
or a persistent EBS volume.

**Audit step:** Claude Code reads every file path reference in all
three apps and flags any that aren't driven by environment variables.
No hardcoded `/Users/vanstedum/` or `~/Projects/` paths anywhere.

### 0.4 — Environment variable management

All secrets and config move to a `.env` file (not committed):
- API keys (OpenAI, xAI/Grok, Anthropic)
- Database credentials
- Flask secret key
- `DATA_DIR` path
- Any service URLs

`.env.example` committed to repo with all keys present but no values.
This is the documented interface for "what does this app need to run."

On production: environment variables injected at runtime — no `.env`
file on the server.

### 0.5 — Confirm local Docker works

Before moving to AWS: `docker-compose up` starts all three apps,
all routes work, Postgres connects, all features functional.
This is the Phase 0 acceptance test.

**Definition of Done — Phase 0:**
- All three apps have Dockerfiles
- `docker-compose up` starts the full stack locally
- No hardcoded local file paths anywhere in the codebase
- All config driven by environment variables
- `.env.example` committed with documented keys
- Postgres running in Docker, apps connecting to it
- All existing features confirmed working in Docker before Phase 1

---

## Phase 1 — AWS Foundation

**Goal:** AWS account properly configured, networking ready,
one-time setup complete.

### 1.1 — AWS account setup

If not already done:
- Enable MFA on root account immediately
- Create an IAM user for deployment (not root)
- Create an IAM role for the EC2 instance
- Enable AWS Cost Alerts at $30/month threshold

### 1.2 — IAM roles and policies

Three IAM entities:

**Deployment user** (used by GitHub Actions):
- EC2: StartInstances, StopInstances, DescribeInstances
- ECR: GetAuthorizationToken, BatchCheckLayerAvailability,
  PutImage, InitiateLayerUpload, UploadLayerPart, CompleteLayerUpload
- S3: GetObject, PutObject, DeleteObject on the mini-moi bucket

**EC2 instance role:**
- S3: GetObject, PutObject on the mini-moi bucket
- Secrets Manager: GetSecretValue for the mini-moi secret
- CloudWatch: PutMetricData, CreateLogGroup, PutLogEvents

**Never:** root access in GitHub Actions or on EC2.

### 1.3 — VPC and networking

Use the default VPC to start — simpler, no custom networking needed
at this scale. One public subnet is sufficient.

Security group for EC2:
- Inbound: 80 (HTTP), 443 (HTTPS), 22 (SSH from your IP only)
- Outbound: all (apps need to call external APIs)

### 1.4 — ECR (Elastic Container Registry)

Three ECR repositories — one per app:
- `minimoi/portal`
- `minimoi/curator`
- `minimoi/mein-deutsch`

Images are pushed here by GitHub Actions and pulled by EC2.

### 1.5 — S3 bucket

One S3 bucket: `minimoi-data-[account-id]`

Structure mirrors the local `data/` directory:
```
minimoi-data/
  cos_context.json
  curator_preferences.json
  personas/
    maria.txt
    frau_berger.txt
    ...
  working/
    ROADMAP.md
    NEAR_TERM_PLAN.md
    ...
  sessions/
```

Versioning enabled — accidental overwrites recoverable.
No public access — EC2 instance role reads/writes via IAM.

**Definition of Done — Phase 1:**
- IAM user, roles, and policies created and documented
- VPC security group configured
- ECR repositories created
- S3 bucket created with correct structure
- Cost alert set at $30/month
- All IAM permissions tested (least privilege confirmed)

---

## Phase 2 — First Cloud Deployment

**Goal:** All three apps running on EC2, accessible at
`app.minimoi.ai`, no Cloudflare tunnel.

### 2.1 — Launch EC2 instance

Instance: `t3.small` (2 vCPU, 2GB RAM)
- Enough for three Flask apps under light load
- Free tier eligible for t3.micro but 1GB RAM is tight with three
  apps + Postgres; start with t3.small
- AMI: Amazon Linux 2023 (well-documented, AWS-maintained)
- Storage: 20GB gp3 EBS (fast, cheap)
- Attach the EC2 instance role from Phase 1

### 2.2 — EC2 bootstrap

Install on first login:
```bash
# Docker
sudo yum install -y docker
sudo service docker start
sudo usermod -a -G docker ec2-user

# Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/\
download/docker-compose-$(uname -s)-$(uname -m)" \
-o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# AWS CLI (pre-installed on Amazon Linux 2023)
aws configure  # use instance role, not access keys
```

### 2.3 — Nginx as reverse proxy

Nginx runs on EC2 (not in Docker) as the front door:
- Listens on 80 and 443
- SSL via Let's Encrypt (Certbot)
- Routes:
  - `app.minimoi.ai/` → Portal (5001)
  - `app.minimoi.ai/german/` → Mein Deutsch (8767)
  - `app.minimoi.ai/curator/` → Curator (8766)

```nginx
server {
    server_name app.minimoi.ai;

    location /german/ {
        proxy_pass http://localhost:8767;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /curator/ {
        proxy_pass http://localhost:8766;
    }

    location / {
        proxy_pass http://localhost:5001;
    }

    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/app.minimoi.ai/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.minimoi.ai/privkey.pem;
}
```

### 2.4 — First manual deployment

Before CI/CD: deploy manually to confirm the stack works.

```bash
# On EC2
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS \
  --password-stdin [account-id].dkr.ecr.us-east-1.amazonaws.com

docker-compose -f docker-compose.prod.yml up -d
```

`docker-compose.prod.yml` differs from local:
- No volume mounts to local paths
- S3 sync for data files (or EBS mount)
- Production environment variables from AWS Secrets Manager

### 2.5 — DNS cutover

Update Cloudflare DNS:
- `app.minimoi.ai` CNAME → EC2 public DNS (or A record → Elastic IP)
- Remove the old tunnel CNAME record
- Set Elastic IP on EC2 so the IP doesn't change on restart

Test: `https://app.minimoi.ai` loads correctly, all three apps
respond, auth works, Gespräche works.

**Definition of Done — Phase 2:**
- All three apps running on EC2
- `app.minimoi.ai` resolves to EC2, not the tunnel
- SSL working via Let's Encrypt
- All existing features confirmed working
- Cloudflare tunnel retired
- Elastic IP assigned so DNS doesn't break on restart

---

## Phase 3 — CI/CD Pipeline

**Goal:** Push to main → tests run → deploy to EC2 automatically.
This is the DevOps interview story.

### 3.1 — GitHub Actions workflow

`.github/workflows/deploy.yml`:

```yaml
name: Test and Deploy

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt pytest

      - name: Run tests
        run: pytest tests/ -v

  deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-east-1

      - name: Login to ECR
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push images
        run: |
          docker build -t minimoi/portal ./minimoi_portal
          docker tag minimoi/portal:latest \
            $ECR_REGISTRY/minimoi/portal:latest
          docker push $ECR_REGISTRY/minimoi/portal:latest
          # repeat for curator and mein_deutsch

      - name: Deploy to EC2
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.EC2_HOST }}
          username: ec2-user
          key: ${{ secrets.EC2_SSH_KEY }}
          script: |
            cd /app
            aws ecr get-login-password | docker login ...
            docker-compose -f docker-compose.prod.yml pull
            docker-compose -f docker-compose.prod.yml up -d
            docker system prune -f
```

### 3.2 — Test suite (written by Claude Code)

Minimum viable test coverage for CI:

**Smoke tests** — does everything start:
```python
def test_portal_health():
    response = client.get('/health')
    assert response.status_code == 200

def test_auth_required():
    response = client.get('/guild')
    assert response.status_code == 302  # redirects to login
```

**API tests** — critical Gespräche routes:
```python
def test_analyse_route_accepts_transcript():
    response = client.post('/api/gesprache/analyse',
        json={'transcript': 'Maria: Guten Tag.\nSie: Hallo.'})
    assert response.status_code == 200
    assert 'feedback' in response.json
```

**Mobile regression** — Playwright at 390px:
```python
async def test_gesprache_buttons_visible_mobile(page):
    await page.set_viewport_size({'width': 390, 'height': 844})
    await page.goto('https://app.minimoi.ai/german/gesprache')
    button = page.locator('button:has-text("Im Browser")')
    await expect(button).to_be_visible()
```

### 3.3 — GitHub Secrets

Required secrets in GitHub repo settings:
- `AWS_ACCESS_KEY_ID` — deployment IAM user
- `AWS_SECRET_ACCESS_KEY` — deployment IAM user
- `EC2_HOST` — Elastic IP of EC2 instance
- `EC2_SSH_KEY` — private key for SSH to EC2
- `ECR_REGISTRY` — ECR registry URL

**Definition of Done — Phase 3:**
- Push to main triggers the workflow
- Tests run before deploy — failed tests block deployment
- Successful tests → images built, pushed to ECR, EC2 pulls and
  restarts containers
- Zero-downtime deploy (docker-compose rolling update or brief restart)
- All secrets in GitHub Secrets, none in code

---

## Phase 4 — Data Layer (RDS + S3)

**Goal:** Postgres on RDS, all file dependencies on S3.
The app no longer depends on anything on the EC2 filesystem.

### 4.1 — RDS Postgres

Launch `db.t3.micro` RDS instance:
- Postgres 15
- Single AZ (Multi-AZ adds cost, not needed at this scale)
- Storage: 20GB gp2, auto-scaling enabled
- VPC: same as EC2, private subnet (EC2 accesses via private IP)
- Security group: allow inbound 5432 from EC2 security group only
- Automated backups: 7-day retention

Update connection strings in all apps to use RDS endpoint.
Run migrations. Confirm data integrity.

**Why not Postgres on EC2:** RDS handles backups, patching, and
failover automatically. At $15/month it's worth it for a system
in daily use. Also stronger interview story.

### 4.2 — S3 for file storage

Replace local file reads/writes with S3:

```python
import boto3

s3 = boto3.client('s3')

def read_cos_context():
    obj = s3.get_object(
        Bucket='minimoi-data',
        Key='cos_context.json'
    )
    return json.loads(obj['Body'].read())

def write_cos_context(data):
    s3.put_object(
        Bucket='minimoi-data',
        Key='cos_context.json',
        Body=json.dumps(data, indent=2)
    )
```

Wrapper functions replace direct `open()` calls throughout the
codebase. Claude Code audits every file read/write and replaces
with S3 equivalents.

**Files that move to S3:**
- `cos_context.json`
- `curator_preferences.json`
- `keyword_map.json`
- Persona `.txt` files
- `_working/` directory contents
- Session transcripts and analysis outputs
- Curator article cache

**Files that stay local (dev only):**
- `.env`
- `docker-compose.yml`
- Source code (obviously)

### 4.3 — AWS Secrets Manager

Move all API keys from `.env` to AWS Secrets Manager:

```python
def get_secret(secret_name):
    client = boto3.client('secretsmanager', region_name='us-east-1')
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response['SecretString'])

secrets = get_secret('minimoi/production')
OPENAI_API_KEY = secrets['openai_api_key']
GROK_API_KEY = secrets['grok_api_key']
```

One secret named `minimoi/production` containing all keys as JSON.
Rotation supported if needed later.

**Definition of Done — Phase 4:**
- Postgres on RDS, all apps connecting via RDS endpoint
- All file operations going through S3
- All API keys in Secrets Manager
- EC2 instance has no sensitive data on disk
- EC2 can be terminated and relaunched with no data loss

---

## Phase 5 — Hardening and Monitoring

**Goal:** Production-grade reliability. CloudWatch monitoring,
alerting, proper IAM, cost controls.

### 5.1 — CloudWatch

Log groups for each app:
- `/minimoi/portal`
- `/minimoi/curator`
- `/minimoi/mein-deutsch`

Key metrics to track:
- HTTP 5xx error rate
- Response time (p50, p95)
- Memory and CPU on EC2
- S3 access errors
- RDS connection count

Alarms:
- CPU > 80% for 5 minutes → SNS notification → Telegram
- 5xx rate > 5% → SNS notification → Telegram
- RDS storage < 20% → SNS notification

### 5.2 — Cost monitoring

AWS Cost Explorer configured. Monthly budget alert at $35.
Break down by service to track actual vs. expected.

### 5.3 — Backup verification

Monthly check:
- RDS automated backup restores correctly (test restore to new instance)
- S3 versioning confirmed working (check a versioned file)
- EC2 AMI snapshot (quarterly)

### 5.4 — Security review

- All security groups reviewed — no overly permissive rules
- IAM policies reviewed — least privilege confirmed
- SSL certificate auto-renewal working (Certbot cron)
- No API keys in code, logs, or environment variables on EC2

**Definition of Done — Phase 5:**
- CloudWatch dashboards live for all three apps
- Alerts configured and tested (trigger a test 500, confirm Telegram
  notification received)
- Cost within expected range
- Backup restore tested

---

## Cost estimate

| Service | Monthly (after free tier) |
|---------|--------------------------|
| EC2 t3.small (always on) | ~$17 |
| g4dn.xlarge spot (4-8hrs/day) | ~$19-38 |
| RDS db.t3.micro | ~$15 |
| S3 (minimal data) | ~$1 |
| CloudWatch | ~$2 |
| Elastic IP | ~$0 (free when attached) |
| Data transfer | ~$1 |
| **Total** | **~$55-74/month** |

**Within the $60/month budget** at moderate GPU usage (4-6 hrs/day).
Spot pricing keeps the GPU instance cost flexible — use more on
active development days, less on quieter days.

Free tier (first 12 months): EC2 t3.micro free, RDS free,
S3 5GB free. Actual cost year 1 (web apps only, GPU on demand):
**~$20-40/month** depending on GPU usage.

**vs. Mac Mini:** $2,000 upfront + electricity vs. $55-74/month
with better model quality, no hardware maintenance, and enterprise-
appropriate cloud infrastructure. Break-even never makes sense.

---

## The interview story

*"I containerized a three-app Flask stack, set up a CI/CD pipeline
with GitHub Actions that runs automated tests before every deployment,
and migrated from a local dev setup to AWS — EC2 for compute, RDS for
Postgres, S3 for file storage, Secrets Manager for credentials,
CloudWatch for monitoring. The whole thing deploys automatically on
git push and costs about $35/month. Claude Code did the implementation;
I designed the architecture and made the tradeoff decisions."*

That's a TPM who understands infrastructure, uses AI to accelerate
rather than replace engineering work, and has a live system to point to.

---

## Open questions before speccing

1. **AWS region:** us-east-1 (cheapest, most services) or us-east-2?
   No strong reason to prefer either — recommend us-east-1.

2. **t3.micro vs t3.small for Phase 2:** t3.micro is free tier but
   1GB RAM running three Flask apps + Postgres may be tight.
   Recommendation: start with t3.small ($17/month), downgrade if
   monitoring shows plenty of headroom.

3. **Postgres on EC2 vs RDS for Phase 2:** Running Postgres on EC2
   in Phase 2 (before Phase 4) is simpler and free. Migrate to RDS
   in Phase 4. This means two database locations during the migration
   — manageable but worth noting.

4. **OpenClaw on AWS:** OpenClaw currently runs locally. It manages
   files and memory — if files move to S3, OpenClaw needs to be
   updated to read/write S3. This is Phase 4 work, not Phase 0.

5. **Neo4j:** Not active yet, not in scope for this migration.
   If/when it becomes active, it runs on EC2 (no managed Neo4j
   service needed at this scale).

6. **Curator scheduling:** The Curator scoring loops run on launchd
   locally. On AWS these become either: cron on EC2 (simplest),
   EventBridge + Lambda (more AWS-native), or a persistent process
   in the container. Recommend EC2 cron for Phase 2, revisit later.

---

## Roadmap placement

**Domain:** Platform
**Phase:** AWS Migration (new phase, above Mac Mini)
**Status:** target — pre-spec, review pending
**Source:** `docs/AWS_MIGRATION_PLAN.md`
**Replaces:** Mac Mini migration (deferred)

Phases in order:
- Phase 0: Containerization — start immediately
- Phase 1: AWS Foundation — after Phase 0 confirmed working
- Phase 2: First cloud deployment (t3.small) — after Phase 1
- Phase 3: CI/CD pipeline — after Phase 2 stable
- Phase 4: Data layer (RDS + S3) — after Phase 3
- Phase 5: GPU instance (g4dn.xlarge spot) — local LLM on AWS,
  replaces Mac Mini, enables Learning System Phase 1 (RAG) and
  Phase 2 (LoRA)
- Phase 6: Hardening and monitoring — after Phase 5

---

*AWS Migration Plan · 2026-06-18 · Claude.ai*
*Pre-spec — review with Grok before building*
*Commit to: docs/AWS_MIGRATION_PLAN.md*
