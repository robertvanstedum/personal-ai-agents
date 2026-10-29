"""Pure interpretation of the supported Curator text briefing format."""

def parse_briefing_text(content: str):
    """
    Parse supplied briefing text into a list of article dicts.
    File selection and reading belong to the calling adapter.
    Current format:
    #N [Source] 🏷️  category (model)
       ID: xxxxx
       Title
       https://...
       Published: ...
       Scores: X/10 (raw: X, final: X)
       snippet...
    """
    import re

    articles = []

    # Split on article markers (#1, #2, etc.)
    sections = re.split(r'(?:^|\n)#(\d+) ', content)

    for i in range(1, len(sections), 2):
        num = sections[i]
        article_text = sections[i+1]

        lines = [l.strip() for l in article_text.strip().split('\n')]
        if len(lines) < 3:
            continue

        # Parse header line: [Source] 🏷️  category (model)
        header = lines[0]
        source_match = re.search(r'\[(.*?)\]', header)
        category_match = re.search(r'🏷️\s+(\w+)', header)

        source = source_match.group(1) if source_match else "Unknown"
        category = category_match.group(1) if category_match else "other"

        # Find URL by pattern (robust against added/removed lines above it)
        url = "unknown"
        for line in lines[1:]:
            if line.startswith('http://') or line.startswith('https://'):
                url = line
                break

        # Find title: first non-empty line that isn't the ID, a URL, Published, or Scores
        title = "Unknown"
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('ID:'):
                continue
            if stripped.startswith('http://') or stripped.startswith('https://'):
                continue
            if stripped.startswith('Published:') or stripped.startswith('Scores:'):
                continue
            title = stripped
            break

        # Parse score
        score = "?"
        for line in lines:
            if 'Scores:' in line:
                score_match = re.search(r'Scores:\s+([\d.]+)/10', line)
                if score_match:
                    score = score_match.group(1)
                break

        articles.append({
            'num': num,
            'title': title,
            'url': url,
            'source': source,
            'category': category,
            'score': score
        })

    return articles
