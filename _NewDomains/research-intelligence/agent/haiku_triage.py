#!/usr/bin/env python3
"""
Haiku triage for Session 001 source candidates.
Model: claude-haiku-3-5-20241022
"""

import json
import sys

# Get API key from openclaw auth profiles
with open('/Users/vanstedum/.openclaw/auth-profiles.json') as f:
    auth = json.load(f)
api_key = auth['anthropic']['apiKey']

import anthropic
client = anthropic.Anthropic(api_key=api_key)

RESEARCH_TARGETS = """
1. Non-Anglophone takes on China-Russia positional swap
2. Authoritarian loyalty-vs-competence literature (comparative, beyond Stalin)
3. Historical precedents for industrial hollowing (dominant power transferring strategic capacity to rival)
4. Mackinder's actual argument — who has updated or challenged it seriously?
5. Latin American dependency theory angle on current great power competition
"""

candidates = [
    {
        "id": 1,
        "title": "The Loyalty-Competence Trade-Off in Dictatorships and Outside Options for Subordinates",
        "author": "Alexei V. Zakharov",
        "source": "Journal of Politics, 2016",
        "language": "English",
        "abstract": "Why do dictators sometimes prefer incompetent subordinates? This paper uses a formal model to explore the tradeoff. In each period of a dynamic game, a subordinate chooses his level of loyalty, which determines the dictator's survival probability. He also performs another task — such as carrying out policy or running the economy. The dictator chooses whether to retain or fire the subordinate. The model shows that when subordinates have better outside options (e.g., from strong rule of law or market economy), dictators will prefer more loyal but less competent subordinates. This has implications for why authoritarian regimes often underperform economically."
    },
    {
        "id": 2,
        "title": "The competence-loyalty tradeoff in China's political selection",
        "author": "Multiple (ScienceDirect, 2022)",
        "source": "Journal of Comparative Economics, 2022",
        "language": "English",
        "abstract": "The CCP's cadre selection system has changed substantially to reflect leaders' personal preferences and adapt to new conditions. This paper analyzes the tradeoff between competence and loyalty from the perspective of the central controller, studying both city and provincial officials from 1994–2017 — the longest period in the literature. Findings: different periods show different results. Under Xi Jinping, the balance has shifted toward loyalty, with implications for China's economic performance and governance effectiveness."
    },
    {
        "id": 3,
        "title": "Dependency in the Twenty-First Century? The Political Economy of China-Latin America Relations",
        "author": "Barbara Stallings",
        "source": "Cambridge University Press, 2020",
        "language": "English",
        "abstract": "This book asks how Latin American relations with China differ from relations with the United States. Using dependency theory, Stallings finds that China has not used political leverage in its Latin America relationships — at least not in most bilateral exchanges. Instead, China mainly uses economic leverage to access raw materials and markets. Trade with China reached $268 billion at end of 2013 (22x the 2000 level), but remained only 13% of Latin America's total trade. Imports from China (industrial goods) predominate over Latin American exports (natural resources/commodities) to China, creating a trade deficit exceeding $300 billion in 2018. Three causal mechanisms: market relations, leverage, and linkages. The book argues this constitutes a new form of dependency where China has replaced the US as dominant external economic force."
    },
    {
        "id": 4,
        "title": "Dependency Theory and the Critique of Neodevelopmentalism in Latin America",
        "author": "Mariano Treacy",
        "source": "Latin American Perspectives (SAGE), 2022",
        "language": "English",
        "abstract": "A number of writers have pointed to the importance of recovering aspects of dependency theory to explain the persistence of Latin American underdevelopment after 40 years of neoliberal hegemony and profound change and crisis in the world economy. This article argues that dependency theory, properly updated, can explain patterns of underdevelopment that are missed by neodevelopmentalist frameworks. The critique focuses on how center-periphery dynamics persist even under left-leaning governments, and how the emergence of China as a new hegemonic force does not fundamentally alter dependent relationships — it merely shifts their direction."
    },
    {
        "id": 5,
        "title": "Geopolitics and Political Geography in Russia: Global Context and National Characteristics",
        "author": "Multiple Russian scholars",
        "source": "Geography and Sustainability (MDPI), 2022",
        "language": "English (reviews Russian-language scholarship)",
        "abstract": "Against the backdrop of global trends, this paper reviews main directions and research results in geopolitics and political geography in Russia 2011–2021. Particular attention is paid to geopolitical publications about the pivot of Russian foreign policy to the East and the Greater Eurasia concept. Includes discussion of K.S. Gadzhiev's critique of Mackinder in 'Introduction to Geopolitics' (Vvedenie v geopolitiku) which argues that Mackinder's privileging of physiography for political strategy is a form of geographical determinism. The paper charts how Russian scholars have engaged with and diverged from Western geopolitical frameworks."
    },
    {
        "id": 6,
        "title": "Mackinder's 'heartland' — legitimation of US foreign policy in World War II and the Cold War of the 1950s",
        "author": "Oliver Krause",
        "source": "Geographische Historische (Copernicus Open Access), 2023",
        "language": "English (German institutional author, draws on German Geopolitik literature)",
        "abstract": "This paper examines how Mackinder's heartland theory was used to legitimate US foreign policy during WWII and the early Cold War. Written from a German geography perspective, it draws on the tradition of German Geopolitik (Haushofer, Ratzel) and critical geopolitics to argue that the heartland concept was not a neutral geographic analysis but a tool of policy legitimation. The paper is notable for bringing the German critical geography tradition to bear on Anglo-American geopolitical theory, and for documenting how ideas travel between geopolitical traditions."
    },
    {
        "id": 7,
        "title": "Hegemony in Eurasia, the Joining of the Euro-Atlantic and Indo-Pacific, and the Enduring Relevance of Mackinder's Heartland Theory",
        "author": "Brendon J. Cannon",
        "source": "SSRN preprint, 2025",
        "language": "English",
        "abstract": "Halford Mackinder's century-old geopolitical theories retain striking relevance for contemporary international relations and security. His identification of Eurasia's 'Heartland' and 'pivot area' forms a foundation for understanding today's emerging security architecture linking the Euro-Atlantic and Indo-Pacific regions. This paper argues that the Russia-China partnership, combined with their combined influence over the Eurasian heartland, constitutes a contemporary manifestation of what Mackinder feared most: a land-based empire capable of challenging seapower dominance. Colin Gray's earlier prediction that China might compensate for Russia's weakness has been borne out."
    },
    {
        "id": 8,
        "title": "How Chinese Strategists View, Understand, and Contend with Russia's Strategic Space",
        "author": "Multiple (NBR Strategic Space project)",
        "source": "National Bureau of Asian Research, 2024",
        "language": "English (analyzes Chinese-language scholarship)",
        "abstract": "This essay focuses on the rationale for China-Russia cooperation as seen by Chinese scholars assessing Russia's strategic use for China's rise to global dominance. Chinese strategic thinkers view Russia not as an equal partner but as a 'swing state' whose value to China lies in its ability to distract and constrain the United States. The essay identifies key Chinese scholars engaging with this question and maps their frameworks, which diverge sharply from Western IR assumptions about the China-Russia relationship. Distinctly, Chinese analysts see the partnership as instrumentally valuable but asymmetric — with China as the rising core and Russia as a useful but declining periphery."
    },
    {
        "id": 9,
        "title": "Latin American Critical Thought: Theory and Practice",
        "author": "CLACSO (committee compilation)",
        "source": "CLACSO Sur-Sur series, 2006",
        "language": "English/Spanish",
        "abstract": "Latin American critical thought is resurfacing after the long period of decline following the impasse of dependency theory in the 1970s and the intellectual domination of neoliberalism. This anthology recovers the intellectual foundations of hegemony critique from a Latin American perspective. The volume includes essays on how center-periphery dynamics continue to structure global political economy, and how the US-led liberal order has reproduced structural inequalities. The CLACSO framework explicitly frames dependency not as historical artifact but as a continuing structural condition — one that is being reconfigured, not dissolved, by the rise of China."
    },
    {
        "id": 10,
        "title": "Dependency, Neoliberalism and Globalization in Latin America",
        "author": "Carlos Eduardo Martins",
        "source": "Brill / Studies in Critical Social Sciences, 2019",
        "language": "English (translated from Portuguese original)",
        "abstract": "A Brazilian CLACSO-linked scholar applying updated dependency theory to globalization. Emir Sader (CLACSO Secretary General) writes: 'La TMD sale enriquecida y renovada de esta obra de Carlos Eduardo Martins dedicada a pensar el capitalismo bajo la perspectiva del anticapitalismo' — 'The theory of Marxist dependency emerges enriched and renewed from this work dedicated to thinking capitalism from the perspective of anti-capitalism.' Martins argues that the current moment of great power competition between the US and China does not represent a 'decoupling' from dependency structures but rather their intensification under new hegemonic conditions. Latin American countries remain periphery regardless of which core power dominates."
    },
]

