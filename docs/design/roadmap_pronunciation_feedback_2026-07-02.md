# Roadmap Thinking: Pronunciation Feedback Layer

*Created: 2026-07-02 · Claude.ai (design/strategy)*
*Status: Thinking document — not a spec, not a build plan*
*Scope: German (Mein Deutsch) + Portuguese (Meu Português) — both domains, siblings, same architecture*

---

## What this is

A roadmap for adding pronunciation feedback to the language domains. Not conversations — words and phrases from the drill/vocabulary section only. Quality-first, small scope, extensible over time as tools improve.

The goal: a learner says a word or phrase, hears the correct pronunciation played back by a persona, gets feedback on what was wrong, and can drill until it's right. Optional future: hear your own voice as the playback model.

---

## Why now (and why bounded)

The conversation infrastructure (VAD, Whisper, TTS) already exists in Mein Deutsch Gespräche. What doesn't exist is the pronunciation *evaluation* layer — the thing that compares what you said to what you should have said and tells you specifically what was wrong.

Keeping scope to words and phrases from Wörter/Palavras (not full conversations) makes the evaluation problem tractable. A word has one correct pronunciation. A conversation has infinite valid variations. Start where the answer is clear.

---

## The three layers

### Layer 1 — Capture and transcription (largely exists)

VAD captures audio. Whisper transcribes. This pipeline is live in German Gespräche. Portuguese Conversas has voice too. What needs to happen:

- Confirm the pipeline is consistent between German and Portuguese
- Expose it from the Wörter/Palavras drill section (currently only accessible from Gespräche/Conversas)
- Add a "say this word" prompt and a record button to the drill UI

### Layer 2 — Pronunciation evaluation (the new piece)

Whisper tells you *what* you said, not *how* you said it. Evaluation requires something more. Two approaches:

**Option A — Phoneme-level comparison (precise, complex)**
Compare your audio against a reference pronunciation using a dedicated speech/pronunciation model (e.g. Montreal Forced Aligner, wav2vec2, Speechace API). Gives phoneme-level detail: "your /r/ in 'Straße' sounds like an English /r/, should be uvular." High quality, higher complexity, requires a hosted API or local model.

**Option B — LLM-assisted evaluation (simpler, good enough)**
Feed Whisper's transcript + the target word to an LLM. Ask: "The learner was trying to say X. Whisper heard Y. What pronunciation errors likely caused this difference?" Surprisingly effective for systematic errors (English /r/ vs German /r/, nasal vowels in Portuguese). Not phoneme-precise but actionable for a personal tool.

**Recommendation for Phase 1: Option B.** Simpler to build, runs on existing infrastructure, and the LLM can give culturally-informed feedback ("this is a common mistake for English speakers") that a phoneme model can't. Option A is the upgrade path when precision matters more.

**Future Option C — Audio-native LLM evaluation (closer than it appears)**
GPT-4o audio mode and Gemini audio can already evaluate pronunciation directly from audio input — not just transcript. The gap is cost and API stability. Design Phase 1 to store raw audio so it can be passed directly to an audio-native LLM for re-evaluation without asking the user to re-record. Option C may arrive during Phase 2, not Phase 3.

### Layer 3 — Drill and repeat loop

- Save flagged words/phrases to a pronunciation library (extension of Wörter/Palavras)
- Play correct pronunciation via TTS (persona voice — Maria, Carlos, etc. for Portuguese; Vienna personas for German)
- Record learner attempt
- Evaluate and give specific feedback
- Repeat until confidence threshold reached or learner marks as learned

This is spaced repetition for audio — same logic as the existing drill system, different modality.

---

## Persona playback

The persona reads back the correct pronunciation. Maria corrects your "padaria," Stefan corrects your "Straße." The persona voice is already defined (TTS voice per persona). No new infrastructure needed — it's a TTS call with the persona's voice setting.

Benefits: feels like a tutor, not a robot. Consistent with the existing character of the domains.

---

## Voice cloning — future option

Robert asked for the option to hear your own voice as the playback model. This is the right long-term direction and should be designed for now, not retrofitted later.

**Current state of tools (mid-2026):**
- ElevenLabs voice cloning: mature, API-accessible, ~1 minute of audio to clone, good quality
- OpenAI voice cloning: in development, not yet production-ready
- Local voice cloning (XTTS, Tortoise): possible, compute-intensive, quality improving rapidly
- Privacy consideration: voice is biometric data — stored locally, never sent to a cloud service without explicit consent

**Design principle:** store a short voice sample per user (10–30 seconds), use it to condition TTS when the user opts into "hear it in my voice." Persona voice is the default; own-voice is opt-in.

**Why not build it now:** the tools are improving fast. Audio-native LLMs will likely make this much cheaper and higher quality by late 2026. Design the data model now, build the feature when the right tool exists.

---

## Phased roadmap

### Phase 0 — Research (before any build)

Answer these questions before committing to an approach:

