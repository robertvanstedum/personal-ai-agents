"""Dashboard values from already loaded records; storage remains in routes."""
import re as _re


def topic_summary(topic, session_ids, article_signals, all_candidates,
                  log_by_session, thread, sessions_at_last_dive):
    session_set = set(session_ids)

    # Saves: article signals whose session_id belongs to this topic
    saved_count = sum(1 for s in article_signals
                      if s.get('session_id') in session_set)

    # Candidates: query candidates for this topic still in 'candidate' status
    candidate_count = sum(1 for c in all_candidates
                           if c.get('topic') == topic
                           and c.get('status') == 'candidate')

    # Last run + top find: most recent session-log row for this topic's sessions
    topic_rows = sorted(
        [log_by_session[sid] for sid in session_ids if sid in log_by_session],
        key=lambda r: r['date'],
        reverse=True,
    )

    last_run = None
    top_find = None
    if topic_rows:
        latest   = topic_rows[0]
        last_run = latest['date']
        tf_match = _re.search(r'top find:\s*(.+)', latest.get('notes', ''))
        top_find = tf_match.group(1).strip() if tf_match else None

    return {
        "topic":                  topic,
        "session_count":          len(session_ids),
        "saved_count":            saved_count,
        "candidate_count":        candidate_count,
        "last_run":               last_run,
        "top_find":               top_find,
        "status":                 thread.status if thread else "active",
        "expires":                thread.expires if thread else None,
        "deeper_dive_generated":  thread.deeper_dive_generated if thread else False,
        "deeper_dive_path":       thread.deeper_dive_path if thread else None,
        "sessions_at_last_dive":  sessions_at_last_dive,
    }


def dashboard_payload(topic_summaries, used, budget_total, budget_warn, active):
    return {
        "ok": True,
        "topics": topic_summaries,
        "budget": {"used": used, "total": budget_total, "warn": budget_warn,
                   "pct": round(used / budget_total * 100, 1) if budget_total else 0.0},
        "agent_active": active,
    }
