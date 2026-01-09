from fastapi import FastAPI
from pydantic import BaseModel
import ollama
from neo4j import GraphDatabase

app = FastAPI(title="Mini-Me Agent Prototype")

# Neo4j connection (CHANGE password if needed)
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "***REDACTED***"  # your password

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

class Question(BaseModel):
    query: str

@app.get("/")
def root():
    return {"message": "Mini-Me Agent API is running"}

@app.post("/ask")
def ask(question: Question):
    # Retrieve all decision traces from Neo4j
    with driver.session() as session:
        result = session.run("MATCH (d:Decision) RETURN d.thought, d.motive")
        decisions = [record for record in result]

    # Build context from graph
    context = ""
    if decisions:
        context = "Your personal context from past decisions:\n"
        for d in decisions:
            context += f"- Thought: {d['d.thought']}\n  Motive: {d['d.motive']}\n\n"

    # Personalized prompt with your context
    prompt = f"""You are a personalized AI agent that thinks and reasons like the user.
Use the following personal context to inform your response:

{context}

User question: {question.query}

Respond thoughtfully, considering uncertainty, complexity, geopolitics, investing, and the user's worldview."""

    response = ollama.chat(
        model='gemma3:1b',
        messages=[
            {'role': 'system', 'content': prompt},
            {'role': 'user', 'content': question.query}
        ]
    )

    return {"answer": response['message']['content']}
