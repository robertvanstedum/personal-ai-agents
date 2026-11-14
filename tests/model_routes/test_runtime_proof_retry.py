"""#606: a rate-limit retry of the same run keeps its tools; receipts still never cover a run that later used tools.

OpenClaw ends an attempt with ``agent_end`` success=false on a gateway 429, waits, then retries the SAME runId.
These tests drive the real tracker (core/model_routes/runtime_proof.cjs) under node with real ed25519 signing and
check every receipt it publishes with the Python verifiers the portal, MC binding and CoS binding use.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.model_routes.runtime_proof import unused_completed, valid_receipt, verified
from minimoi_portal.guild_ui.mc.turn_ledger import run_end_proved

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")

ROOT = Path(__file__).resolve().parents[2]
TRACKER = ROOT / "core" / "model_routes" / "runtime_proof.cjs"
INSTANCE = "b" * 32
USER = "fixture"

# One scenario per run. Each step is [op, arg]; "tool" records whether before_tool_call was allowed.
DRIVER = r"""
const {tracker, identity} = require(process.argv[1]);
const {instance, settleMs, scenarios} = JSON.parse(process.argv[2]);
const out = {publicKey: identity(instance).publicKey, runs: {}};
const receipts = [];
const t = tracker({instance, states: new Map(), settleMs, publish: async r => { receipts.push(r); }});
(async () => {
  for (const [run, steps] of Object.entries(scenarios)) {
    const ctx = {runId: run, agentId: 'mc-chat', sessionKey: 'agent:mc-chat:openai-user:fixture'};
    const tools = [];
    for (const [op, arg] of steps) {
      if (op === 'model') t.modelStarted({callId: arg}, ctx);
      else if (op === 'tool') tools.push(t.beforeTool({toolName: 'minimoi_host_list'}, {...ctx, toolCallId: arg}) === undefined);
      else if (op === 'end') await t.ended({success: arg}, ctx);
      else if (op === 'sleep') await new Promise(r => setTimeout(r, arg));
    }
    out.runs[run] = {tools, receipts: receipts.filter(r => r.run === run)};
  }
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exitCode = 1; });
"""


def run_id(n: str) -> str:
    return f"chatcmpl_{n * 8}-1111-4111-8111-111111111111"


def drive(scenarios, settle_ms=50, module=TRACKER):
    done = subprocess.run(["node", "-e", DRIVER, str(module),
                           json.dumps({"instance": INSTANCE, "settleMs": settle_ms, "scenarios": scenarios})],
                          capture_output=True, text=True, timeout=30, cwd=ROOT)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def proved(receipt, key, run):
    return verified(receipt, key) and run_end_proved(receipt, run=run, instance=INSTANCE, agent="mc-chat",
                                                     user=USER, public_key=key)


def test_node_tracker_suite():
    done = subprocess.run(["node", str(ROOT / "tests" / "model_routes" / "runtime_proof.test.cjs")],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert "runtime proof: passed" in done.stdout


def test_rate_limit_retry_keeps_tools_and_one_receipt_covers_all_attempts():
    run = run_id("4")
    steps = [["model", "attempt-1"], ["tool", "before-429"], ["end", False],    # gateway 429 ends the attempt
             ["tool", "between-attempts"],                                      # nothing may run between attempts
             ["model", "attempt-2"]]                                            # transient same-model retry
    steps += [["tool", f"host-list-{i}"] for i in range(9)]                     # the nine calls blocked on Dev
    steps += [["end", True], ["tool", "after-final-end"], ["sleep", 120]]       # past settleMs: no second receipt
    out = drive({run: steps})
    result = out["runs"][run]
    assert result["tools"] == [True, False] + [True] * 9 + [False]
    [receipt] = result["receipts"]
    assert valid_receipt(receipt) and proved(receipt, out["publicKey"], run)
    assert receipt["success"] is True and receipt["model_calls"] == 2 and receipt["tool_calls"] == 10
    assert not unused_completed(receipt)          # a run that used tools can never authorize depth continuation


def test_quick_run_without_retry_still_authorizes_depth():
    run = run_id("5")
    out = drive({run: [["model", "only"], ["end", True], ["tool", "late"]]})
    [receipt] = out["runs"][run]["receipts"]
    assert out["runs"][run]["tools"] == [False]   # a successful final end still tombstones the run
    assert proved(receipt, out["publicKey"], run) and unused_completed(receipt)


def test_retry_counts_both_model_calls_so_depth_stays_refused():
    """Conservative: a retried run made two model calls, so it is never 'unused completed' (same as before #606)."""
    run = run_id("6")
    out = drive({run: [["model", "a"], ["end", False], ["model", "b"], ["end", True]]})
    [receipt] = out["runs"][run]["receipts"]
    assert receipt["tool_calls"] == 0 and receipt["model_calls"] == 2 and not unused_completed(receipt)


def test_error_end_without_retry_settles_to_a_failed_receipt_and_stays_fenced():
    run = run_id("7")
    out = drive({run: [["model", "only"], ["tool", "t1"], ["end", False], ["sleep", 150],
                       ["model", "too-late"], ["tool", "after-receipt"], ["end", True]]})
    result = out["runs"][run]
    [receipt] = result["receipts"]                 # the late attempt never yields a second receipt
    assert result["tools"] == [True, False]        # never a tool under a published receipt
    assert proved(receipt, out["publicKey"], run)  # what settle_unknown relies on for a lost stream
    assert receipt["success"] is False and receipt["tool_calls"] == 1


def test_native_cli_runs_are_unchanged():
    run = "77777777-3333-4333-8333-333333333333"
    out = drive({run: [["model", "n1"], ["tool", "n-a"], ["end", False], ["tool", "n-b"],
                       ["model", "n2"], ["end", True], ["tool", "n-c"], ["sleep", 120]]})
    assert out["runs"][run] == {"tools": [True, True, True], "receipts": []}
