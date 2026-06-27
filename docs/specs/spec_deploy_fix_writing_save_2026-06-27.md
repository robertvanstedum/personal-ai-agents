# Spec — Deploy Reliability + Escrita/Schreiben Save Fix
*Created: 2026-06-27 — Claude.ai*
*Status: spec_ready — fix in this exact order*
*Applies to: CI/CD pipeline + both language domains*

---

## Summary

Nothing can be validated on production until Bug A is fixed.
Fix deploy reliability first. Everything else depends on it.

---

## Bug A — CI/CD deploy not actually executing (FIRST PRIORITY)

### Symptom
Commits 638183c and 9bf0081 show "success" in GitHub Actions
but EC2 is still running old code. Old templates visible on
minimoi.ai — no Übernehmen/Aceitar button, old hint text.

### Root cause
`.github/workflows/deploy.yml` uses `aws ssm send-command`
which returns success when AWS *accepts* the command — not
when EC2 actually pulls new images and restarts containers.
The `docker-compose pull && up -d` may be failing silently.

### Fix — `.github/workflows/deploy.yml`

Replace the current fire-and-forget SSM call with verified
execution:

```yaml
- name: Deploy on EC2 via SSM
  run: |
    # Send command and capture ID
    COMMAND_ID=$(aws ssm send-command \
      --instance-ids ${{ secrets.EC2_INSTANCE_ID }} \
      --document-name "AWS-RunShellScript" \
      --parameters "commands=[
        'cd /opt/minimoi',
        'mkdir -p /opt/minimoi/data/portuguese',
        'mkdir -p /opt/minimoi/data/german',
        'aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 332704997792.dkr.ecr.us-east-1.amazonaws.com',
        'docker-compose -f docker-compose.prod.yml pull',
        'docker-compose -f docker-compose.prod.yml up -d',
        'sleep 15',
        'curl -sf http://localhost:5001/health || exit 1',
        'curl -sf http://localhost:8770/health || exit 1',
        'curl -sf http://localhost:8767/health || exit 1'
      ]" \
      --query 'Command.CommandId' \
      --output text)

    echo "SSM Command ID: $COMMAND_ID"

    # Wait for actual completion (not just accepted)
    aws ssm wait command-executed \
      --command-id "$COMMAND_ID" \
      --instance-id ${{ secrets.EC2_INSTANCE_ID }}

    # Check real exit status
    STATUS=$(aws ssm get-command-invocation \
      --command-id "$COMMAND_ID" \
      --instance-id ${{ secrets.EC2_INSTANCE_ID }} \
      --query 'Status' \
      --output text)

    echo "Deploy status: $STATUS"
    if [ "$STATUS" != "Success" ]; then
      echo "EC2 deploy FAILED — containers may not have restarted"
      exit 1
    fi

    echo "Deploy confirmed successful on EC2"
```

**Two additions in the deploy command:**
- `mkdir -p /opt/minimoi/data/portuguese` — creates host dir
  for volume mount before docker-compose runs
- `mkdir -p /opt/minimoi/data/german` — same for German

Without these directories existing on EC2, Docker volume mounts
fail silently and data writes go to the ephemeral container
filesystem (wiped on every restart).

---

## Bug B — Portuguese Escrita saves failing

### History of attempts

**Attempt 1:** Original code used `postgresql://` with
`portuguese.writing_sessions` table. The `portuguese` schema
was never created on EC2. `_ensure_writing_sessions_table()`
ran `CREATE TABLE IF NOT EXISTS portuguese.writing_sessions`
but the schema didn't exist — error caught and silently
swallowed. Every save failed silently. No user-facing error.

**Attempt 2 (commit 9bf0081):** Ported to JSON file pattern
matching German's working approach. Removed all Postgres
writing_sessions code. Added volume mount in
`docker-compose.prod.yml`. Seeded empty `writing_sessions.json`.

**Current state:** 9bf0081 likely never deployed (Bug A).
EC2 still running old Postgres code.

### What's needed after Bug A is fixed

1. **Verify `/opt/minimoi/data/portuguese/` exists on EC2**
   (the `mkdir -p` in the deploy step above handles this)

2. **Verify `writing_sessions.json` is present:**
   ```bash
   ls -la /opt/minimoi/data/portuguese/
   cat /opt/minimoi/data/portuguese/writing_sessions.json
   ```
   Should show `{"entries": []}` from the seeded file.

3. **Test end-to-end:**
   - Write in Escrita → Corrigir → Salvar
   - Check Arquivo Escrita tab → entry should appear
   - Reload page → entry still there (disk, not memory)
   - Restart container → entry still there (volume mount works)

### Architecture confirmed correct (commit 9bf0081)

Portuguese now follows German's JSON-first pattern:
- `_pt_save_writing_entry()` writes to `writing_sessions.json`
- `_pt_get_writing_sessions()` reads from same file
- Volume mount: `/opt/minimoi/data/portuguese:/app/domains/portuguese/data`
- No Postgres dependency for writing sessions