1. **Whisper accuracy on single words vs. phrases.** Whisper is optimized for continuous speech. Does it reliably transcribe single words, or does it hallucinate or skip them?
2. **Which LLM gives the best pronunciation error analysis?** The evaluation prompt needs testing across Claude, GPT, and Grok. This is a prompt engineering and model evaluation question.
3. **Phoneme models for Option A upgrade:** Speechace API, wav2vec2, Azure Speech pronunciation assessment, CMU Sphinx / Montreal Forced Aligner
4. **Audio-native LLMs for Option C:** GPT-4o audio mode (current cost/quality), Gemini audio, future Claude audio
5. **Voice cloning tools for Phase 4:** ElevenLabs instant cloning API, XTTS local, OpenAI timeline
6. **Integration point in current architecture:** Can the VAD/Whisper pipeline be called from Wörter/Palavras without duplicating code? Does the drill UI need a new tab or can it extend existing Wörter? German and Portuguese must share the same pronunciation pipeline.
7. **Audio-native LLM current state:** What do GPT-4o audio mode and Gemini audio cost per evaluation, and how does quality compare to Option B?

Output: recommendation on Option A vs B vs C for Phase 1, tool choices, cost estimates, integration approach.

### Phase 1 — Minimal viable pronunciation drill

- Expose voice capture from Wörter/Palavras drill section (record button + "say this word" prompt)
- Whisper transcription of learner attempt
- LLM evaluation (Option B) — what was wrong, what to focus on
- **Store raw audio file with every evaluation record** — cheap insurance for re-evaluation with better models
- Persona TTS playback of correct pronunciation
- Save evaluation record to per-user JSON (with audio file path)
- Both German and Portuguese, same architecture

**Not in Phase 1:** phoneme-level analysis, voice cloning, conversation pronunciation, mobile-optimized recording UI

### Phase 2 — Drill loop + spaced repetition

- Pronunciation library (words/phrases flagged for drill)
- Repeat until confidence threshold
- Track improvement over time in Archiv/Arquivo
- Integrate with existing Wörter/Palavras saved vocabulary
- Decision point: evaluate whether hybrid evaluation (LLM + phoneme, or audio-native LLM) is worth adding based on Phase 1 quality

### Phase 3 — Precision evaluation upgrade

- Evaluate Option A tools (Speechace, wav2vec2, Azure)
- Upgrade from LLM-estimated errors to phoneme-level feedback where justified
- Keep Option B as fallback for languages/features not covered

### Phase 4 — Own-voice playback (when tools are ready)

- Voice sample capture (10–30 seconds, per user, stored locally)
- Voice cloning opt-in flag in user profile
- TTS conditioned on user's voice sample for correct-pronunciation playback
- Privacy: local storage, explicit consent, never auto-uploaded
- Trigger: when a tool reaches quality + cost threshold worth building on

---

## Architecture principles

- **Per-user data** — all pronunciation records, audio files, and voice samples keyed by user ID. No shared files.
- **JSON-first** — evaluation records in per-user JSON, audio files on disk with path references. Postgres/Neo4j as rebuildable projections.
- **Model-agnostic** — evaluation LLM, TTS provider, and future phoneme model are all swappable. No hard coupling to any vendor.
- **Store audio for future re-evaluation** — don't discard recordings. Better models will come.
- **Private/local voice is a binding constraint from Phase 1.** Voice is biometric data. Stored locally per user, never auto-uploaded, explicit opt-in consent required for any cloud processing, user-controlled deletion.
- **German and Portuguese as siblings** — same pipeline, same data model, same UI pattern, different language content.

---

## Open questions for design session

1. Where does the pronunciation drill live in the UI — a new sub-tab in Wörter/Palavras, or integrated into the existing drill flow?
2. How many words/phrases in a session before fatigue? (Probably 5–10 max)
3. Should the persona give encouragement as well as correction — stay in character, or switch to a more clinical feedback mode?
4. How do we handle words the learner saves from Leitura/Lesen vs. words manually added — both should be drill-eligible?
5. What's the minimum audio sample length for acceptable voice cloning quality in Phase 4?

---

## Relationship to existing design threads

- **Memory/intent layer** — pronunciation evaluation records belong in the learning archetype's per-user memory. "User consistently mispronounces uvular /r/ in German" is exactly the kind of insight the learning memory layer should capture and surface at session start.
- **Dual correction (literal/natural)** — pronunciation has the same dual-layer structure: "technically correct" vs "how a native speaker actually says it." Same design pattern as existing conversation feedback.
- **Seed personas** — pronunciation persona voice should be consistent with the seed persona's character. Maria doesn't sound like Dr. Huber.

---

*Thinking document · 2026-07-02 · Claude.ai · not a spec · research phase first · pairs with spec queue for language domains*
*GitHub: [Issue #71](https://github.com/robertvanstedum/personal-ai-agents/issues/71)*
