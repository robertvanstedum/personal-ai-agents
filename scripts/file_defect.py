"""
scripts/file_defect.py — File GitHub issues for failed tests from JUnit XML.

Called by CI on test failure:
  python3 scripts/file_defect.py --junit reports/junit.xml --sha abc1234 --branch main
"""

import argparse
import os
import sys
import xml.etree.ElementTree as ET

import requests


def file_issue(test_name: str, failure_message: str, sha: str, branch: str):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print(f"GITHUB_TOKEN not set — skipping issue for {test_name}", file=sys.stderr)
        return

    repo = "robertvanstedum/personal-ai-agents"
    title = f"Test failure: {test_name} [{sha[:7]}]"
    body = (
        f"## Automated defect report\n\n"
        f"**Test:** `{test_name}`\n"
        f"**Branch:** {branch}\n"
        f"**Commit:** {sha}\n\n"
        f"**Failure:**\n```\n{failure_message[:2000]}\n```\n\n"
        f"**Actions:** https://github.com/{repo}/actions\n\n"
        f"*Filed automatically by CI pipeline.*"
    )

    r = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
        json={"title": title, "body": body, "labels": ["defect", "ci-failure"]},
        timeout=15,
    )
    if r.status_code == 201:
        print(f"Filed issue for {test_name}: {r.json()['html_url']}")
    else:
        print(f"Failed to file issue for {test_name}: {r.status_code} {r.text}", file=sys.stderr)


def parse_junit(junit_path: str):
    tree = ET.parse(junit_path)
    root = tree.getroot()
    failures = []
    for testcase in root.iter("testcase"):
        failure = testcase.find("failure")
        error = testcase.find("error")
        node = failure if failure is not None else error
        if node is not None:
            classname = testcase.get("classname", "")
            name = testcase.get("name", "unknown")
            test_name = f"{classname}.{name}" if classname else name
            message = node.get("message", "") or (node.text or "")
            failures.append((test_name, message))
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", required=True, help="Path to JUnit XML report")
    parser.add_argument("--sha", required=True, help="Git commit SHA")
    parser.add_argument("--branch", required=True, help="Git branch name")
    args = parser.parse_args()

    failures = parse_junit(args.junit)
    if not failures:
        print("No failures found in JUnit report.")
        return

    print(f"Found {len(failures)} failure(s) — filing GitHub issues...")
    for test_name, message in failures:
        file_issue(test_name, message, args.sha, args.branch)


if __name__ == "__main__":
    main()
