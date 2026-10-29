"""Privacy and correctness tests for outbound_log --summary.

These tests live beside test_release.py and do not modify it. The existing
LogTests case there remains the non-regression guard on calls() itself.
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/mc_persona/outbound_log.py'


def module(name):
    s = importlib.util.spec_from_file_location(name, ROOT / 'scripts/mc_persona' / f'{name}.py')
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


log = module('outbound_log')

EPOCH = '1970-01-01T00:00:00+00:00'
FUTURE = '2999-01-01T00:00:00+00:00'


class SummaryBase(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.db = Path(self.t.name) / 'db'
        self.c = sqlite3.connect(self.db)
        self.c.execute('CREATE TABLE transcript_events(session_id TEXT,seq INT,event_json TEXT,created_at INT)')
        self._seq = 0

    def add(self, session, role, content, created=1000):
        self._seq += 1
        self.c.execute(
            'INSERT INTO transcript_events VALUES (?,?,?,?)',
            (session, self._seq, json.dumps({'message': {'role': role, 'content': content}}), created),
        )
        self.c.commit()

    def call(self, name, arguments, call_id='x'):
        return {'type': 'toolCall', 'id': call_id, 'name': name, 'arguments': arguments}

    def run_main(self, session='a', since=EPOCH, summary=True):
        argv = ['outbound_log.py', '--db', str(self.db), '--session-id', session, '--since', since]
        if summary:
            argv.append('--summary')
        buf = io.StringIO()
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(buf):
            log.main()
        return buf.getvalue()


class SentinelSweepTests(SummaryBase):
    """Test 1: nothing from any row value may reach stdout in summary mode."""

    SENTINELS = {
        'query_value': 'SENTINEL-QUERY-VALUE',
        'url_value': 'https://SENTINEL-HOST.example/SENTINEL-PATH',
        'unknown_arg_key': 'SENTINEL-UNKNOWN-ARG',
        'sibling_text': 'SENTINEL-SIBLING-TEXT',
        'tool_result': 'SENTINEL-TOOL-RESULT',
        'call_id': 'SENTINEL-CALL-ID',
    }

    def setUp(self):
        super().setUp()
        search = self.call('web_search', {
            'query': self.SENTINELS['query_value'],
            'private_extra': self.SENTINELS['unknown_arg_key'],
        }, call_id=self.SENTINELS['call_id'])
        fetch = self.call('web_fetch', {'url': self.SENTINELS['url_value']})
        self.add('a', 'assistant', [search, {'type': 'text', 'text': self.SENTINELS['sibling_text']}])
        self.add('a', 'assistant', [fetch])
        self.add('a', 'toolResult', [self.call('web_search', {'query': self.SENTINELS['tool_result']})])

    def test_no_sentinel_reaches_stdout(self):
        out = self.run_main()
        for label, sentinel in self.SENTINELS.items():
            with self.subTest(label=label):
                self.assertNotIn(sentinel, out)
        # Sanity: the summary actually counted the calls it refused to echo.
        self.assertEqual(json.loads(out)['total_web_calls'], 2)

    def test_no_sentinel_reaches_summarize_return_value(self):
        result = log.summarize(log.calls(self.db, 'a', 0))
        blob = json.dumps(result)
        for label, sentinel in self.SENTINELS.items():
            with self.subTest(label=label):
                self.assertNotIn(sentinel, blob)

    def test_streaming_mode_still_emits_rows(self):
        """Test 5 (mutual exclusion, other direction): without --summary the JSONL stream is unchanged."""
        out = self.run_main(summary=False)
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])['query'], self.SENTINELS['query_value'])
        self.assertNotIn('total_web_calls', out)

    def test_summary_suppresses_the_stream(self):
        out = self.run_main(summary=True)
        self.assertNotIn('"seq"', out)
        self.assertNotIn('"created_at"', out)
        self.assertNotIn('"call_id"', out)
        self.assertNotIn('"session_id"', out)


class CallsNonRegressionTests(SummaryBase):
    """Test 2: calls() contract is untouched by the summary feature."""

    def test_calls_row_shape_and_values_unchanged(self):
        self.add('a', 'assistant', [self.call('web_search', {'query': 'public documentation'})])
        rows = list(log.calls(self.db, 'a', 0))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['query'], 'public documentation')
        self.assertEqual(
            sorted(rows[0]),
            sorted(['session_id', 'seq', 'created_at', 'call_id', 'tool', 'query', 'arguments_available']),
        )

    def test_calls_still_closes_its_connection(self):
        self.add('a', 'assistant', [self.call('web_search', {'query': 'public documentation'})])
        connections = []
        original_connect = sqlite3.connect

        def tracked(*args, **kwargs):
            con = original_connect(*args, **kwargs)
            connections.append(con)
            return con

        with patch.object(log.sqlite3, 'connect', tracked):
            log.summarize(log.calls(self.db, 'a', 0))
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute('SELECT 1')


class FilterParityTests(SummaryBase):
    """Test 3: the summary consumes calls()' filtering rather than re-implementing it."""

    def test_counts_only_target_session_assistant_web_calls(self):
        search = self.call('web_search', {'query': 'q'})
        fetch = self.call('web_fetch', {'url': 'u'})
        self.add('a', 'assistant', [search, fetch])
        self.add('a', 'assistant', [self.call('shell', {'command': 'ls'})])
        self.add('a', 'toolResult', [search])
        self.add('a', 'user', [search])
        self.add('b', 'assistant', [search, fetch])
        out = json.loads(self.run_main())
        self.assertEqual(out['total_web_calls'], 2)
        self.assertEqual(out['by_tool'], {'web_search': 1, 'web_fetch': 1})

    def test_since_window_is_respected(self):
        self.add('a', 'assistant', [self.call('web_search', {'query': 'old'})], created=1000)
        self.add('a', 'assistant', [self.call('web_search', {'query': 'new'})], created=5_000_000_000_000)
        out = json.loads(self.run_main(since='2100-01-01T00:00:00+00:00'))
        self.assertEqual(out['total_web_calls'], 1)


