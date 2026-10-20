"""Unit tests that need no database: scope filters, account helpers, file names,
per-file savepoints, the splitter's upload helpers and the AI report number check."""
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from services import auth_utils
from parsers.base import DataQualityWarning
from parsers.iw61x import parse_iw61x_log_file
from errors import ApiError
from ingestion.splitter import infer_work_order, is_valid_work_order, iter_upload_runs
from services.filters import Scope, parse_scope, raw_where
from services.ingest import file_name, parse_quantity, validate_upload
from services.report_text import check_numbers
from ingestion.postgres import PostgresRunRepository
from ingestion.service import BatchReport, FileIngestionResult

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
    def test_iso_week_is_monday_to_sunday(self):
        scope = parse_scope(year=2024, week=1)
        self.assertEqual((scope.date_from, scope.date_to), (date(2024, 1, 1), date(2024, 1, 8)))

    def test_date_range_is_half_open_on_start_time(self):
        where, params = raw_where(parse_scope("2026-07-21", "2026-07-22", "WO"))
        self.assertEqual(where, "WHERE start_time >= %s AND start_time < %s AND work_order = %s")
        self.assertEqual(params, [date(2026, 7, 21), date(2026, 7, 23), "WO"])

    def test_bad_input_is_a_400(self):
        for kwargs in ({"date_from": "foo"}, {"week": 3}, {"date_from": "2026-07-22", "date_to": "2026-07-21"},
                       {"year": 2026, "week": 60}):
            with self.assertRaises(ApiError) as ctx:
                parse_scope(**kwargs)
            self.assertEqual(ctx.exception.status, 400)

    def test_no_filters(self):
        self.assertTrue(parse_scope().is_empty)
        self.assertEqual(raw_where(Scope()), ("", []))


class UploadRuleTests(unittest.TestCase):
    FACTS = {"inferred_wo": "5101-260715003", "unique_mac1": 120, "invalid": 0, "products": {"IW611": 180}}

    def codes(self, *args):
        return {i["code"]: i["level"] for i in validate_upload(self.FACTS, *args)}

    def test_work_order_and_quantity(self):
        self.assertTrue(is_valid_work_order("5101-260715003"))
        self.assertFalse(is_valid_work_order("5101-2607"))
        self.assertEqual(parse_quantity("4,000"), 4000)
        self.assertIsNone(parse_quantity("4k"))
        self.assertEqual(self.codes("", ""), {"R1": "error", "R2": "error"})

    def test_other_work_order_and_other_product(self):
        codes = self.codes("5101-260715009", 100, {"5101-260715003": 180})
        self.assertEqual((codes["R3"], codes["R4"]), ("warn", "warn"))
        facts = dict(self.FACTS, products={"IW611": 170, "IW416": 10})
        self.assertEqual({i["code"]: i["level"] for i in validate_upload(facts, "5101-260715003", 1)}["R9"], "error")

    def test_work_order_from_names(self):
        self.assertEqual(infer_work_order("upload.zip", "5101-260715003/30/Log_all.txt"), "5101-260715003")
        self.assertIsNone(infer_work_order("logs.zip"))

    def test_split_files_pass_through_and_summary_is_ignored(self):
        name = "20260721_142350_001F7B5806CA_001F7B5806CB_PASS.txt"
        runs = list(iter_upload_runs([(name, lambda: b"a\r\nb\r\n"), ("summary.txt", lambda: b"x")]))
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["content"], "a\nb\n")          # same newlines as the desktop split file


class ReportNumberCheckTests(unittest.TestCase):
    FACTS = {"units": 120, "yield": {"latest": {"yield_pct": 99.17}}, "attempts": {"stop": 46},
             "spec_watch": [{"item": "PER (WIFI 5GHz 11a BW20 OFDM54 5500 MHz)", "cpk": 0.354}]}

    def test_numbers_must_come_from_the_facts(self):
        ok = {"summary": "120 units, yield 99.17%, 46 STOP runs.", "findings": ["PER at 5GHz 5500 MHz has cpk 0.354"],
              "recommendations": []}
        self.assertTrue(check_numbers(ok, self.FACTS)["ok"])
        bad = dict(ok, summary="121 units, yield 98.5%")
        self.assertEqual(check_numbers(bad, self.FACTS)["unknown"], ["121", "98.5"])


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

    def test_batch_without_work_order_takes_the_resolved_one(self):
        # Desktop uploader: work order comes from the folder name, not from begin_batch.
        cursor = RecordingCursor()
        repo = PostgresRunRepository(RecordingConnection(cursor))
        repo.batch_id = 3
        one = BatchReport(total_files=2, files=[FileIngestionResult("a.txt", "uploaded", work_order="5101-260715003"),
                                                FileIngestionResult("b.txt", "duplicate", work_order="5101-260715003")])
        repo.finish_batch(one)
        self.assertEqual(cursor.statements[-1][1][-2:], ("5101-260715003", 3))
        two = BatchReport(files=[FileIngestionResult("a.txt", "uploaded", work_order="5101-260715003"),
                                 FileIngestionResult("c.txt", "uploaded", work_order="5101-260801001")])
        repo.finish_batch(two)
        self.assertEqual(cursor.statements[-1][1][-2:], (None, 3))


if __name__ == "__main__":
    unittest.main()
