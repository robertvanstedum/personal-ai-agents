# Manual Review — Session 2026-04-19
**Persona:** Frau Berger  
**Scenario:** bakery_order  
**Reviewed by:** Claude.ai (manual, pre-pipeline)  
**Purpose:** Golden test case reference — use to validate reviewer.py output quality

---

## Overall

Really solid first session. Stayed in German throughout, completed a full transaction, and used natural hesitation ("ähm") rather than switching to English when uncertain. The conversation has real flow.

---

## Strengths

- "Ich möchte ein Brot kaufen" — correct and natural
- "vier Brötchen, bitte" — clear, polite, got the job done
- "zwei Stück, bitte" — perfect
- "mit Karte" — exactly right, this is what you'd say in Vienna
- "Dankeschön. Bis später" — natural closing
- Stayed in character throughout without requesting English
- Handled authentic Viennese dialect (Semmerl, Powidltascherl, Magst) without flinching

---

## Errors

### 1. Missing adjective ending (vocabulary / grammar)
- **What Robert said:** "muss da etwas süß"
- **Correct form:** "Ich möchte etwas Süßes dazu" or "Haben Sie etwas Süßes?"
- **Explanation:** When an adjective is used as a noun in German, it takes a strong ending. *Süßes* not *süß*.
- **Error type:** `vocabulary` (adjective-as-noun ending)

### 2. Missed hätte gerne construction (verb_conjugation)
- **Context:** When asked "Welches Brot hättest du gern?" Robert described wanting something rather than using the standard polite order form.
- **Correct form:** "Ich hätte gern das Bauernbrot, bitte."
- **Explanation:** *hätte gerne* is the standard polite ordering construction in Austrian shops, cafés, and restaurants. Should become automatic.
- **Error type:** `verb_conjugation`

---

## Vocabulary Highlights

| German | English | Note |
|--------|---------|------|
| das Semmerl | crusty bread roll (Austrian) | Diminutive of Semmel — distinctly Viennese |
| das Roggenbrot | rye bread | Used correctly in context |
| das Bauernbrot | farmhouse bread | Softer, wheat-heavy — explained by Frau Berger |
| der Mohnkuchen | poppy seed cake | Robert asked correctly with "haben Sie" |
| das Nusskipferl | walnut crescent pastry | Austrian bakery staple |
| das Powidltascherl | plum jam pastry pocket | Distinctly Viennese — worth knowing |
| Zahlen Sie bar oder mit Karte? | Cash or card? | Standard phrase at any Austrian till |

---

## Next Focus

Practice *hätte gerne* until it is automatic. It is the single most useful phrase for every shop, café, and restaurant interaction in Vienna. Drill: "Ich hätte gern einen Kaffee, bitte." / "Ich hätte gern das Bauernbrot." / "Ich hätte gern zwei Semmerln."

---

## Notes for reviewer.py Validation

When `reviewer.py` runs against `2026-04-19_frau_berger_bakery_raw.txt`, the output should:
- Identify at minimum 1–2 errors (adjective ending on süß, missed hätte gerne)
- Include Semmerl, Mohnkuchen, Powidltascherl in vocabulary highlights
- Flag next_focus on hätte gerne construction
- Overall summary should be positive — this was a good session

If reviewer.py misses all errors or produces an empty vocabulary list, the prompt needs tuning.
