"""
db/graph_seed.py — Seed Neo4j graph from Postgres data.

Reads declared edges from Postgres (not JSON directly — Postgres is the
single read source for graph operations). Writes nodes and relationships
to Neo4j using MERGE (idempotent, safe to re-run).

Node types:   Topic, Source, Group, Leaning, Tag
Relationships: MEMBER_OF, LINKED_TO, TAGGED, ATTACHED_TO, SUPPORTED_BY, ALIAS_OF

Usage:
    python3 db/graph_seed.py
"""

import sys
from pathlib import Path

from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.postgres import (
    list_topics, list_sources, list_groups,
    list_leanings, get_leaning, get_tag_aliases
)

NEO4J_URI  = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "simple123")


def seed(driver):
    with driver.session() as s:

        # ── Topics ────────────────────────────────────────────────────────────
        topics = list_topics()
        for t in topics:
            s.run(
                "MERGE (n:Topic {slug: $slug}) "
                "SET n.name=$name, n.status=$status, n.tags=$tags",
                slug=t["slug"], name=t["slug"], status=t["status"],
                tags=t.get("tags") or []
            )
        print(f"  Topics:   {len(topics)} nodes")

        # ── Sources ───────────────────────────────────────────────────────────
        sources = list_sources()
        for src in sources:
            s.run(
                "MERGE (n:Source {id: $id}) "
                "SET n.title=$title, n.type=$type, n.tags=$tags",
                id=src["id"], title=src.get("title", ""),
                type=src.get("type", "article"), tags=src.get("tags") or []
            )
            # Source → Tag edges
            for tag in (src.get("tags") or []):
                s.run(
                    "MERGE (t:Tag {name: $tag}) "
                    "WITH t MATCH (src:Source {id: $id}) "
                    "MERGE (src)-[:TAGGED]->(t)",
                    tag=tag.lower(), id=src["id"]
                )
        print(f"  Sources:  {len(sources)} nodes")

        # ── Groups ────────────────────────────────────────────────────────────
        groups = list_groups()
        for g in groups:
            s.run(
                "MERGE (n:Group {id: $id}) SET n.name=$name",
                id=g["id"], name=g.get("name", g["id"])
            )
            # Topic → Group edges
            for slug in (g.get("member_topic_slugs") or []):
                s.run(
                    "MATCH (t:Topic {slug: $slug}), (g:Group {id: $id}) "
                    "MERGE (t)-[:MEMBER_OF]->(g)",
                    slug=slug, id=g["id"]
                )
        print(f"  Groups:   {len(groups)} nodes")

        # ── Leanings + evidence ───────────────────────────────────────────────
        leanings = list_leanings()
        for lean in leanings:
            s.run(
                "MERGE (n:Leaning {id: $id}) "
                "SET n.title=$title, n.state=$state",
                id=lean["id"], title=lean.get("title", ""), state=lean.get("state", "question")
            )
            full = get_leaning(lean["id"])
            for ev in (full.get("evidence") or []):
                src_id = ev.get("url") or ev.get("id")
                if src_id:
                    s.run(
                        "MERGE (src:Source {id: $id}) "
                        "WITH src MATCH (l:Leaning {id: $lid}) "
                        "MERGE (l)-[:SUPPORTED_BY {stance: $stance}]->(src)",
                        id=src_id, lid=lean["id"], stance=ev.get("stance", "neutral")
                    )
        print(f"  Leanings: {len(leanings)} nodes")

        # ── Tag aliases ───────────────────────────────────────────────────────
        aliases = get_tag_aliases()
        for alias, canonical in aliases.items():
            s.run(
                "MERGE (a:Tag {name: $alias}) "
                "MERGE (c:Tag {name: $canonical}) "
                "MERGE (a)-[:ALIAS_OF]->(c)",
                alias=alias, canonical=canonical
            )
        print(f"  Aliases:  {len(aliases)} edges")


def main():
    print("── Graph seed ───────────────────────────────────────────────")
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        driver.verify_connectivity()
    except Exception as e:
        print(f"❌ Cannot connect to Neo4j: {e}")
        sys.exit(1)

    try:
        seed(driver)
        print("✅ Graph seeded")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
