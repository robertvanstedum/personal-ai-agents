from neo4j import GraphDatabase
import datetime

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "***REDACTED***")

driver = GraphDatabase.driver(URI, auth=AUTH)

def create_decision(tx, thought, motive):
    tx.run(
        "CREATE (d:Decision {thought: $thought, motive: $motive, timestamp: $timestamp})",
        thought=thought,
        motive=motive,
        timestamp=datetime.datetime.now().isoformat()
    )

with driver.session() as session:
    session.execute_write(
        create_decision,
        thought="Local-first personalized AI foundation established (January 7, 2026)",
        motive=(
            "Successfully built clean repo, Ollama + gemma3:1b local inference, Docker infra with Neo4j and PostgreSQL "
            "(resolved Postgres 18 volume mount issue, venv/PATH problems, and multiple setup hurdles). "
            "Overcame every obstacle through persistent, systematic rebuilding and research. "
            "Chose local-first architecture for privacy, speed, and deliberate evolution — consistent with complexity science view "
            "that robust systems emerge from iterative adaptation under uncertainty. "
            "This seed node begins the context graph that will allow agents to learn and reflect my motives, precedents, and worldview over time."
        )
    )

print("✅ First decision trace permanently stored in context graph!")
driver.close()
