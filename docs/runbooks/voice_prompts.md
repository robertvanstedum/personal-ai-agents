# Where the voices' words live

Robert's MC and CoS voices read rules plus a versioned card. Change them here, not in a summary somewhere else.

**The direct path (Dev and production).** `direct_host.compose_instructions`: rules, then card, then data.

- Cards: MC `services/mc_operations/personas/mc-chat/releases/<v>/voice.json`, CoS `domains/cos/voice/releases/<v>/voice.json`.
  Each folder's `pin.json` names the release in use. Manner, language and pronunciation belong on the card.
- Rules: MC `DIRECT_INSTRUCTIONS` in `minimoi_portal/guild_ui/mc_voice_direct.py`; CoS `_RULES` in
  `domains/cos/direct_voice.py`; shared by both, `direct_host.direct_rules`. Confirmation and receipts belong here.

**The managed path only.** `VOICE_MANNER` in `controlled_config.py` is added by `configuration()` to managed sessions. The
direct path never reads it. Its clauses reach the direct path restated on the cards (Spec 165 §6.2); no sentence is copied
word for word, and `tests/cos/test_cos_direct_voice.py` and `tests/test_voice_card.py` hold that.

**Make a card release.**

1. Copy the current release folder to the next version; change the card text. CoS: `voice.json` and `manifest.json`.
   MC: also `AGENTS.md`'s header version and a new `CHANGES.md`.
2. In `manifest.json` set the version, status `candidate`, a spec line saying why, and the SHA-256 of every changed file. A
   card that does not match its manifest is refused.
3. Move the pin. MC has more names for one release: `pin.json`, `docker/mc-agent-container/Dockerfile` (COPY and label) and
   `PERSONA` plus the label in `scripts/staging/beta_dev_deploy.py`. `tests/mc_persona` holds them together.
4. Update the version names and text in the card tests, then deploy on Robert's word.

**Roll back.** Move the same names back to the earlier release. Old releases stay; never edit one, make a new one.
