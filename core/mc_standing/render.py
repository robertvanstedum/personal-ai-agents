"""The deterministic morning brief (standing spec §7.1). Zero model calls.

Three sections, in order:
  1. coverage and freshness: what could and could not be checked;
  2. what needs attention: each with its evidence reference and a proposed next step;
  3. open proposals, or a short no-action line (plus the optional learning line, P-G7).

A quiet, fully checked day is at most 3 lines. A normal brief is at most 20 lines plus a link to the
details. Unknown coverage is never a green day. P0 performs no product actions, so every next step
is a proposal. No guest names, secrets or log text appear here; only fixed phrases and small numbers.
"""
from __future__ import annotations

from datetime import date

MAX_LINES = 20
SEVERITY = {"failed": 0, "unknown": 1, "degraded": 2, "healthy": 3, "not_applicable": 4}

# Fixed proposed next steps by reason code. P0 never acts; these are proposals for Robert or MC.
NEXT_STEP = {
    "job_failed": "check the job's last run and rerun it through its runbook",
    "job_missed": "check why the scheduled run did not happen",
    "job_stuck": "check whether the run is hung before starting another",
    "job_never_ran": "confirm the job is installed and scheduled",
    "job_no_success": "check the job's first runs",
    "job_late": "watch for the next run; check if it stays late",
    "job_warn": "review the job's warnings",
    "services_failing": "check the failing services' health",
    "headroom_critical": "free space or memory now",
    "headroom_low": "plan to free space or memory",
    "deploy_image_check_failed": "check the last deployment and its rollback",
    "deploy_verification_failed": "re-run the deployment's health checks",
    "deploy_unverified": "verify the last deployment's health checks",
    "deploy_in_progress_stale": "check whether the last deployment finished",
    "guest_requests_waiting": "review the waiting access requests (Operate → Administration)",
    "guest_store_unavailable": "check the guest request stores",
}
DEFAULT_NEXT_STEP = "check this source"


def _label(watches_by_id, obs) -> str:
    watch = watches_by_id.get(obs.watch_id)
    return watch.label if watch else obs.watch_id


def _detail(obs) -> str:
    m = obs.measurements
    bits = []
    if m.get("last_success_age_h") is not None:
        bits.append(f"last success {m['last_success_age_h']}h ago")
    if m.get("services_failing"):
        bits.append(f"{m['services_failing']} of {m.get('services_total', m.get('services_checked', '?'))} failing")
    if m.get("disk_used_pct") is not None:
        bits.append(f"disk {m['disk_used_pct']}%, memory {m['mem_used_pct']}%")
    if m.get("pending_total"):
        age = f", oldest {m['oldest_age_h']}h" if m.get("oldest_age_h") is not None else ""
        bits.append(f"{m['pending_total']} pending{age}")
    return "; ".join(bits)


def render_brief(observations, watches, *, local_date: date, environment: str,
                 details_link: str | None = None, learning_counts: dict | None = None,
                 review_needed: list | None = None) -> str:
    by_id = {w.id: w for w in watches}
    obs = sorted(observations, key=lambda o: (SEVERITY.get(o.state, 9), _label(by_id, o)))
    checked = [o for o in obs if o.state in ("healthy", "degraded", "failed")]
    unknowns = [o for o in obs if o.state == "unknown"]
    attention = [o for o in obs if o.state in ("failed", "degraded")]
    missing_required = [w for w in watches if w.required_for_coverage
                        and w.id not in {o.watch_id for o in obs}]
    env = {"prod": "production", "dev": "dev"}.get(environment, environment)

    head = f"MC operations brief, {env}, {local_date.isoformat()}."
    learning = _learning_line(learning_counts)
    review = ("Watch review due (review-by date passed): " + ", ".join(review_needed) + "."
              if review_needed else None)

    if not attention and not unknowns and not missing_required:
        lines = [head, f"All {len(checked)} checks healthy and current."
                       + ("" if review else " Nothing needs you.")]
        lines += [x for x in (review, learning) if x]
        return "\n".join(lines)

    lines = [head]
    cov = f"Checked {len(checked)} of {len(obs) + len(missing_required)}."
    if unknowns or missing_required:
        names = [_label(by_id, o) for o in unknowns] + [w.label for w in missing_required]
        cov += " Could not check: " + ", ".join(names) + "."
    lines.append(cov)
    for o in unknowns:
        lines.append(f"- {_label(by_id, o)}: unknown ({o.reason.replace('_', ' ')}); "
                     f"proposed: {NEXT_STEP.get(o.reason, DEFAULT_NEXT_STEP)}. [{o.observation_id[:8]}]")
    for o in attention:
        detail = _detail(o)
        lines.append(f"- {_label(by_id, o)}: {o.state}"
                     f"{' (' + detail + ')' if detail else ''}; "
                     f"proposed: {NEXT_STEP.get(o.reason, DEFAULT_NEXT_STEP)}. [{o.observation_id[:8]}]")
    healthy = [o for o in obs if o.state == "healthy"]
    if healthy:
        lines.append("Healthy: " + ", ".join(_label(by_id, o) for o in healthy) + ".")
    lines.append("MC took no actions; the steps above are proposals." if attention or unknowns
                 else "No action needed.")
    lines += [x for x in (review, learning) if x]

    if len(lines) > MAX_LINES:
        kept = lines[:MAX_LINES - 1]
        kept.append(f"... {len(lines) - (MAX_LINES - 1)} more lines in the details.")
        lines = kept
    if details_link:
        lines.append(f"Details: {details_link}")
    return "\n".join(lines)


def _learning_line(counts: dict | None) -> str | None:
    """P-G7: shown only when something is pending; counts only, never statement text."""
    if not counts:
        return None
    pending = counts.get("suggestions_pending") or 0
    adopted = counts.get("practices_adopted_this_week") or 0
    repeats = counts.get("repeated_corrections") or 0
    parts = []
    if pending:
        parts.append(f"{pending} learning suggestion{'s' if pending != 1 else ''} pending")
    if adopted:
        parts.append(f"{adopted} practice{'s' if adopted != 1 else ''} adopted this week")
    if repeats:
        parts.append(f"{repeats} repeated correction{'s' if repeats != 1 else ''}")
    return ("Learning: " + " · ".join(parts) + ".") if parts else None
