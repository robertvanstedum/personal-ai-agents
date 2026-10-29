"""Real generated briefing through both parsers and the feedback CLI boundary."""
import ast
import re
import sys
from pathlib import Path
import pytest
from domains.curator.briefing_render import format_output
from domains.curator.briefing_text import parse_briefing_text

ROOT = Path(__file__).resolve().parents[1]


def functions(path, calls):
    source = ROOT / 'domains/curator/curator_feedback.py'
    nodes = [n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef)
             and n.name in {'parse_curator_output', 'main'}]
    ns = {'CURATOR_OUTPUT': path, 'sys': sys, 're': re,
          'record_feedback': lambda *a, **kw: calls.append((a, kw))}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), ns)
    return ns


def entries(count, revision='old'):
    return [dict(hash_id=f'h{revision}{i}', title=f'Synthetic {i}', source='Example', category='fiscal',
                 link=f'https://example.invalid/{revision}/{i}', published=None, score=7.0, summary='')
            for i in range(count)]


@pytest.mark.parametrize('count,headerless', [(1, False), (2, False), (15, False), (20, False), (23, False), (1, True)])
def test_generated_briefing_feedback_identity_and_stale_refusal(tmp_path, monkeypatch, count, headerless):
    text = format_output(entries(count))
    if headerless: text = text[text.index('#1 '):]
    path = tmp_path / 'briefing.txt'; path.write_text(text)
    delivered = parse_briefing_text(text)
    calls = []; ns = functions(path, calls)
    stored = ns['parse_curator_output']()
    assert len(delivered) == len(stored) == count
    assert delivered[0]['url'] == stored[1]['url'] == 'https://example.invalid/old/0'
    monkeypatch.setattr(sys, 'argv', ['feedback.py','like','1','--channel','telegram','--text','synthetic',
                                    '--expected-url',delivered[0]['url']])
    ns['main']()
    assert len(calls) == 1 and calls[0][0][3]['hash_id'] == 'hold0'
    calls.clear(); path.write_text(format_output(entries(count, 'new')))
    with pytest.raises(SystemExit) as exc: ns['main']()
    assert exc.value.code == 2 and calls == []
