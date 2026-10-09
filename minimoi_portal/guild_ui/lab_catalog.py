"""The Prototype Lab catalog (Guild 1.1, 5 Oct).

One entry per prototype, as data, so a new prototype is a new entry and not a
new page. Each entry carries what the page shows: a title, the owner's short
description, a cover image, the real screenshots it has, links to its own
documents on GitHub, and a run book for starting it on this Mac. Nothing here
starts, stops or checks a demo, and nothing here claims one is running:
availability of a hosted copy is not known to this page.

Screenshots are only ever real ones, shipped in static/prototypes/<slug>/.
Where a prototype has none of the application itself, the page says so.
"""
from __future__ import annotations

REPO = "robertvanstedum/personal-ai-agents"
GITHUB = f"https://github.com/{REPO}"


def blob(path: str) -> str:
    return f"{GITHUB}/blob/main/{path}"


def tree(path: str) -> str:
    return f"{GITHUB}/tree/main/{path}"


_IOT = "prototype-lab/projects/project-iot-connect"

IOT_CONNECT = {
    "slug": "iot-connect",
    "title": "IoT Connect",
    "subtitle": "Enterprise Connectivity Management",
    "icon": "C",
    "kind": "demo",
    "status": "Proof of concept",
    "description": ("IoT Connect is a full-stack BSS/OSS platform for connectivity services. This POC runs a real "
                    "database, internal APIs, synthetic external APIs, and condensed order-management and billing workflows."),
    "repo_path": _IOT,
    # A hosted copy exists behind the portal's own sign-in. This page does not check it.
    "hosted_url": "https://minimoi.ai/app/iotconnect/",
    "cover": "prototypes/iot-connect/slide-4.png",
    "shows": [
        "Operator SIM inventory, assigned to exactly one customer account.",
        "Account-scoped activation in batches, one outcome per SIM, with network evidence (HSS → POLICY → SMSC → AAA).",
        "A failed network element rolls back, releases the number and blocks the billing submission; a failed item is retried on its own.",
        "Legacy-billing submission only after network success, then summarized billing, bill cycles and independent reconciled runs.",
        "A real PostgreSQL database and a SQL evidence query, behind synthetic external APIs. Every customer, number and amount is invented.",
    ],
    "slides": [
        {"file": "prototypes/iot-connect/slide-1.png", "title": "The case",
         "caption": "Where the migration stands and the decision being asked for."},
        {"file": "prototypes/iot-connect/slide-2.png", "title": "Agenda",
         "caption": "What the walk-through covers, including the Summarized Billing switch."},
        {"file": "prototypes/iot-connect/slide-3.png", "title": "Before",
         "caption": "Activation and billing through the legacy path, where duplicates and rework arise."},
        {"file": "prototypes/iot-connect/slide-4.png", "title": "After",
         "caption": "Direct synchronous activation, rollback if any network element fails, and one summarized bill file."},
        {"file": "prototypes/iot-connect/slide-5.png", "title": "Next steps",
         "caption": "The staged path from a production proof of concept to a controlled launch."},
    ],
    "app_screens": [],      # none captured yet: the page says so; it never shows a placeholder as a screenshot
    "docs": [
        ("README", f"{_IOT}/README.md"),
        ("Start here", f"{_IOT}/START_HERE.md"),
        ("Hands-on run book", f"{_IOT}/HANDS_ON_RUNBOOK.md"),
        ("Brief", f"{_IOT}/BRIEF.md"),
        ("Decisions", f"{_IOT}/DECISIONS.md"),
        ("Architecture", f"{_IOT}/docs/ARCHITECTURE.md"),
        ("Data model and ordering flow", f"{_IOT}/docs/CANONICAL_DATA_MODEL_AND_ORDERING_FLOW_2026-08-28.md"),
        ("FlowOne provisioning component", f"{_IOT}/docs/FLOWONE_PROVISIONING_COMPONENT_SPEC_2026-08-28.md"),
        ("Middleware component", f"{_IOT}/docs/AMDOCS_MIDDLEWARE_COMPONENT_SPEC_2026-08-28.md"),
    ],
    # Run it: condensed from the project's own HANDS_ON_RUNBOOK.md (commands and addresses are its own).
    "run": {
        "where": "Run every command from the project folder.",
        "caution": ("This Mac has 8 GB. Stop other stacks first so the demo's three containers have room. "
                    "How much memory the demo uses has not been measured here."),
        "steps": [
            {"title": "Check whether it is already running",
             "text": "If all three services show healthy and both health checks say ok, it is up. Do not start a second copy.",
             "commands": ["make status"]},
            {"title": "Start it",
             "text": ("Docker must be running (Docker Desktop, or colima start). The first start builds the image, applies the "
                      "schema and seeds the demo data; later starts keep the data. If a port is taken (8095, 8096 or 54329), "
                      "start on others."),
             "commands": ["docker info", "make up",
                          "IOTCONNECT_APP_PORT=18095 IOTCONNECT_INTEGRATIONS_PORT=18096 IOTCONNECT_DB_PORT=15432 make up"]},
            {"title": "Check that it works",
             "text": "Both should pass. make smoke creates one bill run, so run make reset afterwards if you want a clean start.",
             "commands": ["make status", "make smoke"]},
            {"title": "Open it", "text": "", "commands": [], "urls": True},
            {"title": "Try the story",
             "text": ("Open the operator portfolio, enter a customer account, assign SIMs from inventory, submit an activation batch, "
                      "then run a bill cycle and open the customer billing view. make verify runs the same story and prints a "
                      "pass/fail table. The ten-step rehearsal is in the run book."),
             "commands": ["make verify"]},
            {"title": "Reset, then stop",
             "text": ("Reset only at a planned preparation point, never in the middle of a demonstration: it deletes the demo "
                      "transactions. Stopping keeps the data."),
             "commands": ["make reset", "make down"]},
        ],
        "urls": [
            ("Launch page", "http://127.0.0.1:8095/"),
            ("Presentation", "http://127.0.0.1:8095/presentation"),
            ("Customer (Aster)", "http://127.0.0.1:8095/portal?account=ACCT-000100"),
            ("Customer (Boreal)", "http://127.0.0.1:8095/portal?account=ACCT-000200"),
            ("Operator application", "http://127.0.0.1:8095/operator"),
            ("Support workbench", "http://127.0.0.1:8095/admin"),
            ("Billing workbench", "http://127.0.0.1:8095/workbench"),
            ("API documentation", "http://127.0.0.1:8095/docs"),
            ("Integration API documentation", "http://127.0.0.1:8096/docs"),
        ],
        "urls_note": "A bare request to port 8096 shows {\"detail\":\"Not Found\"}. That is normal: it is an API, so open /docs.",
        "trouble": [
            ("A service is not healthy", "make logs, restart only that service (docker compose restart app), then make status."),
            ("port is already allocated", "Stop what owns the port, or start on other ports (step 2)."),
            ("A page looks stale", "Hard refresh (Command-Shift-R), or return to the launch page."),
            ("Docker is unavailable", "Start Docker (colima start), then make up. The database volume survives restarts."),
            ("A bill cycle skips accounts", "Read the reason on the Bill Cycles page. For a rehearsal use a new cycle such as 2026-11."),
        ],
    },
}

CATALOG = (IOT_CONNECT,)
KIND_LABEL = {"demo": "Demo", "capability": "Capability", "practice": "Practice"}


def all_prototypes() -> list[dict]:
    return [dict(p, kind_label=KIND_LABEL.get(p["kind"], p["kind"])) for p in CATALOG]


def get(slug: str) -> dict | None:
    for p in all_prototypes():
        if p["slug"] == slug:
            return p
    return None
