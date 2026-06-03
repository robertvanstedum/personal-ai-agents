"""
db/neo4j_driver.py — Neo4j graph query helpers.

Uses the official neo4j Bolt driver. Read-only queries only — writes
go through graph_seed.py or the bridge layer.

Usage:
    from db.neo4j_driver import get_topic_neighbors, get_related_topics
"""

from neo4j import GraphDatabase

NEO4J_URI  = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "simple123")


def _driver():
    return GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)


def get_topic_neighbors(slug: str, hops: int = 2) -> list:
    """Return all nodes within `hops` of a Topic node.

    Returns list of {id, labels, props} dicts — the raw neighborhood.
    """
    with _driver().session() as s:
        result = s.run(
            f"MATCH (start:Topic {{slug: $slug}})-[*1..{hops}]-(neighbor) "
            "RETURN DISTINCT neighbor",
            slug=slug
        )
        nodes = []
        for record in result:
            node = record["neighbor"]
            nodes.append({
                "labels": list(node.labels),
                "props":  dict(node),
            })
        return nodes


def get_related_topics(slug: str, hops: int = 2) -> list:
    """Return Topic nodes within `hops` of the given topic (excluding itself)."""
    with _driver().session() as s:
        result = s.run(
            f"MATCH (start:Topic {{slug: $slug}})-[*1..{hops}]-(other:Topic) "
            "WHERE other.slug <> $slug "
            "RETURN DISTINCT other.slug AS slug, other.status AS status",
            slug=slug
        )
        return [{"slug": r["slug"], "status": r["status"]} for r in result]


def get_sources_for_leaning(leaning_id: str) -> list:
    """Return Source nodes connected to a Leaning via SUPPORTED_BY."""
    with _driver().session() as s:
        result = s.run(
            "MATCH (l:Leaning {id: $id})-[r:SUPPORTED_BY]->(src:Source) "
            "RETURN src.id AS id, src.title AS title, r.stance AS stance",
            id=leaning_id
        )
        return [{"id": r["id"], "title": r["title"], "stance": r["stance"]}
                for r in result]


def get_node_counts() -> dict:
    """Return count of each node type — useful for quick health check."""
    with _driver().session() as s:
        result = s.run(
            "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count "
            "ORDER BY label"
        )
        return {r["label"]: r["count"] for r in result}
