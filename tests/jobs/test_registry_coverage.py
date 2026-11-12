"""The rule that keeps the master list current (FF-19, #537): a scheduled job the repo defines has an entry in
config/scheduled_jobs.json. "A new scheduled job ships with its entry in config/scheduled_jobs.json." (AGENTS.md)

The scan finds schedule definitions in tracked files: cron lines in host install scripts and runbooks, launchd
StartInterval / StartCalendarInterval (plists and plists written by code), systemd timers, compose services that run
once (restart "no"), GitHub Actions schedules, and APScheduler jobs. Each line found must be named by some entry's
``defined_in`` ("path" or "path::text on that line") or by ``not_jobs``. And every ``defined_in`` must still point at
a real file and a real line, so a removed job cannot leave a stale entry that claims coverage.

If this fails on your change: add the job to config/scheduled_jobs.json (host, schedule, what healthy means, evidence,
defined_in), or, if the match is not a job, add it to ``not_jobs`` with why.
"""
from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "config" / "scheduled_jobs.json"
SKIP_PREFIXES = ("tests/", "docs/", "planning-studio/", "_working/", "_NewDomains/", "minimoi_portal/static/")
SUFFIXES = (".sh", ".py", ".plist", ".timer", ".service", ".yml", ".yaml", ".in")

CRON = re.compile(r"""(?:^|["'\s=])(?:[0-9*/,-]+\s+){4}[0-9*/,A-Za-z-]+\s+(?:root\s+)?"""
                  r"""(?:/|\$|cd\b|docker\b|bash\b|python|%s|AWS_|[A-Z_]+=)""")
LAUNCHD = re.compile(r"StartInterval|StartCalendarInterval")
SYSTEMD = re.compile(r"OnCalendar=|OnUnitInactiveSec=|OnBootSec=")
GHA = re.compile(r"^\s*-\s*cron:\s*['\"]")
COMPOSE_ONCE = re.compile(r"restart:\s*[\"']?no[\"']?\s*$")
APSCHEDULER = re.compile(r"""\bid=["'][\w-]+["'].*misfire_grace_time""")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    return [f for f in out if f.endswith(SUFFIXES) and not f.startswith(SKIP_PREFIXES)
            and f != "config/scheduled_jobs.json" and (REPO / f).is_file()]


def kinds_for(path: str):
    kinds = [("cron", CRON), ("launchd", LAUNCHD), ("systemd", SYSTEMD), ("apscheduler", APSCHEDULER)]
    if path.startswith(".github/workflows/"):
        kinds.append(("github-actions", GHA))
    if path.startswith("docker-compose"):
        kinds.append(("compose", COMPOSE_ONCE))
    return kinds


def scan(files=None) -> list[tuple[str, int, str, str]]:
    """[(path, line number, kind, line)] for every schedule definition found."""
    found = []
    for path in files if files is not None else tracked_files():
        try:
            lines = (REPO / path).read_text("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            for kind, pattern in kinds_for(path):
                if kind == "apscheduler" and line.lstrip().startswith("#"):
                    continue                            # a commented-out job is not scheduled
                if pattern.search(line):
                    found.append((path, number, kind, line.strip()))
    return found


def references(doc: dict) -> list[tuple[str, str, str]]:
    """[(owner, path, text or "")] from every entry's defined_in and from not_jobs."""
    refs = []
    for job in doc["jobs"]:
        for ref in job.get("defined_in", []):
            path, _, text = ref.partition("::")
            refs.append((job["id"], path, text))
    for item in doc.get("not_jobs", []):
        path, _, text = item["defined_in"].partition("::")
        refs.append(("not_jobs", path, text))
    return refs


def uncovered(found, refs) -> list[tuple[str, int, str, str]]:
    return [hit for hit in found
            if not any(path == hit[0] and (not text or text in hit[3]) for _, path, text in refs)]


class MasterListCoversEveryScheduledJob(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(REGISTRY.read_text("utf-8"))

    def test_every_schedule_definition_has_a_list_entry(self):
        missing = uncovered(scan(), references(self.doc))
        self.assertEqual(missing, [], "scheduled job(s) without an entry in config/scheduled_jobs.json:\n" +
                         "\n".join(f"  {p}:{n} [{k}] {line[:120]}" for p, n, k, line in missing))

    def test_every_entry_names_where_it_is_scheduled(self):
        bare = [j["id"] for j in self.doc["jobs"] if not j.get("defined_in")]
        self.assertEqual(bare, [], "entries without defined_in")

    def test_no_stale_references(self):
        stale = []
        for owner, path, text in references(self.doc):
            target = REPO / path
            if not target.is_file():
                stale.append((owner, path, "file missing"))
            elif text and text not in target.read_text("utf-8", errors="replace"):
                stale.append((owner, path, f"no line contains {text!r}"))
        self.assertEqual(stale, [])

    def test_every_active_entry_says_what_healthy_means(self):
        bare = [j["id"] for j in self.doc["jobs"] if not j.get("retired") and not j.get("healthy")]
        self.assertEqual(bare, [])

    def test_retired_entries_say_when_and_why(self):
        for job in self.doc["jobs"]:
            if job.get("retired"):
                self.assertTrue(job.get("retired_on") and job.get("retired_why"), job["id"])


class TheScanItself(unittest.TestCase):
    """The detectors catch each kind and ignore what is not a schedule."""

    def hits(self, path, text):
        found = []
        for line in text.splitlines():
            for kind, pattern in kinds_for(path):
                if kind == "apscheduler" and line.lstrip().startswith("#"):
                    continue
                if pattern.search(line):
                    found.append(kind)
        return found, path

    def test_kinds(self):
        self.assertEqual(self.hits("x.sh", 'L="0 2 * * * $DIR/backup.sh >> log"')[0], ["cron"])
        self.assertEqual(self.hits("x.sh", "printf '7 * * * * root /opt/x/run.sh\\n'")[0], ["cron"])
        self.assertEqual(self.hits("x.plist", "<key>StartCalendarInterval</key>")[0], ["launchd"])
        self.assertEqual(self.hits("x.py", '"OnCalendar=*-*-* 09:10:00"')[0], ["systemd"])
        self.assertEqual(self.hits(".github/workflows/x.yml", "    - cron: '50 9 * * *'")[0], ["github-actions"])
        self.assertEqual(self.hits("docker-compose.x.yml", '    restart: "no"')[0], ["compose"])
        self.assertEqual(self.hits("x.py", '    "cron", hour=7, id="loop_z", misfire_grace_time=600')[0], ["apscheduler"])

    def test_ignored(self):
        self.assertEqual(self.hits("x.py", "#     \"cron\", hour=6, id=\"loop_a\", misfire_grace_time=600")[0], [])
        self.assertEqual(self.hits("x.sh", "echo 1 2 3 4 5")[0], [])
        self.assertEqual(self.hits("x.yml", '    restart: "no"')[0], [])             # not a compose file

    def test_a_new_cron_line_in_a_covered_file_still_needs_its_own_entry(self):
        doc = {"jobs": [{"id": "a", "defined_in": ["scripts/x.sh::backup_local.sh"]}], "not_jobs": []}
        found = [("scripts/x.sh", 1, "cron", 'A="0 2 * * * $D/backup_local.sh"'),
                 ("scripts/x.sh", 2, "cron", 'B="0 5 * * * $D/new_job.sh"')]
        self.assertEqual([h[1] for h in uncovered(found, references(doc))], [2])


if __name__ == "__main__":
    unittest.main()
