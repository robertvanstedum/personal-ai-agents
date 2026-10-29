"""Interpret supplied briefing text; file selection belongs to the adapter."""
import re

def parse_briefing(content):
    articles = {}
    current_article = None
    
    # Find the articles section
    lines = content.split('\n')
    
    for line in lines:
        # The formatter emits TOP N for the actual count; a strict article
        # header is sufficient even when the optional section heading is absent.
        if line.startswith('#'):
            # Extract rank and source
            match = re.match(r'#(\d+)\s+\[([^\]]+)\].*?🏷️\s+(\w+)', line)
            if match:
                rank = int(match.group(1))
                source = match.group(2)
                category = match.group(3)
                current_article = rank
                articles[rank] = {
                    'rank': rank,
                    'source': source,
                    'category': category,
                    'title': None,
                    'url': None,
                    'scores': None,
                    'hash_id': None
                }
        
        # Extract hash_id
        elif current_article and line.strip().startswith('ID:'):
            articles[current_article]['hash_id'] = line.split('ID:')[1].strip()
        
        # Extract title (next non-empty line after rank)
        elif current_article and articles[current_article]['title'] is None:
            title = line.strip()
            if title and not title.startswith('http') and not title.startswith('Published') and not title.startswith('ID:'):
                articles[current_article]['title'] = title
        
        # Extract URL
        elif current_article and line.strip().startswith('http'):
            articles[current_article]['url'] = line.strip()
        
        # Extract scores
        elif current_article and 'Scores:' in line:
            articles[current_article]['scores'] = line.strip()
            current_article = None  # Done with this article
    
    return articles
