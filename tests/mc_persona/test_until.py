"""Inclusive audit windows using synthetic SQLite only; no owner transcripts."""
import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import traceback
from unittest.mock import patch

import test_summary as fixtures

log = fixtures.module('outbound_log')
ZERO = {'total_web_calls': 0, 'by_tool': {'web_search': 0, 'web_fetch': 0},
        'missing_arguments': {'web_search.query': 0, 'web_fetch.url': 0},
        'null_arguments': {'web_search.query': 0, 'web_fetch.url': 0}}


class UntilTests(fixtures.SummaryBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.c.close)
        # Deliberately nonchronological insertion verifies ORDER BY seq remains.
        for value in (2000, 1000, 2001):
            self.add('a', 'assistant', [self.call('web_search', {'query': str(value)})], created=value)

    def argv(self, until=None, *, since=fixtures.EPOCH, summary=False):
        args = [str(fixtures.SCRIPT), '--db', str(self.db), '--session-id', 'a', '--since', since]
        if until is not None:
            args += ['--until', until]
        if summary:
            args += ['--summary']
        return args

    def cli(self, until=None, **kwargs):
        return subprocess.run([sys.executable, *self.argv(until, **kwargs)], capture_output=True, text=True)

    def test_fractional_millisecond_boundary_cli_regression(self):
        self.c.execute('DELETE FROM transcript_events')
        for value in (1000, 1001, 1002):
            self.add('a', 'assistant', [self.call('web_search', {'query': 'synthetic'})], created=value)
        for summary in (False, True):
            with self.subTest(summary=summary):
                result = self.cli('1970-01-01T00:00:01.001Z', summary=summary)
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                if summary:
                    self.assertEqual(json.loads(result.stdout)['total_web_calls'], 2)
                else:
                    self.assertEqual([json.loads(line)['created_at'] for line in result.stdout.splitlines()], [1000, 1001])

    def test_inclusive_upper_bound_and_seq_order_in_both_modes(self):
        until = '1970-01-01T00:00:02Z'
        stream = self.cli(until)
        self.assertEqual((stream.returncode, stream.stderr), (0, ''))
        rows = [json.loads(line) for line in stream.stdout.splitlines()]
        self.assertEqual([row['created_at'] for row in rows], [2000, 1000])
        self.assertEqual(sorted(rows[0]), sorted(['session_id', 'seq', 'created_at', 'call_id',
                                                'tool', 'query', 'arguments_available']))
        summary = self.cli(until, summary=True)
        self.assertEqual((summary.returncode, summary.stderr), (0, ''))
        result = json.loads(summary.stdout)
        self.assertEqual(result['total_web_calls'], 2)
        self.assertEqual(sorted(result), sorted(ZERO))

    def test_window_isolates_middle_timestamp(self):
        for summary in (False, True):
            with self.subTest(summary=summary):
                result = self.cli('1970-01-01T00:00:02Z', since='1970-01-01T00:00:02Z', summary=summary)
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                value = json.loads(result.stdout)
                self.assertEqual(value['total_web_calls'] if summary else value['created_at'], 1 if summary else 2000)

    def test_inverted_window_is_empty(self):
        result = self.cli('1970-01-01T00:00:01Z', since='1970-01-01T00:00:02Z', summary=True)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(json.loads(result.stdout), ZERO)
        stream = self.cli('1970-01-01T00:00:01Z', since='1970-01-01T00:00:02Z')
        self.assertEqual((stream.returncode, stream.stdout, stream.stderr), (0, '', ''))

    def test_omitted_until_preserves_three_positional_call(self):
        self.assertEqual(list(log.calls(self.db, 'a', 0)), list(log.calls(self.db, 'a', 0, until=None)))
        self.assertEqual(len(list(log.calls(self.db, 'a', 0))), 3)
        self.assertEqual(json.loads(self.cli(summary=True).stdout)['total_web_calls'], 3)

    def test_invalid_until_is_withheld_in_both_modes(self):
        for invalid in ('2031-02-30T00:00:00Z-SENTINEL', 'PRIVATE_UNTIL_SENTINEL', '2020-01-01T00:00:00'):
            for summary in (False, True):
                with self.subTest(invalid=invalid, summary=summary):
                    result = self.cli(invalid, summary=summary)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (2, '', log.UNTIL_ERROR + '\n'))
                    self.assertNotIn(invalid, result.stderr)

    def test_exact_upper_bound_conversion(self):
        cases = {
            '1970-01-01T00:00:01.001Z': 1001,
            '1970-01-01T00:00:00.999Z': 999,
            '1970-01-01T00:00:01.001999Z': 1001,
            '1970-01-01T01:00:01.001+01:00': 1001,
            '1969-12-31T23:59:59.999Z': -1,
            '1969-12-31T23:59:59.999999Z': -1,
            '9999-12-31T23:59:59.999999Z': 253402300799999,
            '0001-01-01T00:00:00+00:00': -62135596800000,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                actual = log.until_ms(raw)
                self.assertIsInstance(actual, int)
                self.assertEqual(actual, expected)

    def test_year_one_is_exact_and_valid_in_both_modes(self):
        for summary in (False, True):
            with self.subTest(summary=summary):
                result = self.cli('0001-01-01T00:00:00+00:00', summary=summary)
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                if summary:
                    self.assertEqual(json.loads(result.stdout), ZERO)
                else:
                    self.assertEqual(result.stdout, '')

    def test_known_since_float_defect_remains_out_of_scope(self):
        # Known defect, not desired behavior. Delete this pin when --since is
        # repaired; this change must not silently alter the legacy lower bound.
        self.assertEqual(log.since_ms('1970-01-01T00:00:01.001Z'), 1000)

    def test_conversion_failures_are_sanitized_without_exception_chain(self):
        sentinel = 'PRIVATE_CONVERSION_SENTINEL'
        for exception_type in (OverflowError, OSError):
            for summary in (False, True):
                with self.subTest(exception_type=exception_type, summary=summary):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with patch.object(sys, 'argv', self.argv('1970-01-01T00:00:02Z', summary=summary)), \
                         patch.object(log, 'exact_ms', side_effect=exception_type(sentinel)), \
                         contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        try:
                            log.main()
                        except SystemExit as exc:
                            self.assertEqual(exc.code, 2)
                            self.assertIsNone(exc.__cause__)
                            self.assertIsNone(exc.__context__)
                            rendered = traceback.format_exc()
                        else:
                            self.fail('conversion failure must fail')
                    self.assertEqual((stdout.getvalue(), stderr.getvalue()), ('', log.UNTIL_ERROR + '\n'))
                    self.assertNotIn(sentinel, rendered)
                    self.assertNotIn(exception_type.__name__, rendered)

    def test_malformed_until_exception_chain_and_rendered_traceback_are_clean(self):
        sentinel = 'PRIVATE_PARSE_SENTINEL'
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                log.until_ms(sentinel)
            except SystemExit as exc:
                self.assertIsNone(exc.__cause__)
                self.assertIsNone(exc.__context__)
                rendered = traceback.format_exc()
            else:
                self.fail('invalid until must fail')
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn('ValueError', rendered)

    def test_since_is_parsed_first_and_legacy_jsonl_asymmetry_is_preserved(self):
        # SinceParsingPrivacyTests.test_default_jsonl_mode_behaviour_is_unchanged
        # explicitly pins the old JSONL ValueError; only the new flag is sanitized.
        for summary in (False, True):
            with self.subTest(summary=summary):
                result = self.cli('BAD_UNTIL_SENTINEL', since='BAD_SINCE_SENTINEL', summary=summary)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertNotIn('BAD_UNTIL_SENTINEL', result.stderr)
                self.assertNotIn(log.UNTIL_ERROR, result.stderr)
                if summary:
                    self.assertEqual(result.stderr, log.SINCE_ERROR + '\n')
                else:
                    self.assertIn('Invalid isoformat string', result.stderr)
                    self.assertIn('BAD_SINCE_SENTINEL', result.stderr)

    def test_bound_is_parameterized_read_only_and_connection_closes(self):
        statements, connections = [], []
        original = sqlite3.connect
        class Tracked:
            def __init__(self, connection):
                self.connection = connection
            def execute(self, sql, params=()):
                statements.append((sql, params))
                return self.connection.execute(sql, params)
            def close(self):
                self.connection.close()
        def connect(database_uri, **kwargs):
            self.assertTrue(database_uri.endswith('?mode=ro'))
            self.assertEqual(kwargs, {'uri': True})
            connection = original(database_uri, **kwargs)
            connections.append(connection)
            return Tracked(connection)
        with patch.object(log.sqlite3, 'connect', connect):
            self.assertEqual(len(list(log.calls(self.db, 'a', 1000, until=2000))), 2)
        self.assertEqual(statements[0], ('PRAGMA query_only=ON', ()))
        self.assertTrue(statements[1][0].endswith('created_at<=? ORDER BY seq'))
        self.assertEqual(statements[1][1], ('a', 1000, 2000))
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute('SELECT 1')
