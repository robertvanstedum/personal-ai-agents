# Phase 3 — CI/CD Pipeline
*Created: 2026-06-20 — Claude.ai*
*Updated: 2026-06-20 — reflects staging/production architecture decisions*
*Status: Pre-spec — review before building*
*Location: docs/PHASE3_CICD_PLAN.md*
*Depends on: Production Cutover Plan Phases A-C complete*

---

## Architecture (locked)

```
Production:  app.minimoi.ai   → EC2 (main branch)
Staging:     app.mini-moi.ai  → Mac tunnel (staging/dev branch)
Local:       localhost         → Mac (dev branch)
```

CI/CD scope: **production only** (EC2). Staging runs on Mac tunnel —
no automated deploy needed there. Robert is the sole developer and
user acceptance tester. Staging = Mac. Production = EC2.

---

## Branch Model

| Branch | Purpose | Deploys via |
|--------|---------|------------|
| main | Production — EC2 | GitHub Actions (automatic) |
| staging | Staging — Mac tunnel | Manual git pull on Mac |
| dev | Local development | Manual on Mac |
| hotfix/* | Emergency fixes | Branch from main, PR to main |

**Promotion flow:**
```
dev (Mac) → PR → staging → test 1-2 weeks → PR → main → auto-deploys to EC2
```

---

## What Phase 3 Builds (MVP scope)

| Item | Included | Notes |
|------|---------|-------|
| GitHub Actions workflow | Yes | Core deliverable |
| Build Docker images (linux/amd64) | Yes | All three apps |
| Push to ECR on merge to main | Yes | Tagged with git SHA + latest |
| Auto-deploy to EC2 | Yes | docker-compose pull + up |
| Health check after deploy | Yes | Verify all three apps respond |
| Basic smoke tests (8 checks) | Yes | Lightweight — no real credentials |
| Telegram notification | Yes | Success + failure |
| Manual trigger | Yes | Emergency deploys, rollback |
| /health endpoint on all three apps | Yes | Required for smoke tests |
| Mac startup script (one launchd entry) | Yes | Minimal Mac tie-in |
| Staging auto-deploy | No | Manual on Mac is sufficient |
| Automated rollback | No | Manual via SHA for now |
| Branch protection rules | No | Add after pipeline stable |
| Post-deploy smoke tests on EC2 | No | Phase 4 |
| ECR lifecycle policy | No | Nice to have, not blocking |

---

## GitHub Actions Workflow

**File:** `.github/workflows/deploy.yml`

```yaml
name: Test, Build, Deploy

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:
    inputs:
      image_tag:
        description: 'Image tag to deploy (SHA or latest)'
        default: 'latest'

env:
  AWS_REGION: us-east-1
  ECR_REGISTRY: 332704997792.dkr.ecr.us-east-1.amazonaws.com

jobs:

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt pytest pytest-html
      - name: Run smoke tests
        run: pytest tests/ -v --html=reports/test-report.html
      - name: Upload test report
        uses: actions/upload-artifact@v4
        with:
          name: test-report
          path: reports/test-report.html
        if: always()

  build-push:
    needs: test
    if: github.ref == 'refs/heads/main' && github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    outputs:
      image_tag: ${{ steps.tag.outputs.sha }}
    steps:
      - uses: actions/checkout@v4
      - name: Set image tag
        id: tag
        run: echo "sha=${GITHUB_SHA::7}" >> $GITHUB_OUTPUT
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ${{ env.AWS_REGION }}
      - name: Login to ECR
        uses: aws-actions/amazon-ecr-login@v2
      - name: Build and push images
        run: |
          SHA=${{ steps.tag.outputs.sha }}
          for app in portal curator german; do
            docker build -t $ECR_REGISTRY/minimoi/$app:$SHA \
                         -t $ECR_REGISTRY/minimoi/$app:latest \
                         ./$app
            docker push $ECR_REGISTRY/minimoi/$app:$SHA
            docker push $ECR_REGISTRY/minimoi/$app:latest
          done

  deploy:
    needs: build-push
    if: github.ref == 'refs/heads/main' && github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ${{ env.AWS_REGION }}
      - name: Deploy to EC2
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.EC2_HOST }}
          username: ec2-user
          key: ${{ secrets.EC2_SSH_KEY }}
          script: |
            cd /opt/minimoi
            # Save current state for rollback
            docker ps --format '{{.Image}}' > /opt/minimoi/.last_deploy
            # Pull new images
            aws ecr get-login-password --region us-east-1 | \
              docker login --username AWS \
              --password-stdin 332704997792.dkr.ecr.us-east-1.amazonaws.com
            docker-compose pull
            docker-compose up -d
            # Health check
            sleep 30
            curl -f http://localhost:5001/health || exit 1
            curl -f http://localhost:8766/health || exit 1
            curl -f http://localhost:8767/health || exit 1

  notify:
    needs: [test, build-push, deploy]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Send Telegram notification
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          STATUS: ${{ needs.deploy.result }}
          SHA: ${{ github.sha }}
        run: |
          pip install requests -q
          python3 scripts/notify_deploy.py \
            --status $STATUS \
            --sha ${SHA:0:7} \
            --branch ${{ github.ref_name }}
```

---

## GitHub Secrets Required

Add in GitHub → repo → Settings → Secrets → Actions:

| Secret | Value |
|--------|-------|
| AWS_ACCESS_KEY_ID | minimoi-deploy IAM key |
| AWS_SECRET_ACCESS_KEY | minimoi-deploy IAM secret |
| EC2_HOST | 100.57.23.192 |
| EC2_SSH_KEY | SSH private key for EC2 access |
| TELEGRAM_BOT_TOKEN | CoS bot token |
| TELEGRAM_CHAT_ID | Your Telegram chat ID |

**Note on EC2_SSH_KEY:** Phase 2 used EC2 Instance Connect (no key
pair). GitHub Actions needs SSH access. Two options:
- Generate a key pair, add public key to EC2 authorized_keys,
  store private key in GitHub Secrets
- Use AWS SSM Session Manager (no key needed, IAM auth only)

Claude Code to decide and implement during build. SSM preferred
for consistency with the no-key-pair approach from Phase 2.

---

## Smoke Tests (8 tests, ~30 seconds)

**File:** `tests/`

```python
# tests/test_portal.py
def test_health(client):
    assert client.get('/health').status_code == 200

def test_unauthenticated_redirect(client):
    r = client.get('/guild')
    assert r.status_code == 302  # redirects to login, not 500

def test_login_page_loads(client):
    assert client.get('/login').status_code == 200

# tests/test_curator.py
def test_curator_health(curator_client):
    assert curator_client.get('/health').status_code == 200

def test_curator_loads(curator_client):
    assert curator_client.get('/').status_code in [200, 302]

# tests/test_german.py
def test_german_health(german_client):
    assert german_client.get('/health').status_code == 200

def test_gesprache_loads(german_client):
    assert german_client.get('/gesprache').status_code in [200, 302]

def test_personas_load(german_client):
    r = german_client.get('/api/personas')
    assert r.status_code == 200
    assert len(r.get_json()) > 0
```

No real credentials needed — tests use Flask test client, mock
external calls. Safe to run in CI without SSM access.

---

## Health Endpoints (Claude Code adds to all three apps)

```python
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'app': 'portal'}), 200
```

One route per app. Required for deploy health check and smoke tests.

---

## Telegram Notification Script

**File:** `scripts/notify_deploy.py`

```python
import argparse
import os
import requests

def notify(status, sha, branch):
    emoji = '✅' if status == 'success' else '❌'
    message = (
        f"{emoji} mini-moi deploy {status}\n"
        f"Branch: {branch} | Commit: {sha}\n"
        f"https://github.com/robertvanstedum/personal-ai-agents/commit/{sha}"
    )
    requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage",
        json={'chat_id': os.environ['TELEGRAM_CHAT_ID'], 'text': message}
    )

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--status')
    parser.add_argument('--sha')
    parser.add_argument('--branch')
    args = parser.parse_args()
    notify(args.status, args.sha, args.branch)
```

---

## Rollback (manual, via SHA)

Every image tagged with git SHA. To roll back:

**Option A — Manual trigger in GitHub Actions UI:**
Actions → deploy.yml → Run workflow → enter previous SHA as image_tag

**Option B — Emergency on EC2:**
```bash
# SSH to EC2
cat /opt/minimoi/.last_deploy  # shows previous image tags
# Edit docker-compose.yml to pin previous tag
docker-compose up -d
```

---

## Mac Startup Script (one launchd entry)

**File:** `scripts/start_minimoi.sh`

```bash
#!/bin/bash
cd ~/Projects/personal-ai-agents
git checkout staging
git pull origin staging
docker-compose -f docker-compose.dev.yml up -d
echo "mini-moi staging started on $(date)"
```

One launchd plist calls this script. Everything else in the repo.

---

## Commit Sequence (four commits)

```
1. Add /health endpoints to all three Flask apps
2. Add pytest smoke test suite + conftest.py
3. Add GitHub Actions workflow (deploy.yml)
4. Add Telegram notify script + Mac startup script
```

---

## Definition of Done

- [ ] /health on all three apps — returns 200
- [ ] 8 smoke tests pass locally
- [ ] .github/workflows/deploy.yml committed
- [ ] All 6 GitHub Secrets configured
- [ ] Push to main triggers pipeline end to end
- [ ] PR to main runs tests only — no deploy
- [ ] Push to staging/dev does NOT trigger pipeline
- [ ] Test report artifact viewable in Actions UI
- [ ] Telegram notification received on successful deploy
- [ ] Manual trigger deploys successfully
- [ ] Rollback tested — previous SHA deployed via manual trigger
- [ ] Mac startup script replaces individual launchd entries

---

## Thinking Section — Where This Leads

### Immediate next steps after Phase 3

- **Staging auto-deploy:** Add a second GitHub Actions job that
  deploys `staging` branch to a staging EC2 environment when
  mini-moi grows beyond single-developer. Not needed now.
- **Branch protection:** Require PR review before merge to main.
  Currently Robert is reviewer and developer — one person,
  so this adds friction without value. Add when there are
  collaborators.
- **Test expansion:** Start with 8 smoke tests. Add one test
  per bug fixed going forward. The test suite grows organically
  from real failures, not from trying to cover everything upfront.

### The enterprise pattern this maps to

```
Mini-moi              Enterprise equivalent
─────────────────     ──────────────────────────
GitHub Actions        Jenkins / CircleCI / Buildkite
ECR                   Artifactory / Google Container Registry
EC2 + docker-compose  ECS / Kubernetes / Nomad
pytest + pytest-html  JUnit / TestRail / Allure
Telegram alerts       PagerDuty / OpsGenie / Slack
SHA-tagged images     GitOps / Argo Rollouts
SSM Parameter Store   HashiCorp Vault / AWS Secrets Manager
```

The pattern is identical at every scale. The tools differ.
Understanding the pattern is what transfers to any environment.

### The AI-enhanced pipeline story

What makes mini-moi's CI/CD distinctive is the AI layer:
- Pipeline designed by Claude.ai
- Built by Claude Code
- Reviewed by Grok
- Operated by one human

Future additions that would strengthen this story:
- Claude reviews diffs before merge to main
- Failure notifications include AI-generated diagnosis
- Test cases suggested by Claude when new routes are added
- Deployment risk scoring before merge

This is "AI-enhanced DevOps" — not AI replacing engineers,
but AI making a single operator as effective as a team.

---

*Phase 3 CI/CD Plan · 2026-06-20 · Claude.ai*
*Updated to reflect: staging on Mac tunnel, two-domain setup,*
*narrowed MVP scope, single-developer model*
*Related: docs/PRODUCTION_CUTOVER_PLAN.md, docs/AWS_MIGRATION_PLAN.md*
