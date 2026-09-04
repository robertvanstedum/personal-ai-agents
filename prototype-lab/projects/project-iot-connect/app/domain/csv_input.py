from __future__ import annotations

import csv
import io
from collections import Counter

from .catalog import RATE_PLANS
from .errors import ValidationError


REQUIRED_COLUMNS = {"source_subscription_ref", "sim_id", "rate_plan_id"}


def parse_subscription_csv(raw_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(raw_text))
    if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
        raise ValidationError(
            "CSV columns must include source_subscription_ref, sim_id, and rate_plan_id"
        )

    rows: list[dict] = []
    source_refs: set[str] = set()
    sim_ids: set[str] = set()
    for line_number, source in enumerate(reader, start=2):
        row = {key: (value or "").strip() for key, value in source.items()}
        source_ref = row["source_subscription_ref"]
        sim_id = row["sim_id"]
        plan_id = row["rate_plan_id"]
        if not source_ref or not sim_id:
            raise ValidationError(
                f"Line {line_number}: source_subscription_ref and sim_id are required"
            )
        if source_ref in source_refs:
            raise ValidationError(
                f"Line {line_number}: duplicate source_subscription_ref {source_ref}"
            )
        if sim_id in sim_ids:
            raise ValidationError(f"Line {line_number}: duplicate sim_id {sim_id}")
        if plan_id not in RATE_PLANS or RATE_PLANS[plan_id]["status"] != "ACTIVE":
            raise ValidationError(f"Line {line_number}: unknown active rate_plan_id {plan_id}")
        source_refs.add(source_ref)
        sim_ids.add(sim_id)
        rows.append(
            {
                "source_subscription_ref": source_ref,
                "sim_id": sim_id,
                "rate_plan_id": plan_id,
            }
        )

    if not rows:
        raise ValidationError("CSV contains no subscription rows")
    return rows


def plan_counts(rows: list[dict]) -> dict[str, int]:
    return dict(sorted(Counter(row["rate_plan_id"] for row in rows).items()))

