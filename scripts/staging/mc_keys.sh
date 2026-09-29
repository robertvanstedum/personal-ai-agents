#!/bin/bash
# mc_keys.sh — the pieces of MC stage C (MC spec v0.9 §2, §6) that mc.sh and
# the no-spend probe share, so the probe tests exactly what Robert runs.
# Sourced; defines functions only. Never prints a secret.

# The gateway's key database and its own role in the existing Postgres.
KEYDB_NAME="litellm_keys"
KEYDB_ROLE="litellm_keys"

# MC's virtual key: its own route only; chat completions only; a monthly
# budget; rpm 10. /key/info is NOT allowed (unlike spec v0.9 §2): the stage C
# probe found LiteLLM 1.93.1 answers /key/info?key=<another key> for any key
# that may call /key/info, so MC could read other keys' records. The positive
# control reads MC's key with the master key, inside the gateway, instead.
MC_KEY_MODELS='["minimoi-mc-agent"]'
MC_KEY_ROUTES='["/v1/chat/completions", "/chat/completions"]'
MC_KEY_BUDGET_DURATION="30d"
MC_KEY_RPM=10

# keydb_sql PASSWORD: idempotent SQL that creates (or re-passwords) the role and
# creates its database, owned by it. PASSWORD is hex (no quoting issues). Run it
# through psql's stdin, never as an argument, so the password is not in `ps`.
keydb_sql() {
  local pw="$1"
  [[ "$pw" =~ ^[0-9a-f]{32,}$ ]] || { echo "keydb_sql: the password must be 32+ hex characters" >&2; return 64; }
  cat <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$KEYDB_ROLE') THEN
    CREATE ROLE $KEYDB_ROLE LOGIN PASSWORD '$pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  ELSE
    ALTER ROLE $KEYDB_ROLE WITH LOGIN PASSWORD '$pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE $KEYDB_NAME OWNER $KEYDB_ROLE'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$KEYDB_NAME')\gexec
REVOKE ALL ON DATABASE $KEYDB_NAME FROM PUBLIC;
GRANT ALL ON DATABASE $KEYDB_NAME TO $KEYDB_ROLE;
SQL
}

# keydb_url PASSWORD HOST: the gateway's DATABASE_URL.
keydb_url() { echo "postgresql://$KEYDB_ROLE:$1@$2:5432/$KEYDB_NAME"; }

# gateway_keygen_py MODELS ROUTES DURATION RPM ALIAS_PREFIX ROLE CAP_VAR OLD_VAR:
# Python run INSIDE the gateway container (stdin), with the master key from the
# container's own LITELLM_MASTER_KEY. Env in: $CAP_VAR (dollars), $OLD_VAR
# (optional; deleted after the new key exists). Prints two lines: the new key,
# then the last four characters of its token id (the only non-secret part).
gateway_keygen_py() {
  local models="$1" routes="$2" duration="$3" rpm="$4" alias_prefix="$5" role="$6" cap_var="$7" old_var="$8"
  cat <<PY
import json, os, sys, urllib.request as u
base = "http://127.0.0.1:4000"
master = os.environ["LITELLM_MASTER_KEY"]
def call(path, body):
    req = u.Request(base + path, data=json.dumps(body).encode(), method="POST",
                    headers={"Authorization": "Bearer " + master, "Content-Type": "application/json"})
    with u.urlopen(req, timeout=60) as r:
        return json.loads(r.read())
cap = float(os.environ["$cap_var"])
made = call("/key/generate", {
    "models": $models,
    "allowed_routes": $routes,
    "max_budget": cap,
    "budget_duration": "$duration",
    "rpm_limit": $rpm,
    "key_alias": "$alias_prefix" + os.urandom(3).hex(),
    "metadata": {"role": "$role", "caller": "interactive"},
})
old = os.environ.get("$old_var", "")
if old:
    try:
        call("/key/delete", {"keys": [old]})
    except Exception as e:
        print("old key not deleted: " + type(e).__name__, file=sys.stderr)
print(made["key"])
print(str(made.get("token") or made.get("token_id") or "")[-4:])
PY
}

# MC's key (mc.sh key). Env in: MC_CAP, MC_OLD_KEY.
mc_keygen_py() {
  gateway_keygen_py "$MC_KEY_MODELS" "$MC_KEY_ROUTES" "$MC_KEY_BUDGET_DURATION" "$MC_KEY_RPM" "mc-agent-" "guild.mc" MC_CAP MC_OLD_KEY
}

# CoS's own key (cos.sh key; Robert's decision, September 28 2026: CoS gets
# its own capped key instead of the master key). CoS's four routes only; chat
# completions and responses (its bounded web search) only; a monthly budget;
# rpm 60 (a CoS turn can make several calls: tools, the fallback, web search).
# Like MC's, /key/info is not allowed (it would read other keys' records).
COS_KEY_MODELS='["minimoi-cos-agent", "minimoi-cos-agent-xai-fast", "minimoi-cos-web-search", "minimoi-cos-agent-anthropic"]'
COS_KEY_ROUTES='["/v1/chat/completions", "/chat/completions", "/v1/responses", "/responses"]'
COS_KEY_BUDGET_DURATION="30d"
COS_KEY_RPM=60

# CoS's key. Env in: COS_CAP, COS_OLD_KEY.
cos_keygen_py() {
  gateway_keygen_py "$COS_KEY_MODELS" "$COS_KEY_ROUTES" "$COS_KEY_BUDGET_DURATION" "$COS_KEY_RPM" "cos-agent-" "guild.cos" COS_CAP COS_OLD_KEY
}

# valid_cap TEXT: a positive dollar amount up to 500, at most two decimals.
valid_cap() { [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,2})?$ ]] && awk "BEGIN{exit !($1 > 0 && $1 <= 500)}"; }
