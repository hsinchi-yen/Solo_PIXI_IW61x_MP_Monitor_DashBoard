import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ingestion.service import InMemoryRunRepository, collect_log_files, ingest_paths


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


if __name__ == "__main__":
    unittest.main()