class ArgumentClassificationTests(SummaryBase):
    """Test 4: not-a-dict / key-absent / key-present-null are three distinct states."""

    def test_three_way_classification(self):
        self.add('a', 'assistant', [self.call('web_fetch', 'not-a-dict')])
        self.add('a', 'assistant', [self.call('web_fetch', {'other': 'o'})])
        self.add('a', 'assistant', [self.call('web_fetch', {'url': None})])
        self.add('a', 'assistant', [self.call('web_fetch', {'url': 'https://ok.example'})])
        out = json.loads(self.run_main())
        self.assertEqual(out['total_web_calls'], 4)
        self.assertEqual(out['missing_arguments']['web_fetch.url'], 2)
        self.assertEqual(out['null_arguments']['web_fetch.url'], 1)
        self.assertEqual(
            out['missing_arguments']['web_fetch.url'] + out['null_arguments']['web_fetch.url'] + 1,
            out['by_tool']['web_fetch'],
        )

    def test_empty_string_counts_as_present(self):
        self.add('a', 'assistant', [self.call('web_search', {'query': ''})])
        self.add('a', 'assistant', [self.call('web_fetch', {'url': ''})])
        out = json.loads(self.run_main())
        self.assertEqual(out['total_web_calls'], 2)
        self.assertEqual(out['missing_arguments'], {'web_search.query': 0, 'web_fetch.url': 0})
        self.assertEqual(out['null_arguments'], {'web_search.query': 0, 'web_fetch.url': 0})

    def test_missing_arguments_do_not_cross_tools(self):
        self.add('a', 'assistant', [self.call('web_search', {})])
        self.add('a', 'assistant', [self.call('web_fetch', {'url': None})])
        out = json.loads(self.run_main())
        self.assertEqual(out['missing_arguments'], {'web_search.query': 1, 'web_fetch.url': 0})
        self.assertEqual(out['null_arguments'], {'web_search.query': 0, 'web_fetch.url': 1})


