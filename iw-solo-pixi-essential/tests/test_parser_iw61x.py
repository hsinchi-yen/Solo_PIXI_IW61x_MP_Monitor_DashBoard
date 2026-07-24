import unittest
from pathlib import Path

from parsers.iw61x import audit_folder, parse_iw61x_log_file


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "rawlogs" / "5101-260715003"


class IW61xParserTests(unittest.TestCase):
    def test_pass_log_uses_folder_work_order_and_final_summary(self):
        parsed = parse_iw61x_log_file(
            DATASET / "20260721_142350_001F7B5806CA_001F7B5806CB_PASS.txt"
        )

        self.assertEqual(parsed.work_order, "5101-260715003")
        self.assertEqual(parsed.result, "PASS")
        self.assertEqual(parsed.mac1, "001F7B5806CA")
        self.assertEqual(parsed.mac2, "001F7B5806CB")
        self.assertEqual(parsed.product, "IW611")
        self.assertEqual(parsed.flow_file, "TN329_IW611_CSP_TN.txt")
        self.assertGreater(parsed.test_duration_sec, 80)
        self.assertTrue(any(m.standard == "11ax" for m in parsed.measurements))
        self.assertTrue(any(m.technology == "BT" for m in parsed.measurements))

    def test_fail_log_reports_terminal_failed_step(self):
        parsed = parse_iw61x_log_file(
            DATASET / "20260721_143702_001F7B5806DA_001F7B5806DB_FAIL.txt"
        )

        self.assertEqual(parsed.result, "FAIL")
        self.assertEqual(parsed.fail_step_num, 110)
        self.assertEqual(parsed.fail_step_name, "SAVE_NVRAM")
        self.assertEqual(parsed.fail_category, "TestFail")

    def test_stop_unknown_mac_is_traceable_but_marked_unknown(self):
        parsed = parse_iw61x_log_file(
            DATASET / "20260721_143404_UNKNOWNMAC1_UNKNOWNMAC2_STOP.txt"
        )

        self.assertEqual(parsed.result, "STOP")
        self.assertIsNone(parsed.mac1)
        self.assertIsNone(parsed.mac2)
        self.assertEqual(parsed.mac1_source, "unknown")
        self.assertTrue(any(issue.issue_type == "unknown_mac" for issue in parsed.issues))
        self.assertTrue(any(issue.issue_type == "retry" for issue in parsed.issues))

    def test_acceptance_dataset_audit_matches_known_baseline(self):
        report = audit_folder(DATASET)

        self.assertEqual(report["total_files"], 180)
        self.assertEqual(report["results"], {"PASS": 121, "FAIL": 13, "STOP": 46})
        self.assertEqual(report["unknown_mac_files"], 7)
        self.assertEqual(report["work_orders"], {"5101-260715003": 180})
        self.assertEqual(report["errors"], [])


if __name__ == "__main__":
    unittest.main()
