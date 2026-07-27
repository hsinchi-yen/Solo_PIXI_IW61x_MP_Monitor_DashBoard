from __future__ import annotations

from psycopg2.extras import execute_values


class PostgresRunRepository:
    """
    PostgreSQL-backed run repository.

    Performance notes
    -----------------
    * Pre-fetches ALL known file hashes into a local set on first use.
      ``has_hash()`` is then an O(1) in-memory check with zero DB round-trips.
    * Measurements use psycopg2 ``execute_values`` multi-row INSERTs.
    * Issues also use ``execute_values``.
    * Commits are driven externally (``ingest_paths`` commits every N files),
      keeping individual transactions small.
    """

    def __init__(self, conn):
        self.conn = conn
        self._known_hashes: set[str] | None = None   # lazy-loaded

    # ------------------------------------------------------------------
    # Internal: hash cache
    # ------------------------------------------------------------------
    def _ensure_hash_cache(self) -> None:
        if self._known_hashes is not None:
            return
        with self.conn.cursor() as cur:
            cur.execute("SELECT file_hash FROM test_results")
            self._known_hashes = {row[0] for row in cur.fetchall()}

    def preload_hashes(self) -> int:
        """Eagerly load hash cache and return the count."""
        self._ensure_hash_cache()
        return len(self._known_hashes)

    # ------------------------------------------------------------------
    # RunRepository protocol
    # ------------------------------------------------------------------
    def has_hash(self, file_hash: str) -> bool:
        self._ensure_hash_cache()
        return file_hash in self._known_hashes

    def save(self, parsed, source: str = "api") -> int:
        self._ensure_hash_cache()
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

        # Track in local cache so repeated calls within same session also dedup
        self._known_hashes.add(parsed.file_hash)
        return result_id

    # ------------------------------------------------------------------
    # Bulk insert helpers
    # ------------------------------------------------------------------
    def _save_measurements(self, cur, result_id: int, measurements) -> None:
        if not measurements:
            return
        execute_values(
            cur,
            """
            INSERT INTO test_measurements (
                test_result_id, technology, direction, standard, band,
                frequency_mhz, bandwidth, rate, antenna, step_num, step_name,
                metric_name, value, unit, limit_low, limit_high, passed
            )
            VALUES %s
            """,
            [
                (
                    result_id,
                    m.technology,
                    m.direction,
                    m.standard,
                    m.band,
                    m.frequency_mhz,
                    m.bandwidth,
                    m.rate,
                    m.antenna,
                    m.step_num,
                    m.step_name,
                    m.metric_name,
                    m.value,
                    m.unit,
                    m.limit_low,
                    m.limit_high,
                    m.passed,
                )
                for m in measurements
            ],
            page_size=2_000,
        )

    def _save_issues(self, cur, result_id: int, issues) -> None:
        if not issues:
            return
        execute_values(
            cur,
            """
            INSERT INTO data_quality_issues (test_result_id, issue_type, description)
            VALUES %s
            """,
            [(result_id, iss.issue_type, iss.description) for iss in issues],
            page_size=1_000,
        )
