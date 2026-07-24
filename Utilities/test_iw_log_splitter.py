"""Tests for the IW611 one-Timestamp-per-file splitter utility."""

from pathlib import Path
import tempfile
import unittest

from iw_log_splitter_core import (
    ParsedRun,
    build_output_filename,
    classify_run,
    create_summary_file,
    extract_mac_addresses,
    parse_run,
    split_log_file,
    split_run_blocks,
    summarize_output_directory,
)


HEADER = (
    "==================================================================================================\r\n"
    "=  Running IQfact+ component : IQfactRun_Console.exe                                               |\r\n"
)


def make_run(
    timestamp: str,
    mac1: str = "001F7B5806D4",
    mac2: str = "001F7B5806D5",
    ending: str = "",
) -> str:
    return (
        HEADER
        + "======== Package Name: IQfact+_NXP_W61x_4.0.0.6_Lock\r\n"
        + f"======== Timestamp: {timestamp}\r\n"
        + "9.NXP_INPUT_MAC_ADDRESS  __________________________________________\r\n"
        + f"Generetaed MAC Address: {mac1}\r\n"
        + f"BD_ADDRESS : {mac2}\r\n"
        + f"MAC_ADDRESS : {mac1}\r\n"
        + ending
    )


PASS_ENDING = (
    "[IQfact]- ERROR: transient retry [FAIL]\r\n"
    "Passed Run(s)                     : 1\r\n"
    "Failed Run(s) on Limits           : 0\r\n"
    "Failed Run(s) on Errors           : 0\r\n"
)

FAIL_ENDING = (
    "Passed Run(s)                     : 0\r\n"
    "Failed Run(s) on Limits           : 0\r\n"
    "Failed Run(s) on Errors           : 1\r\n"
    "IQfactRun_Console: - FLOW RUNNING ERROR ! [FAIL]\r\n"
)


class TestRunParsing(unittest.TestCase):
    def test_split_run_blocks_returns_one_block_per_timestamp(self):
        first = make_run("2026-07-21_14:33:51", ending="")
        second = make_run("2026-07-21_14:35:08", ending=PASS_ENDING)

        blocks = split_run_blocks("ignored preamble\r\n" + first + "\r\n" + second)

        self.assertEqual(2, len(blocks))
        self.assertIn("Timestamp: 2026-07-21_14:33:51", blocks[0])
        self.assertIn("Timestamp: 2026-07-21_14:35:08", blocks[1])

    def test_split_run_blocks_preserves_internal_crlf_and_one_final_newline(self):
        source = make_run("2026-07-21_14:35:08", ending=PASS_ENDING) + "\r\n\r\n"

        block = split_run_blocks(source)[0]

        self.assertIn("\r\nPassed Run(s)", block)
        self.assertTrue(block.endswith("\r\n"))
        self.assertFalse(block.endswith("\r\n\r\n"))

    def test_pass_summary_wins_over_transient_fail_text(self):
        block = make_run("2026-07-21_14:35:08", ending=PASS_ENDING)

        self.assertEqual("PASS", classify_run(block))

    def test_failed_run_summary_is_fail(self):
        block = make_run("2026-07-21_14:37:02", ending=FAIL_ENDING)

        self.assertEqual("FAIL", classify_run(block))

    def test_incomplete_run_is_stop_even_when_retry_contains_fail(self):
        block = make_run(
            "2026-07-21_14:33:51",
            ending="[IQfact]- ERROR: reach maximum retry [FAIL]\r\n",
        )

        self.assertEqual("STOP", classify_run(block))

    def test_final_flow_running_error_without_summary_is_fail(self):
        block = make_run(
            "2026-07-21_14:37:02",
            ending="IQfactRun_Console: - FLOW RUNNING ERROR ! [FAIL]\r\n",
        )

        self.assertEqual("FAIL", classify_run(block))

    def test_extract_mac_addresses_normalizes_colons_and_case(self):
        block = make_run(
            "2026-07-21_14:35:08",
            mac1="00:1f:7b:58:06:d4",
            mac2="00:1f:7b:58:06:d5",
            ending=PASS_ENDING,
        )

        self.assertEqual(
            ("001F7B5806D4", "001F7B5806D5"),
            extract_mac_addresses(block),
        )

    def test_mac_fallback_ignores_placeholder_values(self):
        block = (
            HEADER
            + "======== Timestamp: 2026-07-21_14:35:08\r\n"
            + "MAC_ADDRESS : FFFFFFFFFFFF\r\n"
            + "BD_ADDRESS : 000000000000\r\n"
            + "MAC_ADDRESS : 001F7B5806D4\r\n"
            + "BD_ADDRESS : 00:1f:7b:58:06:d5\r\n"
            + PASS_ENDING
        )

        self.assertEqual(
            ("001F7B5806D4", "001F7B5806D5"),
            extract_mac_addresses(block),
        )

    def test_parse_incomplete_run_uses_unknown_mac_labels(self):
        block = (
            HEADER
            + "======== Timestamp: 2026-07-21_14:41:54\r\n"
            + "Operator stopped the flow\r\n"
        )

        parsed = parse_run(block)

        self.assertEqual("UNKNOWNMAC1", parsed.mac1)
        self.assertEqual("UNKNOWNMAC2", parsed.mac2)
        self.assertEqual("STOP", parsed.result)

    def test_build_output_filename_uses_approved_format(self):
        parsed = ParsedRun(
            date="20260721",
            time="143508",
            mac1="001F7B5806D4",
            mac2="001F7B5806D5",
            result="PASS",
            content="log\r\n",
        )

        self.assertEqual(
            "20260721_143508_001F7B5806D4_001F7B5806D5_PASS.txt",
            build_output_filename(parsed),
        )