TRIAGE_PROMPT = """Score this source 1-5 for relevance to the following research targets. 1=not relevant, 3=useful context, 5=directly addresses the question. Return ONLY a JSON object with keys: score (int 1-5), targets (list of target numbers as ints), explanation (one sentence string).

Research targets:
1. Non-Anglophone takes on China-Russia positional swap
2. Authoritarian loyalty-vs-competence literature (comparative, beyond Stalin)
3. Historical precedents for industrial hollowing (dominant power transferring strategic capacity to rival)
4. Mackinder's actual argument — who has updated or challenged it seriously?
5. Latin American dependency theory angle on current great power competition

Source: {title} by {author} ({source})
Abstract: {abstract}

Return only valid JSON, no other text."""

results = []
total_input_tokens = 0
total_output_tokens = 0

for c in candidates:
    prompt = TRIAGE_PROMPT.format(
        title=c['title'],
        author=c['author'],
        source=c['source'],
        abstract=c['abstract']
    )
    
    response = client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    
    total_input_tokens += response.usage.input_tokens
    total_output_tokens += response.usage.output_tokens
    
    raw = response.content[0].text.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        parsed = json.loads(match.group()) if match else {"score": 0, "targets": [], "explanation": "parse error: " + raw[:100]}
    
    results.append({
        "id": c['id'],
        "title": c['title'],
        **parsed
    })
    print(f"#{c['id']} score={parsed.get('score')} targets={parsed.get('targets')} | {parsed.get('explanation','')[:80]}")

# Cost estimate: ~$0.00025/1K input, $0.00125/1K output for Haiku
cost = (total_input_tokens / 1000 * 0.00025) + (total_output_tokens / 1000 * 0.00125)
print(f"\nTokens: {total_input_tokens} in / {total_output_tokens} out | Est. cost: ${cost:.4f}")

# Save results
with open('/tmp/triage_results.json', 'w') as f:
    json.dump({"results": results, "cost": cost, "input_tokens": total_input_tokens, "output_tokens": total_output_tokens}, f, indent=2)

print("Results saved to /tmp/triage_results.json")
