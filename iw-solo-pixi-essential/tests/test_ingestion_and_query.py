import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ingestion.service import InMemoryRunRepository, collect_log_files, ingest_paths
from parsers.iw61x import parse_iw61x_log_file
from api.query_service import build_summary_from_records


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "rawlogs" / "5101-260715003"


class IngestionAndQueryTests(unittest.TestCase):
    def test_folder_scan_discovers_logs_in_nested_work_order_folders(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_order = root / "5101-260715003"
            work_order.mkdir()
            expected = work_order / "20260721_142350_AABBCCDDEEFF_112233445566_PASS.txt"
            expected.write_text("fixture", encoding="utf-8")
            (work_order / "summary.txt").write_text("ignore", encoding="utf-8")

            paths = collect_log_files([root])

        self.assertEqual(paths, [expected])

    def test_ingestion_batch_is_idempotent_by_hash(self):
        repo = InMemoryRunRepository()
        first = ingest_paths([DATASET], repo=repo, source="test")
        second = ingest_paths([DATASET], repo=repo, source="test")

        self.assertEqual(first.total_files, 180)
        self.assertEqual(first.uploaded, 180)
        self.assertEqual(first.duplicates, 0)
        self.assertEqual(first.rejected, 0)
        self.assertGreater(first.warnings, 0)
        self.assertEqual(second.total_files, 180)
        self.assertEqual(second.uploaded, 0)
        self.assertEqual(second.duplicates, 180)
        self.assertEqual(len(repo.records), 180)

    def test_ingestion_reports_progress_for_each_file(self):
        paths = sorted(DATASET.glob("*_PASS.txt"))[:2]
        progress = []

        report = ingest_paths(
            paths,
            repo=InMemoryRunRepository(),
            source="test",
            progress_callback=lambda completed, total, item: progress.append(
                (completed, total, item.status)
            ),
        )

        self.assertEqual(report.uploaded, 2)
        self.assertEqual(progress, [(1, 2, "uploaded"), (2, 2, "uploaded")])

    def test_summary_exposes_attempt_and_three_unique_unit_yields(self):
        records = [
            parse_iw61x_log_file(path)
            for path in sorted(DATASET.glob("*.txt"))
            if path.name[:8].isdigit()
        ]

        summary = build_summary_from_records(records)

        self.assertEqual(summary["attempts"]["total"], 180)
        self.assertEqual(summary["attempts"]["PASS"], 121)
        self.assertEqual(summary["attempts"]["FAIL"], 13)
        self.assertEqual(summary["attempts"]["STOP"], 46)
        self.assertEqual(summary["data_quality"]["unknown_mac_attempts"], 7)
        self.assertIn("first_pass", summary["unit_yield"])
        self.assertIn("latest_result", summary["unit_yield"])
        self.assertIn("any_pass", summary["unit_yield"])
        self.assertEqual(summary["total"], 120)
        self.assertEqual(summary["pass"], 119)
        self.assertEqual(summary["fail"], 1)
        self.assertEqual(summary["stop"], 0)
        self.assertEqual(summary["yield_pct"], 99.17)
        self.assertGreaterEqual(
            summary["unit_yield"]["any_pass"]["yield_pct"],
            summary["unit_yield"]["first_pass"]["yield_pct"],
        )


if __name__ == "__main__":
    unittest.main()
