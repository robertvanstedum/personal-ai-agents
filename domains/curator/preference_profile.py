"""Format loaded preference signals; the adapter owns file access and the clock.

Preserves existing thresholds, ordering and diagnostic notices. Never edits preferences.
"""

def format_user_profile(prefs, *, active_domain, now, min_weight=2):
    lp = prefs.get('learned_patterns', {})
    sample_size = lp.get('sample_size', 0)

    # Decay gate: raise the signal threshold as data gets stale.
    # Old feedback shouldn't lock in forever — weak signals fade first.
    # Formula: > 30 days → min_weight 3, > 60 days → min_weight 4
    last_updated_str = lp.get('last_updated', '')
    if last_updated_str:
        try:
            from datetime import datetime, timezone
            last_updated = datetime.fromisoformat(last_updated_str)
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)
            days_stale = (now - last_updated).days
            if days_stale > 60:
                min_weight = max(min_weight, 4)
                print(f"   ⏳ Feedback is {days_stale}d old — decay gate active (min_weight={min_weight})")
            elif days_stale > 30:
                min_weight = max(min_weight, 3)
                print(f"   ⏳ Feedback is {days_stale}d old — decay gate active (min_weight={min_weight})")
        except Exception:
            pass  # Bad timestamp — continue with default min_weight


    if sample_size < 3:
        return ""  # Not enough data yet

    sections = []

    # ── Themes (most reliable signal from Claude metadata extraction) ──
    themes = {k: v for k, v in lp.get('preferred_themes', {}).items() if abs(v) >= min_weight}
    if themes:
        pos = sorted([(k, v) for k, v in themes.items() if v > 0], key=lambda x: -x[1])
        neg = sorted([(k, v) for k, v in themes.items() if v < 0], key=lambda x: x[1])
        if pos:
            sections.append("Strong interest in themes: " + ", ".join(k for k, _ in pos[:5]))
        if neg:
            sections.append("Low interest in themes: " + ", ".join(k for k, _ in neg[:3]))

    # ── Sources ──
    sources = {k: v for k, v in lp.get('preferred_sources', {}).items() if abs(v) >= min_weight}
    if sources:
        pos = sorted([(k, v) for k, v in sources.items() if v > 0], key=lambda x: -x[1])
        neg = sorted([(k, v) for k, v in sources.items() if v < 0], key=lambda x: x[1])
        if pos:
            sections.append("Preferred sources: " + ", ".join(k for k, _ in pos[:5]))
        if neg:
            sections.append("Penalize sources: " + ", ".join(k for k, _ in neg[:3]))

    # ── Domain signals (inferred from X bookmark link ecosystems — Phase 3C) ──
    # Reads only signals for active_domain — keeps briefing focused on one topic cluster.
    # domain_signals structure: { "Finance and Geopolitics": { "ft.com": 14, ... }, ... }
    all_domain_signals = lp.get('domain_signals', {})
    active_signals     = all_domain_signals.get(active_domain, {})
    top_domains = [(d, s) for d, s in sorted(active_signals.items(), key=lambda x: -x[1]) if s >= 2][:8]
    if top_domains:
        domains_str = ', '.join(f"{d}(+{s})" for d, s in top_domains)
        sections.append(f"Content domains from trusted X curators [{active_domain}]: {domains_str}")

    # ── Content domains (from feedback likes/saves on briefing articles — Phase 3C) ──
    # Format: { "ft.com": {"like": 2, "save": 3, "dislike": 0}, ... }
    # Separate from domain_signals (which comes from X bookmark ecosystems).
    cd = lp.get('content_domains', {})
    if cd:
        def cd_score(d): return d.get('like', 0) * 2 + d.get('save', 0) - d.get('dislike', 0) * 2
        pos_cd = sorted([(k, cd_score(v)) for k, v in cd.items() if cd_score(v) > 0], key=lambda x: -x[1])[:8]
        neg_cd = sorted([(k, cd_score(v)) for k, v in cd.items() if cd_score(v) < 0], key=lambda x: x[1])[:3]
        if pos_cd:
            sections.append("Preferred content domains: " + ", ".join(
                f"{k}(+{s})" for k, s in pos_cd
            ))
        if neg_cd:
            sections.append("Penalize content domains: " + ", ".join(k for k, _ in neg_cd))

    # ── Source types (academic_paper, news_article, substack, etc. — Phase 3C) ──
    st = lp.get('source_types', {})
    if st:
        def st_score(d): return d.get('like', 0) * 2 + d.get('save', 0) - d.get('dislike', 0) * 2
        pos_st = sorted([(k, st_score(v)) for k, v in st.items() if st_score(v) > 0], key=lambda x: -x[1])[:5]
        if pos_st:
            sections.append("Preferred source types: " + ", ".join(
                f"{k}(+{s})" for k, s in pos_st
            ))

    # ── Content topics (from Haiku text analysis of saved tweets — Phase 3C) ──
    # Lower threshold than min_weight — topics start at save=1, build over time.
    ct = lp.get('content_topics', {})
    if ct:
        def ct_score(d): return d.get('like', 0) * 2 + d.get('save', 0) - d.get('dislike', 0) * 2
        top_ct = sorted([(k, ct_score(v)) for k, v in ct.items() if ct_score(v) >= 2], key=lambda x: -x[1])[:10]
        if top_ct:
            sections.append("Content topics from saved posts: " + ", ".join(
                f"{k}(+{s})" for k, s in top_ct
            ))

    # ── Content types ('descriptive' excluded — known co-tag artifact, not a standalone signal) ──
    CO_TAG_EXCLUDE = {'descriptive'}
    content = {k: v for k, v in lp.get('preferred_content_types', {}).items()
               if abs(v) >= min_weight and k not in CO_TAG_EXCLUDE}
    if content:
        pos = sorted([(k, v) for k, v in content.items() if v > 0], key=lambda x: -x[1])
        neg = sorted([(k, v) for k, v in content.items() if v < 0], key=lambda x: x[1])
        if pos:
            sections.append("Preferred content style: " + ", ".join(k for k, _ in pos[:4]))
        if neg:
            sections.append("Penalize content style: " + ", ".join(k for k, _ in neg[:3]))

    # ── Avoid patterns (extracted from disliked articles) ──
    avoid = sorted(lp.get('avoid_patterns', {}).items(), key=lambda x: -x[1])
    top_avoid = [k for k, v in avoid[:5] if v >= 1]
    if top_avoid:
        sections.append("Avoid signals: " + ", ".join(top_avoid))

    if not sections:
        return ""

    profile = f"\nPERSONALIZATION (from {sample_size} user interactions — adjust base score by +1 to +2 for strong matches, -1 to -2 for avoids):\n"
    profile += "\n".join(f"- {s}" for s in sections)
    profile += "\n"
    return profile