class AllowlistDriftTests(SummaryBase):
    """Test 5: construction by allowlist, proved against a row carrying future fields."""

    def test_unknown_row_keys_never_reach_output(self):
        drifted = [{
            'session_id': 'a', 'seq': 1, 'created_at': 1000, 'call_id': 'c1',
            'tool': 'web_search', 'query': 'SENTINEL-DRIFT-QUERY',
            'arguments_available': True,
            'user_agent': 'SENTINEL-DRIFT-UA',
            'response_body': 'SENTINEL-DRIFT-BODY',
        }]
        with patch.object(log, 'calls', lambda *a, **k: iter(drifted)):
            out = self.run_main()
        for sentinel in ('SENTINEL-DRIFT-QUERY', 'SENTINEL-DRIFT-UA', 'SENTINEL-DRIFT-BODY', 'user_agent', 'response_body', 'call_id'):
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, out)
        self.assertEqual(json.loads(out)['by_tool']['web_search'], 1)

    def test_unknown_tool_name_is_not_counted_or_echoed(self):
        drifted = [{'tool': 'SENTINEL-FUTURE-TOOL', 'arguments_available': True}]
        with patch.object(log, 'calls', lambda *a, **k: iter(drifted)):
            out = self.run_main()
        self.assertNotIn('SENTINEL-FUTURE-TOOL', out)
        self.assertEqual(json.loads(out)['total_web_calls'], 0)

    def test_output_keys_are_exactly_the_allowlist(self):
        self.add('a', 'assistant', [self.call('web_search', {'query': 'q'})])
        out = json.loads(self.run_main())
        self.assertEqual(sorted(out), ['by_tool', 'missing_arguments', 'null_arguments', 'total_web_calls'])
        self.assertEqual(sorted(out['by_tool']), ['web_fetch', 'web_search'])
        self.assertEqual(sorted(out['missing_arguments']), ['web_fetch.url', 'web_search.query'])
        self.assertEqual(sorted(out['null_arguments']), ['web_fetch.url', 'web_search.query'])
        for value in out['by_tool'].values():
            self.assertIsInstance(value, int)


class ExceptionSafetyTests(SummaryBase):
    """Invariant 4: an error while summarizing must not carry a row value."""

    def test_raised_error_carries_no_row_data(self):
        class Hostile(dict):
            def get(self, *a, **k):
                raise ValueError('leaked SENTINEL-EXC-VALUE from row')

        rows = iter([Hostile(tool='web_search')])
        with self.assertRaises(RuntimeError) as ctx:
            log.summarize(rows)
        text = str(ctx.exception) + str(ctx.exception.__cause__) + str(ctx.exception.__context__)
        self.assertNotIn('SENTINEL-EXC-VALUE', text)
        self.assertIsNone(ctx.exception.__cause__)


class EmptyAndShapeTests(SummaryBase):
    """Tests 6 and 7: zero is reportable, and output is exactly one JSON object."""

    def test_empty_window_emits_zeroed_object(self):
        self.add('a', 'assistant', [self.call('web_search', {'query': 'q'})])
        out = self.run_main(since=FUTURE)
        self.assertTrue(out.strip())
        parsed = json.loads(out)
        self.assertEqual(parsed, {
            'total_web_calls': 0,
            'by_tool': {'web_search': 0, 'web_fetch': 0},
            'missing_arguments': {'web_search.query': 0, 'web_fetch.url': 0},
            'null_arguments': {'web_search.query': 0, 'web_fetch.url': 0},
        })

    def test_empty_session_exits_zero_via_main(self):
        argv = ['outbound_log.py', '--db', str(self.db), '--session-id', 'nope', '--since', EPOCH, '--summary']
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(log.main())

    def test_stdout_is_exactly_one_json_line(self):
        self.add('a', 'assistant', [self.call('web_search', {'query': 'q'}), self.call('web_fetch', {'url': 'u'})])
        self.add('a', 'assistant', [self.call('web_fetch', {'url': 'u2'})])
        out = self.run_main()
        self.assertEqual(out.count('\n'), 1)
        self.assertTrue(out.endswith('\n'))
        lines = out.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIsInstance(json.loads(lines[0]), dict)

    def test_since_still_requires_timezone(self):
        argv = ['outbound_log.py', '--db', str(self.db), '--session-id', 'a', '--since', '2020-01-01', '--summary']
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                log.main()


