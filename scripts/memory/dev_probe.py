#!/usr/bin/env python3
"""Private-network acceptance: authenticated retrieval for both consumer grants.

Print counts only. Select a keyword already present in this Dev index; never
insert a canary into user history or call a model/provider.
"""
import json
from pathlib import Path
import sqlite3
import urllib.error
import urllib.request


def probe():
    con = sqlite3.connect("file:/index/memory.sqlite?mode=ro", uri=True)
    try:
        con.execute("CREATE VIRTUAL TABLE temp.vocab USING fts5vocab(main, fts, 'row')")
        row = con.execute("SELECT term FROM vocab WHERE length(term)>=5 ORDER BY doc DESC LIMIT 1").fetchone()
        if row is None:
            raise RuntimeError("no_searchable_keyword")
        query = row[0]
    finally:
        con.close()
    endpoint = "http://minimoi-dev-vault:8771/v1/ask"
    def ask(token, body):
        request = urllib.request.Request(endpoint, data=json.dumps(body).encode(),
                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, {}
    assert ask("invalid", {"query": query})[0] == 401
    results = {}
    for agent in ("cos", "mc"):
        env = dict(line.split("=", 1) for line in (Path("/consumer") / (agent + ".env")).read_text().splitlines())
        token = env["MINIMOI_VAULT_ASK_TOKEN"]
        code, result = ask(token, {"query": query, "include_raw": True})
        assert code == 200 and result.get("keyword") and not result.get("unresolved")
        assert ask(token, {"query": query, "scope": "other"})[0] == 400
        results[agent] = {"retrieved": len(result["keyword"]), "scope_override": "refused"}
    print(json.dumps({"state": "verified", "grants": results, "bad_bearer": "refused"}))


if __name__ == "__main__":
    probe()