class TestFileSplitting(unittest.TestCase):
    def test_split_log_file_writes_every_run_and_does_not_overwrite(self):
        source_content = (
            make_run("2026-07-21_14:33:51", ending="")
            + "\r\n"
            + make_run("2026-07-21_14:35:08", ending=PASS_ENDING)
            + "\r\n"
            + make_run("2026-07-21_14:37:02", ending=FAIL_ENDING)
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_path = temp_path / "Log_all.txt"
            output_path = temp_path / "split_output"
            source_path.write_bytes(source_content.encode("utf-8"))

            first_report = split_log_file(source_path, output_path)
            second_report = split_log_file(source_path, output_path)

            self.assertEqual(3, first_report.total)
            self.assertEqual(1, first_report.pass_count)
            self.assertEqual(1, first_report.fail_count)
            self.assertEqual(1, first_report.stop_count)
            self.assertEqual(3, first_report.created)
            self.assertEqual(3, second_report.created)
            self.assertTrue(
                (
                    output_path
                    / "20260721_143508_001F7B5806D4_001F7B5806D5_PASS_1.txt"
                ).is_file()
            )

    def test_split_log_file_preserves_crlf_line_endings(self):
        source_content = make_run(
            "2026-07-21_14:35:08",
            ending=PASS_ENDING,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_path = temp_path / "Log_all.txt"
            output_path = temp_path / "split_output"
            source_path.write_bytes(source_content.encode("utf-8"))

            report = split_log_file(source_path, output_path)
            output_bytes = report.output_paths[0].read_bytes()

            self.assertIn(b"\r\n", output_bytes)
            self.assertNotIn(b"\n", output_bytes.replace(b"\r\n", b""))

    def test_output_summary_counts_only_split_filenames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir)
            (output_path / "20260721_143351_A_B_STOP.txt").write_text("stop")
            (output_path / "20260721_143508_A_B_PASS.txt").write_text("pass")
            (output_path / "20260721_143702_A_B_FAIL_1.txt").write_text("fail")
            (output_path / "notes.txt").write_text("ignore")

            summary = summarize_output_directory(output_path)

            self.assertEqual(3, summary.total)
            self.assertEqual(1, summary.pass_count)
            self.assertEqual(1, summary.fail_count)
            self.assertEqual(1, summary.stop_count)
            self.assertEqual(1, summary.skipped)

    def test_create_summary_file_records_result_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir)
            (output_path / "20260721_143508_A_B_PASS.txt").write_text("pass")
            (output_path / "20260721_143702_A_B_FAIL.txt").write_text("fail")

            summary_path = create_summary_file(output_path)
            summary_text = summary_path.read_text(encoding="utf-8")

            self.assertIn("Total Timestamp Runs: 2", summary_text)
            self.assertIn("PASS count: 1", summary_text)
            self.assertIn("FAIL count: 1", summary_text)
            self.assertIn("STOP count: 0", summary_text)


if __name__ == "__main__":
    unittest.main()