class SinceParsingPrivacyTests(SummaryBase):
    """Repair: a malformed --since must not echo the supplied value in summary mode.

    datetime.fromisoformat raises ValueError('Invalid isoformat string: <value>'),
    which reaches stderr as an unhandled traceback. The supplied string is operator
    input and may itself be private context, so summary mode must not render it.
    """

    SENTINEL = 'PRIVATE_SINCE_SENTINEL'

    def run_cli(self, since, summary=True):
        argv = [sys.executable, str(SCRIPT), '--db', str(self.db), '--session-id', 'a', '--since', since]
        if summary:
            argv.append('--summary')
        return subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT))

    def test_malformed_since_in_summary_mode_is_generic(self):
        proc = self.run_cli(self.SENTINEL)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, '')
        self.assertNotIn(self.SENTINEL, proc.stderr)
        self.assertNotIn('Traceback', proc.stderr)
        self.assertNotIn('ValueError', proc.stderr)

    def test_naive_since_in_summary_mode_is_generic(self):
        proc = self.run_cli('2020-01-01T00:00:00')
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, '')
        self.assertNotIn('Traceback', proc.stderr)
        self.assertNotIn('2020-01-01T00:00:00', proc.stderr)

    def test_out_of_range_since_in_summary_mode_is_generic(self):
        """Conversion, not just parsing: a year-1 timestamp overflows int(dt.timestamp()*1000) on some hosts."""
        proc = self.run_cli('0001-01-01T00:00:00+00:00')
        self.assertEqual(proc.returncode == 0, proc.stdout != '')
        if proc.returncode != 0:
            self.assertNotIn('Traceback', proc.stderr)
            self.assertNotIn('0001-01-01', proc.stderr)

    def test_valid_since_in_summary_mode_still_succeeds(self):
        self.add('a', 'assistant', [self.call('web_search', {'query': 'q'})])
        proc = self.run_cli(EPOCH)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, '')
        self.assertEqual(json.loads(proc.stdout)['total_web_calls'], 1)

    def test_default_jsonl_mode_behaviour_is_unchanged(self):
        """Scope lock: the repair touches summary mode only.

        The streaming path keeps its original argparse/ValueError surface. That path
        is out of scope for this repair, so this test pins it rather than fixing it.
        """
        proc = self.run_cli(self.SENTINEL, summary=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, '')
        self.assertIn('Invalid isoformat string', proc.stderr)

    def test_default_jsonl_mode_still_streams(self):
        self.add('a', 'assistant', [self.call('web_search', {'query': 'public documentation'})])
        proc = self.run_cli(EPOCH, summary=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout.strip())['query'], 'public documentation')


class TracebackRenderingTests(SummaryBase):
    """A raised summary error must stay clean through a *fully rendered* traceback."""

    SENTINEL = 'SENTINEL-GENERATOR-ROW-VALUE'

    def exploding_rows(self):
        yield {'tool': 'web_search', 'arguments_available': True, 'query': 'fine'}
        raise ValueError('db read failed for ' + self.SENTINEL)

    def test_full_rendered_traceback_has_no_row_data(self):
        try:
            log.summarize(self.exploding_rows())
        except RuntimeError:
            rendered = traceback.format_exc()
        else:
            self.fail('summarize() did not raise')
        self.assertNotIn(self.SENTINEL, rendered)
        self.assertNotIn('db read failed', rendered)
        self.assertNotIn('During handling of the above exception', rendered)
        self.assertNotIn('The above exception was the direct cause', rendered)

    def test_full_rendered_traceback_via_main_has_no_row_data(self):
        with patch.object(log, 'calls', lambda *a, **k: self.exploding_rows()):
            try:
                self.run_main()
            except RuntimeError:
                rendered = traceback.format_exc()
            else:
                self.fail('main() did not raise')
        self.assertNotIn(self.SENTINEL, rendered)
        self.assertNotIn('db read failed', rendered)

    def test_exception_chain_attributes_are_empty(self):
        with self.assertRaises(RuntimeError) as ctx:
            log.summarize(self.exploding_rows())
        self.assertIsNone(ctx.exception.__cause__)
        self.assertIsNone(ctx.exception.__context__)


if __name__ == '__main__':
    unittest.main()
