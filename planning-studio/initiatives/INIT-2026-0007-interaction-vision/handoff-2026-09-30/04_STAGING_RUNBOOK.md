# 04: Staging runbook (Mac, Colima)

All staging scripts are in `scripts/staging/`. Run them from the **release worktree** `~/.worktrees/staging-release`, which build.sh manages. The root checkout is on an old branch and doesn't have these scripts. Details are in `scripts/staging/README.md` on the integration branch.

## Colima
- `colima start` or `colima stop`. Stop it when staging isn't being tested, because the Mac has 8 GB of memory.
- dev.minimoi.ai goes down with it.

## Roll out a change to dev
1. **Only reviewed heads go into the integration branch:**
   `cd ~/.worktrees/integration && git fetch -q origin && git merge --no-edit origin/<branch>`
   - When two PRs both append browser checks, resolve by keeping both sections.
   - Then run: `venv/bin/python3 -m pytest tests/guild tests/cos tests/usage tests/workshop -q -p no:cacheprovider`, plus the browser checks:
     - `tests/guild/shop_floor/browser_checks_b1.py`
     - `tests/cos/browser_checks_confer*.py`
     - `tests/browser_checks_voice_mic.py`
   - Use the root venv at `~/Projects/personal-ai-agents/venv/bin/python3`.
   - Then `git push origin staging/integration-2026-09-29`.
2. **Build:** `cd ~/.worktrees/staging-release && scripts/staging/build.sh staging/integration-2026-09-29 --reviewed-branch`
   - When build.sh itself changed, run it twice: the second time with `--no-fetch`. The script resets its own worktree.
   - It creates the staging data folders (for example `data/usage/portal`, `data/usage/cos-*`, `data/workshops`, `data/cos-turns`). If `up.sh` says "missing folder … run seed.sh", re-run build.sh; don't run seed.sh blindly.
3. **Start only what changed:** `scripts/staging/up.sh portal cos-scheduler` (add any other service the classifier names).
4. **MC,** when `docker/mc-agent/*` or the relay changed:
   - `scripts/staging/mc.sh down`
   - `scripts/staging/mc.sh build`
   - `scripts/staging/mc.sh up`
   - `scripts/staging/mc.sh status`
5. **Check:**
   - `curl -s -o /dev/null -w "%{http_code}" http://localhost:5001/health` should give 200;
   - `docker ps`;
   - `docker logs minimoi-portal --since 5m | grep -iE "error|traceback"`;
   - optionally `scripts/staging/verify.sh`. Image-mismatch failures are expected with partial rollouts.

## Prune the disk after every build (each build leaves 5–8 GB)
```bash
docker ps --format '{{.Image}}' | sort -u     # images in use: keep these, plus the previous portal tag
docker images --format '{{.Repository}}:{{.Tag}}' | grep '^minimoi-staging/' | grep -vE '<keep-regex>' | xargs -r docker rmi
docker image prune -f
docker builder prune -af
colima ssh -- sudo fstrim /mnt/lima-colima    # returns the space to macOS
df -h /
```
Never remove the snapshot volumes (`*-pre96-*`, `*-premcA-*`, `*-pre245-*`) or the `personal-ai-agents_*` volumes without Robert's OK.

## CoS switches (`scripts/staging/cos.sh`)
- `cos.sh status` shows the key and the turn-log state.
- `cos.sh key`: CoS's own capped key. **Robert types the cap.** CoS is on its own key now.
- `cos.sh off`: back to the master key. Run it **before** turning `state/gateway.keys` off.
- `cos.sh turns on|off`: the staging voice-transcript turn log (`state/cos.turns`, **off** now). It recreates only cos-scheduler.

## Paid probes (operator-only; each spends money, so ask Robert, or stay within his authorised budget)
- `scripts/staging/mc_cost_probe.sh --turns 4 --cap 0.5 --yes-spend` compares fresh against long context.
- `scripts/staging/mc_cost_probe.sh --stream --yes-spend` runs turn A (streaming and usage) and turn B (Stop).
- Both run through `docker exec` into the portal; there is no browser and no login. They cost about $0.005–0.02 per run.

## Workshop host reading (until a launchd job exists)
```bash
cd ~/.worktrees/integration && ~/Projects/personal-ai-agents/venv/bin/python3 scripts/workshop/workshop.py observe && ~/Projects/personal-ai-agents/venv/bin/python3 scripts/workshop/workshop.py sync
```
Run it every 9 minutes or less while testing: as a `run_in_background` loop in a session, or as a launchd job once Robert approves.

## The Operations launchd job
- **Status:** `launchctl print gui/$(id -u)/com.user.operations | grep -E "state|pid"` and `curl -s localhost:8768/status`.
- **Logs:** `~/.worktrees/ops-runtime/logs/operations_{stdout,stderr}.log`.
- **To update it after #280 changes:**
  1. `git -C ~/.worktrees/ops-runtime checkout --detach <new sha>`
  2. `launchctl kickstart -k gui/$(id -u)/com.user.operations`

## Telegram status helper (recreate if the scratchpad is wiped)
Save it as `<session scratchpad>/tg.py`. It reads the Keychain `telegram/bot_token` and `telegram/chat_id` inside the process and never prints either.
```python
import json, subprocess, sys, urllib.request
def _kc(a): return subprocess.run(["security","find-generic-password","-s","telegram","-a",a,"-w"],capture_output=True,text=True,check=True).stdout.strip()
body=json.dumps({"chat_id":_kc("chat_id"),"text":"🛠 Claude Code: "+sys.argv[1][:3900]}).encode()
req=urllib.request.Request(f"https://api.telegram.org/bot{_kc('bot_token')}/sendMessage",data=body,headers={"Content-Type":"application/json"})
try:
    with urllib.request.urlopen(req,timeout=15) as r: print("sent" if r.status==200 else r.status)
except Exception as e: print("send failed:",type(e).__name__); sys.exit(1)
```

## Review packs (screen PDFs)
Use the shared tool (PR #282 adds the local sample server):
- `python -m scripts.tools.tour_capture.cli --scenario-file scripts/tools/tour_capture/scenarios/guild_1_1_<device>.json --base-url http://127.0.0.1:<PORT> --headless --pdf --browser-channel chrome`
- Then `python -m scripts.tools.tour_capture.review_pdf RUN_DIR... -o OUT.pdf --title "..."`.
- Sample captures stay labelled as sample. Real dev capture uses owner-login mode **with Robert at the keyboard only**.
