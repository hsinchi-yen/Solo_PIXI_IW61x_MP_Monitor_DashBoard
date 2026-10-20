"""Stage 1 fixes that need no database: scope filters, no silent truncation,
account helpers, file names and per-file savepoints."""
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from services import auth_utils
from parsers.base import DataQualityWarning
from parsers.iw61x import parse_iw61x_log_file
from legacy.query_service import fetch_records, sql_filters
from legacy.scope import file_name
from ingestion.postgres import PostgresRunRepository

ROOT = Path(__file__).resolve().parents[2]
PASS_LOG = ROOT / "rawlogs" / "5101-260715003" / "20260721_142350_001F7B5806CA_001F7B5806CB_PASS.txt"


class RecordingCursor:
    def __init__(self, fail_on=None):
        self.statements = []
        self.fail_on = fail_on
        self.description = [("id",)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("insert failed")

    def fetchall(self):
        return []

    def fetchone(self):
        return (7,)


class RecordingConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class ScopeFilterTests(unittest.TestCase):
    def test_week_uses_iso_year(self):
        where, params = sql_filters(year=2025, week=1)
        self.assertIn("EXTRACT(ISOYEAR FROM start_time) = %s", where)
        self.assertIn("EXTRACT(WEEK FROM start_time) = %s", where)
        self.assertEqual(params, [2025, 1])

    def test_year_without_week_is_a_calendar_range(self):
        where, params = sql_filters(year=2026)
        self.assertNotIn("ISOYEAR", where)
        self.assertIn("make_date", where)
        self.assertEqual(params, [2026, 2027])

    def test_day_is_a_half_open_range(self):
        where, params = sql_filters(day=date(2026, 7, 21), alias="tr")
        self.assertIn("tr.start_time >= %s::date AND tr.start_time < %s::date + 1", where)
        self.assertEqual(params, ["2026-07-21", "2026-07-21"])

    def test_no_filters(self):
        self.assertEqual(sql_filters(), ("", []))


class NoTruncationTests(unittest.TestCase):
    def test_fetch_records_has_no_limit_by_default(self):
        cursor = RecordingCursor()
        fetch_records(RecordingConnection(cursor), work_order="WO")
        sql, params = cursor.statements[0]
        self.assertNotIn("LIMIT", sql)
        self.assertEqual(params, ["WO"])

    def test_fail_lists_filter_results_in_sql(self):
        cursor = RecordingCursor()
        fetch_records(RecordingConnection(cursor), results=("FAIL", "STOP"), limit=50, offset=100)
        sql, params = cursor.statements[0]
        self.assertIn("result = ANY(%s)", sql)
        self.assertIn("LIMIT %s OFFSET %s", sql)
        self.assertEqual(params, [["FAIL", "STOP"], 50, 100])


class AccountHelperTests(unittest.TestCase):
    def test_password_hash_round_trip(self):
        stored = auth_utils.hash_password("uploader123", iterations=1000)
        self.assertTrue(auth_utils.verify_password("uploader123", stored))
        self.assertFalse(auth_utils.verify_password("wrong-pass", stored))
        self.assertFalse(auth_utils.verify_password("x", "not-a-hash"))

    def test_username_rules(self):
        self.assertIsNone(auth_utils.username_error("op.line1"))
        self.assertIsNotNone(auth_utils.username_error("a"))
        self.assertIsNotNone(auth_utils.username_error("pixi", reserved=["PIXI"]))

    def test_only_token_digest_is_stored(self):
        token = auth_utils.new_session_token()
        self.assertEqual(len(auth_utils.token_hash(token)), 64)
        self.assertNotIn(token, auth_utils.token_hash(token))


class FileNameTests(unittest.TestCase):
    def test_windows_and_posix_paths_keep_only_the_name(self):
        self.assertEqual(file_name(r"D:\TN_Tool_Projects\logs\a_PASS.txt"), "a_PASS.txt")
        self.assertEqual(file_name("/tmp/iw-upload-x/b_FAIL.txt"), "b_FAIL.txt")
        self.assertIsNone(file_name(None))


class SavepointTests(unittest.TestCase):
    def test_failed_insert_rolls_back_only_that_file(self):
        parsed = parse_iw61x_log_file(PASS_LOG)
        cursor = RecordingCursor(fail_on="INSERT INTO test_results")
        repo = PostgresRunRepository(RecordingConnection(cursor))
        repo._known_hashes = set()

        with self.assertRaises(RuntimeError):
            repo.save(parsed)

        statements = [sql.strip() for sql, _ in cursor.statements]
        self.assertEqual(statements[0], "SAVEPOINT iw_save_run")
        self.assertEqual(statements[-1], "ROLLBACK TO SAVEPOINT iw_save_run")
        self.assertNotIn(parsed.file_hash, repo._known_hashes)

    def test_issues_carry_the_batch_id(self):
        parsed = parse_iw61x_log_file(PASS_LOG)
        repo = PostgresRunRepository(RecordingConnection(RecordingCursor()))
        repo._known_hashes = set()
        repo.batch_id = 9
        parsed.warnings = [DataQualityWarning("unknown_mac", "test")]
        with patch("ingestion.postgres.execute_values") as execute_values:
            repo.save(parsed)
        issue_rows = execute_values.call_args_list[-1].args[2]
        self.assertTrue(all(row[1] == 9 for row in issue_rows))


if __name__ == "__main__":
    unittest.main()
