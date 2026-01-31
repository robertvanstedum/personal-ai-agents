from fastapi import FastAPI
from pydantic import BaseModel
import ollama
from neo4j import GraphDatabase

# Adding Scheduler
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime

def auto_add_daily_trace():
    thought = f"Daily reflection on {datetime.now().strftime('%Y-%m-%d')}"
    motive = "Continuing to monitor geopolitical and investing landscape; patience, fundamentals, and adaptation remain core principles under uncertainty."

    with driver.session() as session:
        session.run(
            """
            CREATE (d:Decision {
                thought: $thought,
                motive: $motive,
                timestamp: datetime()
            })
            """,
            thought=thought,
            motive=motive
        )
    print(f"Auto-added trace at {datetime.now()}")

# Start scheduler when server starts
scheduler = BackgroundScheduler()
scheduler.add_job(auto_add_daily_trace, 'interval', hours=1)  # every hour for testing
scheduler.start()


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

# fixing date handling between neo4j and python
@app.post("/add_trace")
def add_trace(trace: NewTrace):
    with driver.session() as session:
        session.run(
            """
            CREATE (d:Decision {
                thought: $thought,
                motive: $motive,
                timestamp: datetime()  // correct: Neo4j server current time
            })
            """,
            thought=trace.thought,
            motive=trace.motive
        )
    return {"message": "New decision trace added to context graph!"}

# updating get traces to reformat date
@app.get("/traces")
def get_traces(limit: int = 10, keyword: str = None, sort: str = "desc"):
    sort = sort.lower()
    if sort not in ["asc", "desc"]:
        sort = "desc"

    if limit < 1:
        limit = 5
    if limit > 20:
        limit = 20

    cypher = """
    MATCH (d:Decision)
    """
    params = {"limit": limit}

    if keyword:
        cypher += """
        WHERE toLower(d.thought) CONTAINS toLower($keyword)
           OR toLower(d.motive) CONTAINS toLower($keyword)
        """
        params["keyword"] = keyword

    cypher += f"""
    RETURN d.thought, d.motive, toString(d.timestamp) AS timestamp
    ORDER BY d.timestamp {sort.upper()}
    LIMIT $limit
    """

    with driver.session() as session:
        result = session.run(cypher, **params)
        traces = [
            {
                "thought": record["d.thought"],
                "motive": record["d.motive"],
                "timestamp": record["timestamp"]  # now a clean string
            }
            for record in result
        ]
    return {"traces": traces}


from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime

def auto_search_and_trace():
    topic = "gold price geopolitical impact January 2026"

    # Use the toggled search function
    search_results = search_function(topic)

    # Summarize with Ollama (same as before)
    summary_prompt = f"Summarize the following search results about '{topic}' in one short paragraph, focusing on market impact and geopolitical factors:\n\n{search_results}"
    summary_response = ollama.chat(
        model='gemma3:1b',
        messages=[{'role': 'user', 'content': summary_prompt}]
    )
    summary = summary_response['message']['content']

    thought = f"Auto-search summary on {topic} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    motive = f"Geopolitical and market news update: {summary}. This informs my long-term investing view under uncertainty and complexity."

    with driver.session() as session:
        session.run(
            """
            CREATE (d:Decision {
                thought: $thought,
                motive: $motive,
                timestamp: datetime()
            })
            """,
            thought=thought,
            motive=motive
        )
    print(f"Auto-added trace: {thought}")

# Start scheduler when server starts
scheduler = BackgroundScheduler()
scheduler.add_job(auto_search_and_trace, 'interval', minutes=1)  # every 1 minute
scheduler.start()
print("Background scheduler started - auto-stub-traces every 1 minute")
 

# === STUB VERSION (fake data – fast, no network, great for testing) ===
def stub_search(topic):
    return f"""
    - Gold hits new highs amid ongoing US-China tensions
    - Central banks buying gold at record pace in 2026
    - Geopolitical risks drive safe-haven demand despite strong US equities
    - Analysts expect continued volatility in precious metals
    - Inflation concerns linger despite Fed policy
    """

# === REAL VERSION (uses your web_search tool) ===
def real_search(topic):
    try:
        results = web_search(query=topic, num_results=5)
        # Convert to text (handles list/dict format)
        if isinstance(results, list):
            return "\n".join([str(r) for r in results])
        return str(results)
    except Exception as e:
        return f"Web search failed: {str(e)}. Using fallback summary."

# Choose mode here (toggle this line)
search_function = stub_search   # Change to real_search when ready for live data
# search_function = real_search


