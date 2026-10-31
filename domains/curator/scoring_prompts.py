"""Named Curator prompt builders; no SDK, credentials, I/O or scoring policy."""


def haiku_prompt(entries, user_profile=""):
    prompt = """You are a geopolitics & finance curator. For each article below, assign:
1. Category (ONE of: geo_major, geo_other, monetary, fiscal, technology, other)
2. Score (0-10): relevance for a geopolitics/finance professional

CATEGORIES:
- geo_major: US, China, Russia, Europe, Japan, Korea (deployed/operational context)
- geo_other: Middle East, Africa, Latin America, South/Southeast Asia
- monetary: Gold, Bitcoin, currencies, commodities, exchange rates
- fiscal: Government debt, spending, budgets, deficits
- technology: R&D, manufacturing, dual-use DEVELOPMENT (not yet deployed)
- other: Everything else

SCORE GUIDANCE:
9-10: Critical developments, major policy shifts, must-read analysis
7-8: Important trends, significant geopolitical/financial analysis
5-6: Relevant but not urgent, decent background
3-4: Tangential interest, minor relevance
0-2: Skip (noise, spam, off-topic, pure entertainment)
""" + user_profile + """
KEY DISTINCTION (technology vs geopolitics):
- If discussing DEPLOYED systems, active operations, current conflicts → geo category
- If discussing R&D, manufacturing capacity, future capabilities → technology
- Example: "Drones used in Ukraine" = geo_major, "Anduril developing new drones" = technology

OUTPUT FORMAT (one line per article, no explanation):
<article_index>|<category>|<score>

ARTICLES:
"""
    
    for i, entry in enumerate(entries):
        # Include title + first 200 chars of summary for context
        summary = entry.get('summary', '')[:200].replace('\n', ' ')
        source = entry.get('source', 'Unknown')
        prompt += f"\n{i}. [{source}] {entry['title']}\n   {summary}...\n"
    
    prompt += "\nOUTPUT (one line per article):\n"
    return prompt


def prefilter_prompt(entries, user_profile=""):
    prompt = """You are a geopolitics & finance curator doing a QUICK RELEVANCE FILTER.

For each article below, give:
1. Category (ONE of: geo_major, geo_other, monetary, fiscal, technology, other)
2. Score (0-10): Simple relevance check

CATEGORIES:
- geo_major: US, China, Russia, Europe, Japan, Korea
- geo_other: Middle East, Africa, Latin America, South/Southeast Asia
- monetary: Gold, Bitcoin, currencies, commodities
- fiscal: Government debt/spending (US, Japan, Europe)
- technology: AI in physical world (robotics, drones, warfare, autonomous systems)
- other: Everything else

SCORING (KEEP IT SIMPLE):
8-10: Clearly relevant geopolitics/finance
5-7: Probably relevant, needs closer look
3-4: Marginal relevance
0-2: Off-topic noise (entertainment, sports, local news)
""" + user_profile + """
OUTPUT FORMAT (one line per article):
<index>|<category>|<score>

ARTICLES:
"""
    
    for i, entry in enumerate(entries):
        summary = entry.get('summary', '')[:200].replace('\n', ' ')
        source = entry.get('source', 'Unknown')
        prompt += f"\n{i}. [{source}] {entry['title']}\n   {summary}...\n"
    
    prompt += "\nOUTPUT:\n"
    return prompt


