from fastapi import FastAPI
from pydantic import BaseModel
import ollama
from neo4j import GraphDatabase

app = FastAPI(title="Mini-Me Agent Prototype")

# Neo4j connection - CHANGE password if needed
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "***REDACTED***"  # your current password

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

class Question(BaseModel):
    query: str

class NewTrace(BaseModel):
    thought: str
    motive: str

@app.get("/")
def root():
    return {"message": "Mini-Me Agent API is running"}

@app.post("/ask")
def ask(question: Question):
    # Retrieve all decision traces from Neo4j
    with driver.session() as session:
        result = session.run("MATCH (d:Decision) RETURN d.thought, d.motive")
        decisions = [record for record in result]

    # Build context from your personal traces
    context = ""
    if decisions:
        context = "Your personal context from past decisions:\n"
        for d in decisions:
            context += f"- Thought: {d['d.thought']}\n  Motive: {d['d.motive']}\n\n"

    # Personalized system prompt
    system_prompt = f"""You are a personalized AI agent that thinks and reasons exactly like the user.
Use the following personal context to inform every response:

{context}

Always consider uncertainty, complexity, long-term perspective, geopolitics, investing risks, and privacy.
Respond thoughtfully, nuanced, and pragmatic — never give financial advice without disclaimer.

User question: {question.query}"""

    response = ollama.chat(
        model='gemma3:1b',
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': question.query}
        ]
    )

    return {"answer": response['message']['content']}

@app.post("/add_trace")
def add_trace(trace: NewTrace):
    with driver.session() as session:
        session.run(
            """
            CREATE (d:Decision {
                thought: $thought,
                motive: $motive,
                timestamp: datetime()
            })
            """,
            thought=trace.thought,
            motive=trace.motive
        )
    return {"message": "New decision trace added to context graph!"}

@app.get("/traces")
def get_traces(limit: int = 10):
    with driver.session() as session:
        result = session.run(
            """
            MATCH (d:Decision)
            RETURN d.thought, d.motive, d.timestamp
            ORDER BY d.timestamp DESC
            LIMIT $limit
            """,
            limit=limit
        )
        traces = [
            {
                "thought": record["d.thought"],
                "motive": record["d.motive"],
                "timestamp": record["d.timestamp"]
            }
            for record in result
        ]
    return {"traces": traces}


