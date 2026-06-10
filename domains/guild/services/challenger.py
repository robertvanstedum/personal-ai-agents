"""
domains/guild/services/challenger.py — Synthesizer + Challenger service

Shared across all domains. Caller provides the first_pass (Round 1).
This service runs Round 2 (Grok challenge) and Round 3 (Claude final review),
stores the exchange to guild.challenger_exchanges, returns a ChallengerResult.

Usage:
    from domains.guild.services.challenger import ChallengerService

    svc = ChallengerService()
    result = svc.run(
        domain="curator_deep_dive",
        feature="deeper_dive",
        first_pass="...synthesis text...",
        context={"topic_name": "strait-of-hormuz", "sources_summary": "...", ...}
    )
    if result.enabled:
        final_text = result.final
        show_digest = result.show_process
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("challenger")

BASE_DIR    = Path(__file__).resolve().parents[3]
CONFIG_PATH = BASE_DIR / "domains/guild/config/challenger_config.json"


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ChallengerResult:
    enabled: bool = False
    show_process: bool = False

    # Round outputs
    first_pass: str = ""
    challenge_raw: str = ""           # raw JSON string from Grok
    final_raw: str = ""               # raw JSON string from Claude
    final: str = ""                   # final synthesis text

    # Parsed exchange
    challenge_points: list[dict] = field(default_factory=list)
    key_change: str = ""

    # Counts
    challenged_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0

    # Change detection
    outputs_differ: bool = False

    # DB
    exchange_id: Optional[int] = None

    # Meta
    primary_model: str = ""
    challenger_model: str = ""
    prompt_version: str = ""
    error: Optional[str] = None


# ── ChallengerService ─────────────────────────────────────────────────────────

class ChallengerService:
    """
    Runs the two additional LLM rounds for the Synthesizer + Challenger pattern.

    Thread-safe for concurrent domain calls. Config is loaded once at init.
    Degrades gracefully: if either API call fails, returns first_pass unchanged.
    """

    def __init__(self, config_path: Path = CONFIG_PATH):
        self._cfg: dict = {}
        self._load_config(config_path)

    def _load_config(self, path: Path) -> None:
        try:
            raw = json.loads(path.read_text())
            self._cfg = raw.get("challenger", {})
            log.debug("ChallengerService config loaded from %s", path)
        except Exception as e:
            log.error("ChallengerService: config load failed: %s", e)
            self._cfg = {}

    def _domain_cfg(self, domain: str) -> dict:
        return self._cfg.get("domains", {}).get(domain, {})

    def _get_prompt(self, prompt_key: str, variables: dict) -> str:
        """
        Fetch a prompt by key from config and substitute known {variables}.
        Uses targeted string replacement rather than .format() so that JSON
        examples in prompt text (e.g. {"type": "..."}) are left untouched.
        """
        template = self._cfg.get("prompts", {}).get(prompt_key, "")
        if not template:
            raise ValueError(f"Prompt key not found in config: {prompt_key!r}")
        result = template
        for key, value in variables.items():
            result = result.replace(f"{{{key}}}", str(value))
        return result

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        domain: str,
        feature: str,
        first_pass: str,
        context: dict,
        entity_id: Optional[int] = None,
        entity_description: Optional[str] = None,
    ) -> ChallengerResult:
        """
        Run a full challenger exchange. Returns ChallengerResult.

        If domain is disabled, returns immediately with result.enabled=False
        and result.final = first_pass — caller code path is unchanged.

        context keys vary by domain — see challenger_config.json prompt templates
        for required variables per domain.
        """
        result = ChallengerResult(first_pass=first_pass)
        dcfg = self._domain_cfg(domain)

        if not dcfg.get("enabled", False):
            result.final = first_pass
            return result

        result.enabled      = True
        result.show_process = dcfg.get("show_process", False)
        result.primary_model     = self._cfg.get("primary_model", "claude-sonnet-4-6")
        result.challenger_model  = self._cfg.get("challenger_model", "grok-4-1-fast-reasoning")
        result.prompt_version    = dcfg.get("challenger_prompt", "")

        try:
            # Round 2 — Grok challenge
            challenger_prompt_key = dcfg.get("challenger_prompt", "")
            challenge_vars = {**context, "first_pass": first_pass}
            challenger_prompt = self._get_prompt(challenger_prompt_key, challenge_vars)

            result.challenge_raw = self._call_xai(
                result.challenger_model, challenger_prompt
            )
            result.challenge_points = self._parse_challenge_points(result.challenge_raw)
            result.challenged_count = len(result.challenge_points)

            # Round 3 — Claude final review
            final_prompt_key = dcfg.get("final_review_prompt", "")
            challenges_text = json.dumps(result.challenge_points, indent=2)
            final_vars = {
                **context,
                "first_pass": first_pass,
                "challenges": challenges_text,
                "n": len(result.challenge_points),
            }
            final_prompt = self._get_prompt(final_prompt_key, final_vars)

            result.final_raw = self._call_anthropic(
                result.primary_model, final_prompt
            )
            self._parse_final(result)

        except Exception as e:
            log.error("ChallengerService: exchange failed for %s/%s: %s", domain, feature, e)
            result.error = str(e)
            result.final = first_pass   # degrade gracefully
            return result

        # Change detection
        result.outputs_differ = (
            self._hash(first_pass) != self._hash(result.final)
        )

        # Store to DB
        if self._cfg.get("store_exchanges", True):
            result.exchange_id = self._store(
                domain=domain,
                feature=feature,
                entity_id=entity_id,
                entity_description=entity_description,
                result=result,
            )

        return result

    # ── LLM calls ─────────────────────────────────────────────────────────────

    def _call_xai(self, model: str, prompt: str) -> str:
        """Round 2: Grok via xAI (challenger pass)."""
        import keyring
        from openai import OpenAI
        api_key = keyring.get_password("xai", "api_key")
        client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=800,
        )
        return (resp.choices[0].message.content or "").strip()

    def _call_anthropic(self, model: str, prompt: str) -> str:
        """Round 3: Claude via Anthropic (final review)."""
        import keyring
        import anthropic
        api_key = keyring.get_password("anthropic", "api_key")
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=1200,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse_challenge_points(self, raw: str) -> list[dict]:
        """Parse Grok's JSON array response into challenge_points list."""
        try:
            # Strip markdown fences if present
            text = raw.strip()
            if "```" in text:
                text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
            points = json.loads(text)
            if not isinstance(points, list):
                return []
            # Normalise each point
            clean = []
            for p in points:
                if isinstance(p, dict):
                    clean.append({
                        "type":        p.get("type", "unknown"),
                        "description": p.get("description", ""),
                        "impact":      p.get("impact", "medium"),
                        "accepted":    False,   # set in _parse_final
                    })
            return clean
        except Exception as e:
            log.warning("Failed to parse challenge points: %s — raw: %s", e, raw[:200])
            return []

    def _parse_final(self, result: ChallengerResult) -> None:
        """
        Parse Claude's JSON response into result fields.
        Handles both generic {decisions, final} and domain-specific variants.
        Updates result in place.
        """
        raw = result.final_raw
        try:
            text = raw.strip()
            if "```" in text:
                text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
            data = json.loads(text)

            # Apply accept/reject decisions to challenge_points
            decisions = data.get("decisions", [])
            accepted = 0
            rejected = 0
            for dec in decisions:
                idx = dec.get("challenge_index", -1)
                if 0 <= idx < len(result.challenge_points):
                    result.challenge_points[idx]["accepted"] = bool(dec.get("accepted", False))
                    if dec.get("accepted"):
                        accepted += 1
                    else:
                        rejected += 1

            result.accepted_count = accepted
            result.rejected_count = rejected

            # Extract final text — key varies by domain
            for key in ("final", "final_analysis", "final_corrected_german"):
                if key in data:
                    result.final = data[key]
                    break
            else:
                result.final = result.first_pass   # fallback

            # key_change: first accepted high-impact point, else first accepted
            high_accepted = [
                p for p in result.challenge_points
                if p.get("accepted") and p.get("impact") == "high"
            ]
            any_accepted = [p for p in result.challenge_points if p.get("accepted")]
            source = high_accepted or any_accepted
            result.key_change = source[0]["description"][:120] if source else ""

            # Capture domain-specific extras as attributes
            for extra in ("nuance_note", "nuance_notes", "final_english_translation"):
                if extra in data:
                    setattr(result, extra, data[extra])

        except Exception as e:
            log.warning("Failed to parse final review: %s — using first_pass", e)
            result.final = result.first_pass

    # ── DB storage ────────────────────────────────────────────────────────────

    def _store(
        self,
        domain: str,
        feature: str,
        entity_id: Optional[int],
        entity_description: Optional[str],
        result: ChallengerResult,
    ) -> Optional[int]:
        """Write exchange to guild.challenger_exchanges. Returns row id or None."""
        try:
            import psycopg2
            conn = psycopg2.connect(
                "postgresql://minimoi:simple123@localhost:5432/personal_agents"
            )
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO guild.challenger_exchanges (
                        domain, feature, entity_id, entity_description,
                        primary_model, challenger_model,
                        first_pass_summary, challenge_points, key_change,
                        challenged_count, accepted_count, rejected_count,
                        show_process_flag, prompt_version,
                        first_pass_hash, final_hash, outputs_differ
                    ) VALUES (
                        %s,%s,%s,%s, %s,%s, %s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s
                    ) RETURNING id""",
                    (
                        domain, feature, entity_id, entity_description,
                        result.primary_model, result.challenger_model,
                        result.first_pass[:200],          # summary only
                        json.dumps(result.challenge_points),
                        result.key_change,
                        result.challenged_count, result.accepted_count, result.rejected_count,
                        result.show_process, result.prompt_version,
                        self._hash(result.first_pass), self._hash(result.final),
                        result.outputs_differ,
                    )
                )
                row_id = cur.fetchone()[0]
            conn.commit()
            conn.close()
            return row_id
        except Exception as e:
            log.debug("DB store failed (non-fatal): %s", e)
            return None

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]