This is correct. Just needs the deploy to actually run.

---

## Bug C — German Schreiben same deploy issue

Commit 638183c (Übernehmen button, contenteditable corrected
text, writing prompt styling) not visible on minimoi.ai.
EC2 still running pre-638183c image.

Fixes automatically once Bug A is resolved and deploy runs.

**After deploy — verify German:**
- Übernehmen button visible below KORRIGIERT section
- Writing prompt visible in Tagebuch mode (italic, styled)
- Word capture works on corrected text (select → popover)

**German data volume:** Also confirm `docker-compose.prod.yml`
has German volume mount:
```yaml
minimoi-german:
  volumes:
    - /opt/minimoi/data/german:/app/domains/german/data
```
If missing, add it. German Archiv data also needs to persist
across deploys.

---

## German writing prompt visibility (Bug C follow-up)

Prompt text exists in code (`_TAGEBUCH_PROMPTS`, 8 entries).
HTML slot exists in template. But prompt uses muted gray —
less visible than Portuguese's italic green.

**CSS fix in `german.css`:**
```css
.tagebuch-prompt {
  font-style: italic;
  color: var(--md-text-secondary);  /* was --md-text-muted */
  font-size: 1rem;
  margin-bottom: 12px;
  min-height: 1.4em;
}
```

Small change — just bumps from muted to secondary color level.
More readable, matches Portuguese visual weight.

---

## What's in code but not yet on EC2

| Commit | Contents | On prod? |
|--------|---------|---------|
| 638183c | Übernehmen/Aceitar buttons, contenteditable, touchend word capture, 200-char limit | ❌ No |
| 9bf0081 | Portuguese JSON save (replaces Postgres), volume mount | ❌ No |

Both will deploy correctly once Bug A is fixed.

---

## Fix order — do exactly this

```
1. Fix deploy.yml — add ssm wait + mkdir -p host dirs
   Push this change alone first.
   Verify next push actually updates EC2.

2. Manually create host dirs on EC2 NOW (don't wait for deploy):
   aws ssm send-command \
     --instance-ids i-0d13db821169627e2 \
     --document-name "AWS-RunShellScript" \
     --parameters "commands=[
       'mkdir -p /opt/minimoi/data/portuguese',
       'mkdir -p /opt/minimoi/data/german'
     ]"

3. Force re-deploy of current main to EC2:
   After deploy.yml fix is pushed, trigger manual workflow
   dispatch to re-deploy — this will pull 638183c + 9bf0081
   and actually restart containers.

4. Verify Portuguese Escrita saves (end-to-end test on phone)

5. Verify German Schreiben buttons + prompts

6. Fix German writing prompt CSS visibility (minor tweak)
```

---

## Test checklist (run on phone after deploy confirmed)

**Deploy confirmed:**
- [ ] GitHub Actions shows "Deploy confirmed successful on EC2"
  (not just "Command accepted")
- [ ] New templates visible: Übernehmen/Aceitar button on Schreiben/Escrita
- [ ] Old hint text "CLIQUE UMA PALAVRA PARA TRADUZIR" gone

**Portuguese Escrita saves:**
- [ ] Write text → Corrigir → correction appears
- [ ] Click Salvar → no error
- [ ] Arquivo Escrita tab shows entry immediately
- [ ] Reload page → entry still there
- [ ] /opt/minimoi/data/portuguese/writing_sessions.json has content

**German Schreiben saves:**
- [ ] Write text → Korrigieren → correction appears
- [ ] Übernehmen button copies correction to textarea
- [ ] Speichern → entry in Archiv Schreiben
- [ ] /opt/minimoi/data/german/writing_sessions.json has content

**German writing prompt:**
- [ ] Tagebuch mode shows prompt in italic, readable color
- [ ] ↻ Anderer button rotates to next prompt

---

## Definition of Done

- [ ] deploy.yml uses ssm wait command-executed
- [ ] mkdir -p for both data dirs in deploy command
- [ ] EC2 host dirs exist: /opt/minimoi/data/portuguese + /german
- [ ] Both volume mounts in docker-compose.prod.yml
- [ ] German data volume mount added if missing
- [ ] Portuguese Escrita saves to Arquivo (confirmed on phone)
- [ ] German Schreiben saves to Archiv (confirmed on phone)
- [ ] Übernehmen/Aceitar button visible on both domains
- [ ] German writing prompt visible and readable
- [ ] spec committed to docs/specs/

## Commit message

`Fix: CI/CD deploy verification (ssm wait), EC2 host dirs,
German volume mount. Both domains: writing saves confirmed,
Übernehmen button, writing prompt visibility.`

---

*Spec · 2026-06-27 · Claude.ai*
*Fix Bug A first — nothing else can be validated without it*
*All fixes in this spec blocked by deploy reliability*
EOF