def sonnet_prompt(entries, user_profile=""):
    prompt = """You are an expert geopolitics & finance curator. These articles passed initial relevance filter. Now rank them by QUALITY and CHALLENGE-FACTOR.

For each article, give a SINGLE score (0-10) based on:

**QUALITY SIGNALS (50%):**
- Original analysis vs. news aggregation
- Data-driven insights
- Expert credibility
- Depth of understanding

**CHALLENGE-FACTOR (50%):**
- Challenges conventional wisdom
- Offers contrarian perspective
- Presents underappreciated risks/opportunities
- Connects non-obvious dots

**SCORING GUIDE:**
9-10: Must-read. Deep analysis + challenges assumptions. Changes how you think.
7-8: Strong quality. Either excellent analysis OR interesting challenge perspective.
5-6: Solid but standard. Good information, no surprises.
3-4: Mediocre. Superficial or consensus rehash.
0-2: Weak. Skip.

**EXAMPLES:**
- "Fed raises rates 0.25%" (standard news) → 4
- "Why Fed's rate policy may backfire given fiscal dominance" (challenge) → 8
- "China economy slowing" (consensus) → 5
- "China's regional bank crisis: hidden stress test for Xi's power" (contrarian depth) → 9
""" + user_profile + """
OUTPUT FORMAT (one line per article):
<index>|<score>

ARTICLES:
"""
    
    for i, entry in enumerate(entries):
        summary = entry.get('summary', '')[:300].replace('\n', ' ')
        source = entry.get('source', 'Unknown')
        category = entry.get('category', 'other')
        prompt += f"\n{i}. [{source}] [{category}] {entry['title']}\n   {summary}...\n"
    
    prompt += "\nOUTPUT:\n"
    return prompt


def xai_prompt(entries, user_profile=""):
    override_rules = """You are a sharp, personalized intelligence analyst scoring articles for relevance to the user's interests: finance, geopolitics, risk signals, uncertainty, contrarian views, high-signal sources, commodity/supply-chain dynamics, and intersections that reveal fragility or tail risks.

GROUND TRUTH RULES — PRIORITIZE THESE OVER ANY INDEPENDENT JUDGMENT:

- The USER PROFILE below is absolute ground truth. Source preferences, boosts, and avoids OVERRIDE your own content evaluation.
- If a source or content type appears in the user's boost list or has positive history, score it UP significantly — even if it seems generic, broad, aggregate, or tangential on surface.
- The user deliberately reinforces these; treat them as trusted/high-value.
- Do NOT downrank or dismiss based on "generic form", "aggregate style", or categorical judgment if profile signals support relevance.
- Explicit domain rule: Commodity/supply-chain disruptions, dependencies (e.g., Brazil soy → China), environmental intersections (e.g., deforestation links), or trade rerouting = high-priority geopolitical/finance risk signals. Weight these UP when they align with profile topics.
- Use your reasoning to uncover nuanced alignments, hidden implications, contrarian angles, or overlooked risks/opportunities WITHIN the profile's priorities — not to override the profile.
- For borderline cases, err toward higher relevance if profile or domain rules apply.
- Score on the usual scale, applying overrides.

USER PROFILE (ground truth):
""" + user_profile + """

CATEGORIES:
- geo_major: US, China, Russia, Europe, Japan, Korea (deployed/operational context)
- geo_other: Middle East, Africa, Latin America, South/Southeast Asia  
- monetary: Gold, Bitcoin, currencies, commodities, exchange rates
- fiscal: Government debt, spending, budgets, deficits
- technology: R&D, manufacturing, dual-use DEVELOPMENT (not yet deployed)
- other: Everything else

SCORE GUIDANCE:
9-10: Critical developments, major policy shifts, must-read analysis
7-8: Important trends, significant geopolitical/financial analysis
5-6: Relevant but not urgent, decent background
3-4: Tangential interest, minor relevance
0-2: Skip (noise, spam, off-topic, pure entertainment)

OUTPUT FORMAT (one line per article, no explanation):
<article_index>|<category>|<score>

ARTICLES:
"""
    
    prompt = override_rules
    for i, entry in enumerate(entries):
        summary = entry.get('summary', '')[:200].replace('\n', ' ')
        source = entry.get('source', 'Unknown')
        prompt += f"\n{i}. [{source}] {entry['title']}\n   {summary}...\n"
    
    prompt += "\nOUTPUT (one line per article):\n"
    return prompt
