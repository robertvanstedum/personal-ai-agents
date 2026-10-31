#!/bin/sh
# Start Master Craftsman's own container (spec_mc_agent_container v0.2, phase 1).
# 1. Refuse to start if the container holds a provider API key or the config names a non-plan Anthropic route
#    (spec_provider_access: agents use plan routes only; no metered fallback).
# 2. Lay out the pinned persona release into the workspace, refusing on any hash mismatch with the release manifest or
#    an AGENTS header version that differs from the release folder (the 0.1.6-label defect cannot recur). Load the
#    handbook the release pins (its manifest hash) into the same projection, counted in the same budget.
# 3. Install the image's config, validate it with OpenClaw's own validator, then run the gateway.
set -eu

IMAGE_DIR=/opt/minimoi/mc-agent
PERSONA="$IMAGE_DIR/persona"
STATE="${OPENCLAW_STATE_DIR:-/home/node/.openclaw}"
WORKSPACE="$STATE/workspace-mc"
OPENCLAW="node /app/openclaw.mjs"
log() { echo "mc-agent[start]: $*" >&2; }
die() { log "REFUSED: $*"; exit 78; }

# 1. No provider keys, ever.
for v in ANTHROPIC_API_KEY OPENAI_API_KEY XAI_API_KEY MC_ANTHROPIC_API_KEY MC_MODEL_GATEWAY_KEY; do
  eval "val=\${$v:-}"
  [ -z "$val" ] || die "provider key $v is set in the container environment"
done

# 2. Persona: verify the release against its manifest, then lay it out.
node -e '
const fs=require("fs"),c=require("crypto"),p=process.argv[1];
const m=JSON.parse(fs.readFileSync(p+"/manifest.json"));
for (const [f,h] of Object.entries(m.files)) {
  const got=c.createHash("sha256").update(fs.readFileSync(p+"/"+f)).digest("hex");
  if (got!==h) { console.error("hash mismatch "+f); process.exit(1); }
}
const head=fs.readFileSync(p+"/AGENTS.md","utf8").split("\n")[0];
if (!head.endsWith("persona "+m.version)) { console.error("AGENTS header \""+head+"\" != release "+m.version); process.exit(1); }
' "$PERSONA" || die "persona release failed verification"
mkdir -p "$WORKSPACE"
# 2b. Handbook: the release names the handbook it loads by version and manifest hash. handbook.mjs checks the image's
#     copy against that pin and its manifest, lays it out under the workspace, and prints the block that ends AGENTS.md.
hb=$(node -e '
const h=require(process.argv[1]).handbook||{};
if (!/^[0-9]+\.[0-9]+\.[0-9]+$/.test(h.version||"")||!/^[0-9a-f]{64}$/.test(h.manifest_sha256||"")) process.exit(1);
console.log(h.version+" "+h.manifest_sha256);
' "$PERSONA/manifest.json") || die "persona release names no handbook (manifest.json handbook.version, handbook.manifest_sha256)"
# The block is held in memory, not a file MC could replace. $(...) drops its final newline; printf puts it back.
# shellcheck disable=SC2086
block=$(node "$IMAGE_DIR/handbook.mjs" "$IMAGE_DIR/handbook" "$WORKSPACE" $hb) || die "handbook ${hb%% *} failed verification"
cp "$PERSONA/SOUL.md" "$WORKSPACE/SOUL.md"
{ cat "$PERSONA/AGENTS.md"; printf '\n'; cat "$PERSONA/RUNTIME.openclaw.md"; printf '\n%s\n' "$block"; } > "$WORKSPACE/AGENTS.md"
chars=$(node -e 'console.log([...require("fs").readFileSync(process.argv[1],"utf8")].length)' "$WORKSPACE/AGENTS.md")
[ "$chars" -le 16000 ] || die "projected AGENTS.md is $chars characters (budget 16000)"
log "handbook ${hb%% *} loaded (manifest ${hb#* }) into $WORKSPACE/handbook"
log "persona $(node -e 'console.log(require(process.argv[1]).version)' "$PERSONA/manifest.json") laid out ($chars chars)"

# 3. Config: merge the image's policy into the live config, refuse a metered route, validate.
#    The image config is authoritative for every key it sets (objects merge recursively; image scalars and arrays win),
#    except plugins.allow, which is the union with the live list restricted to PLUGINS_PERMITTED. Keys only OpenClaw or
#    a plan sign-in added (e.g. the codex plugin entry after ChatGPT OAuth, gateway.controlUi.allowedOrigins) are kept.
#    The file is rewritten only when the merge changes it: OpenClaw refuses to start if its config changes mid-start,
#    and replacing it wholesale on every start erased sign-in entries and caused exactly that loop (25 Oct).
mkdir -p "$STATE"
[ -f "$STATE/openclaw.json" ] || cp "$IMAGE_DIR/openclaw.json" "$STATE/openclaw.json"
node -e '
const fs=require("fs"),[img,live]=process.argv.slice(1);
const PLUGINS_PERMITTED=new Set(["memory-core","anthropic","xai","openai","codex","minimoi-tools","mc-bounded-search"]);
const I=JSON.parse(fs.readFileSync(img)),L=JSON.parse(fs.readFileSync(live));
const isObj=v=>v&&typeof v==="object"&&!Array.isArray(v);
const merge=(i,l)=>{if(!isObj(i)||!isObj(l))return i;const o={...l};for(const k of Object.keys(i))o[k]=merge(i[k],l[k]);return o;};
const out=merge(I,L);
const allow=[...new Set([...(I.plugins?.allow||[]),...(L.plugins?.allow||[])])];
const bad=allow.filter(p=>!PLUGINS_PERMITTED.has(p));
if(bad.length){console.error("plugins not permitted: "+bad.join(","));process.exit(1);}
out.plugins.allow=allow;
// Compare ignoring OpenClaw-owned bookkeeping (meta). Never write the file directly: OpenClaw treats an external write
// as an accidental clobber and restores its own copy (seen 25 Oct). Emit a patch for `openclaw config patch` instead.
const strip=o=>{const c={...o};delete c.meta;return c;};
const changed=JSON.stringify(strip(out))!==JSON.stringify(strip(L));
const patch={...I,plugins:{...I.plugins,allow}};
fs.writeFileSync("/tmp/mc-config.patch.json",JSON.stringify(patch));
console.error(changed?"config differs from image policy: patching":"config unchanged");
process.exit(changed?10:0);
' "$IMAGE_DIR/openclaw.json" "$STATE/openclaw.json" && rc=0 || rc=$?
case "$rc" in
  0) ;;
  10) OPENCLAW_CONFIG_PATH="$STATE/openclaw.json" $OPENCLAW config patch --file /tmp/mc-config.patch.json >/tmp/patch.out 2>&1 \
        || { cat /tmp/patch.out >&2; die "openclaw config patch failed"; }
      log "config patched to image policy (openclaw config patch)" ;;
  *) die "config merge refused" ;;
