import httpx

def search_duckduckgo(topic: str, max_results: int = 5) -> str:
    try:
        url = f"https://lite.duckduckgo.com/lite/?q={topic.replace(' ', '+')}"
        response = httpx.get(url, timeout=12)
        response.raise_for_status()
        text = response.text[:4000]
        text = ' '.join(text.split())
        return text if len(text.strip()) > 100 else "No useful results from DuckDuckGo."
    except Exception as e:
        return f"DuckDuckGo search failed: {str(e)}"
