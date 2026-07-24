from __future__ import annotations


class PostgresRunRepository:
    def __init__(self, conn):
        self.conn = conn

    def has_hash(self, file_hash: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM test_results WHERE file_hash = %s", (file_hash,))
            return cur.fetchone() is not None

    def save(self, parsed, source: str = "api") -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO test_results (
                    work_order, product, mac1, mac2, mac1_source, start_time,
                    end_time, test_duration_sec, result, flow_file, package_name,
                    tester_sn, fw_version, fail_step_num, fail_step_name,
                    fail_message, fail_category, raw_log, file_hash, source_file
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                RETURNING id
                """,
                (
                    parsed.work_order,
                    parsed.product,
                    parsed.mac1,
                    parsed.mac2,
                    parsed.mac1_source,
                    parsed.start_time,
                    parsed.end_time,
                    parsed.test_duration_sec,
                    parsed.result,
                    parsed.flow_file,
                    parsed.package_name,
                    parsed.tester_sn,
                    parsed.fw_version,
                    parsed.fail_step_num,
                    parsed.fail_step_name,
                    parsed.fail_message,
                    parsed.fail_category,
                    parsed.raw_log,
                    parsed.file_hash,
                    parsed.source_file,
                ),
            )
            result_id = cur.fetchone()[0]
            self._save_measurements(cur, result_id, parsed.measurements)
            self._save_issues(cur, result_id, parsed.issues)
            return result_id

    def _save_measurements(self, cur, result_id: int, measurements) -> None:
        for metric in measurements:
            cur.execute(
                """
                INSERT INTO test_measurements (
                    test_result_id, technology, direction, standard, band,
                    frequency_mhz, bandwidth, rate, antenna, step_num, step_name,
                    metric_name, value, unit, limit_low, limit_high, passed
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    result_id,
                    metric.technology,
                    metric.direction,
                    metric.standard,
                    metric.band,
                    metric.frequency_mhz,
                    metric.bandwidth,
                    metric.rate,
                    metric.antenna,
                    metric.step_num,
                    metric.step_name,
                    metric.metric_name,
                    metric.value,
                    metric.unit,
                    metric.limit_low,
                    metric.limit_high,
                    metric.passed,
                ),
            )

    def _save_issues(self, cur, result_id: int, issues) -> None:
        for issue in issues:
            cur.execute(
                """
                INSERT INTO data_quality_issues (test_result_id, issue_type, description)
                VALUES (%s, %s, %s)
                """,
                (result_id, issue.issue_type, issue.description),
            )