esac
node -e '
const c=JSON.parse(require("fs").readFileSync(process.argv[1]));
const prof=((c.auth||{}).profiles)||{};
for (const [k,v] of Object.entries(prof)) if (v.mode==="api_key") { console.error("api_key auth profile "+k); process.exit(1); }
const models=((c.agents||{}).defaults||{}).models||{};
for (const [ref,v] of Object.entries(models)) if (ref.startsWith("anthropic/") && !((v.agentRuntime||{}).id==="claude-cli")) { console.error(ref+" not on claude-cli"); process.exit(1); }
' "$STATE/openclaw.json" || die "config would allow a metered Anthropic route"
OPENCLAW_CONFIG_PATH="$STATE/openclaw.json" $OPENCLAW config validate --json >/tmp/validate.json 2>&1 || { cat /tmp/validate.json >&2; die "openclaw config validate failed"; }

# 4. MiniMoi tools (phase 2, plan D2/D5). The portal's tools credential arrives in MC_TOOLS_TOKEN and is written to a file
#    only the node user can read, outside the workspace (the plugin refuses anything else); it is then removed from the
#    environment so neither the gateway nor any tool process inherits it. The plugin reaches the portal only through
#    127.0.0.1:5011, which forwards to the tools door and nowhere else.
if [ -n "${MC_TOOLS_TOKEN:-}" ]; then
  printf '%s' "$MC_TOOLS_TOKEN" | grep -Eq '^[A-Za-z0-9_-]{32,256}$' || die "MC_TOOLS_TOKEN is not a valid tools credential"
  ( umask 077; mkdir -p /home/node/.minimoi-tools; printf '%s\n' "$MC_TOOLS_TOKEN" > /home/node/.minimoi-tools/executor.token )
  chmod 0600 /home/node/.minimoi-tools/executor.token
  unset MC_TOOLS_TOKEN
  log "MiniMoi tools: credential in place"
else
  log "MiniMoi tools: no credential (MC_TOOLS_TOKEN unset); the plugin registers nothing"
fi
# Bounded web search (plan D5/D8): MC's own capped gateway key, search route only, in a node-only file like the tools
# credential; the plugin reaches the gateway only through 127.0.0.1:4000 -> the door.
if [ -n "${MC_SEARCH_KEY:-}" ]; then
  printf '%s' "$MC_SEARCH_KEY" | grep -Eq '^sk-[A-Za-z0-9_-]{16,200}$' || die "MC_SEARCH_KEY is not a gateway virtual key"
  ( umask 077; mkdir -p /home/node/.mc-search; printf '%s\n' "$MC_SEARCH_KEY" > /home/node/.mc-search/key )
  chmod 0600 /home/node/.mc-search/key
  unset MC_SEARCH_KEY
  log "bounded search: key in place"
fi
DOOR="${MC_TOOLS_DOOR:-mc-tools-door}"
MC_FORWARD="127.0.0.1:5011=$DOOR:5011,127.0.0.1:4000=$DOOR:4000" node "$IMAGE_DIR/tcp-forward.mjs" &
log "forwarders: 127.0.0.1:5011 and :4000 -> $DOOR (the only way to MiniMoi)"

# After a hard stop, OpenClaw's owner lease on the state directory stays held for a few minutes and then clears on its own
# ("Another Gateway owner lease is still active"; see docker/mc-agent/start-mc.sh). A gateway that exits within 90 s is
# retried after a pause, at most START_ATTEMPTS times; one that ran longer exits with its own status (restart policy).
START_ATTEMPTS="${MC_START_ATTEMPTS:-14}"            # the lease was observed to take ~4 minutes to clear
n=1
while :; do
  log "starting gateway (release ${MINIMOI_RELEASE_SHA:-unknown}), attempt $n/$START_ATTEMPTS"
  t0=$(date +%s)
  set +e
  $OPENCLAW gateway --port 18789
  rc=$?
  set -e
  ran=$(( $(date +%s) - t0 ))
  [ "$rc" -eq 137 ] && { log "gateway was KILLED (status 137: out of memory or SIGKILL) after ${ran}s; not retrying here"; exit "$rc"; }
  [ "$ran" -lt 90 ] && [ "$n" -lt "$START_ATTEMPTS" ] || exit "$rc"
  log "gateway exited after ${ran}s (status $rc); likely a stale owner lease; retrying in 30 s"
  n=$((n + 1))
  sleep 30
done
