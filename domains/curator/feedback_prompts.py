"""Exact metadata prompt construction; no provider or credential access."""

def metadata_prompt(article, user_words, feedback_type):
    return f"""Analyze this user feedback about an article and extract structured metadata.

Article:
- Title: {article['title']}
- Source: {article['source']}
- Category: {article['category']}

User feedback ({feedback_type}):
"{user_words}"

Extract and return ONLY a JSON object with these fields:
{{
  "content_type": ["list of content types: argumentative, analytical, descriptive, statistical, narrative, investigative"],
  "appeal": ["what appealed or didn't: evidence_based, institutional_tension, contrarian, depth, clarity, originality"],
  "style": ["writing style: challenge_not_summary, data_driven, opinion_based, technical, accessible"],
  "themes": ["themes: fiscal_policy, monetary_policy, geopolitics, institutional_debates, market_analysis"],
  "depth": "one of: surface_summary, moderate_analysis, deep_dive, original_research",
  "signals": ["positive signals if liked, negative if disliked"]
}}

Return ONLY valid JSON, no explanation."""
