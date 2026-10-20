"""API contract tests against a throwaway PostgreSQL database.

Needs a reachable server (default: the local stack on port 5434). Set IW_TEST_PG_URL to
a maintenance-database URL to point elsewhere; the suite is skipped when it cannot connect.
It creates a database named iw_it_<random>, loads the 180 acceptance logs, and drops it again.
"""
import os
import secrets
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "rawlogs" / "5101-260715003"
ADMIN_URL = os.environ.get("IW_TEST_PG_URL", "postgresql://pixi:pixipass@localhost:5434/postgres")

try:
    import psycopg2
    from fastapi.testclient import TestClient
    _probe = psycopg2.connect(ADMIN_URL, connect_timeout=3)
    _probe.close()
    AVAILABLE = True
except Exception:  # pragma: no cover - depends on the environment
    AVAILABLE = False

DB_NAME = f"iw_it_{secrets.token_hex(4)}"


def _admin_exec(sql):
    conn = psycopg2.connect(ADMIN_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()


@unittest.skipUnless(AVAILABLE, "PostgreSQL for integration tests is not reachable")
class ApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _admin_exec(f"CREATE DATABASE {DB_NAME}")
        cls.db_url = ADMIN_URL.rsplit("/", 1)[0] + f"/{DB_NAME}"
        os.environ["DATABASE_URL"] = cls.db_url
        os.environ["ADMIN_USER"] = "itadmin"
        os.environ["ADMIN_PASS"] = "it-admin-pass"
        for name in ("config", "db", "auth", "admin_routes", "reference_routes", "upload_routes", "app"):
            sys.modules.pop(name, None)
        import app as app_module
        import db as db_module
        from ingestion.postgres import PostgresRunRepository
        from ingestion.service import ingest_paths

        cls.db_module = db_module
        db_module.init_schema()
        conn = db_module.get_connection()
        try:
            repo = PostgresRunRepository(conn)
            repo.begin_batch(None, "test")
            cls.report = ingest_paths([DATASET], repo=repo, source="test")
            repo.finish_batch(cls.report)
            conn.commit()
        finally:
            conn.close()
        cls.client = TestClient(app_module.app)
        cls.admin = cls._login("itadmin", "it-admin-pass")

    @classmethod
    def tearDownClass(cls):
        cls.db_module.close_pool()
        _admin_exec(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")

    @classmethod
    def _login(cls, username, password):
        r = cls.client.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return {"X-Tweak-Token": r.json()["token"]}

    def _scalar(self, sql, params=None):
        conn = self.db_module.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()[0]
        finally:
            conn.close()

    # ── ingestion ────────────────────────────────────────────────────────────
    def test_ingestion_records_the_batch(self):
        self.assertEqual(self.report.uploaded, 180)
        self.assertEqual(self._scalar("SELECT uploaded FROM upload_batches ORDER BY id LIMIT 1"), 180)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM data_quality_issues WHERE batch_id IS NULL"), 0)
        self.assertEqual(self._scalar("SELECT COUNT(DISTINCT work_order) FROM test_results"), 1)

    # ── numbers ──────────────────────────────────────────────────────────────
    def test_summary_numbers_for_the_acceptance_work_order(self):
        s = self.client.get("/api/summary").json()
        self.assertEqual(s["attempts"], {"total": 180, "PASS": 121, "FAIL": 13, "STOP": 46, "yield_pct": 67.22})
        self.assertEqual(s["unit_yield"]["first_pass"]["yield_pct"], 72.5)
        self.assertEqual(s["unit_yield"]["latest_result"]["yield_pct"], 99.17)
        self.assertEqual(s["unit_yield"]["any_pass"]["yield_pct"], 99.17)
        self.assertEqual(s["data_quality"]["unknown_mac_attempts"], 7)

    def test_fail_lists_are_filtered_in_sql_and_complete(self):
        fails = self.client.get("/api/fails", params={"limit": 5000}).json()
        self.assertEqual(len(fails), 59)
        self.assertTrue(all(r["result"] in ("FAIL", "STOP") for r in fails))
        page = self.client.get("/api/fail-list", params={"page": 2, "page_size": 50}).json()
        self.assertEqual(page["total"], 59)
        self.assertEqual(len(page["rows"]), 9)

    def test_source_file_is_only_a_file_name(self):
        for row in self.client.get("/api/fails", params={"limit": 5}).json():
            self.assertNotIn("/", row["source_file"])
            self.assertNotIn("\\", row["source_file"])

    def test_iso_week_filter(self):
        week = self.client.get("/api/summary", params={"year": 2026, "week": 30}).json()
        self.assertEqual(week["attempts"]["total"], 180)
        self.assertEqual(self.client.get("/api/summary", params={"year": 2025, "week": 30}).json()["attempts"]["total"], 0)

    def test_metric_top_list_is_deterministic(self):
        first = self.client.get("/api/wifi-analysis").json()
        for _ in range(3):
            self.assertEqual(self.client.get("/api/wifi-analysis").json(), first)

    # ── input validation ─────────────────────────────────────────────────────
    def test_bad_parameters_are_400_not_500(self):
        for params in ({"day": "foo"}, {"day": "2026-13-45"}, {"week": 99}, {"month": 13}, {"year": 1}):
            r = self.client.get("/api/summary", params=params)
            self.assertEqual(r.status_code, 400, params)
            self.assertEqual(r.json()["code"], "validation")
        r = self.client.get("/api/tweak/records", params={"page_size": "abc"}, headers=self.admin)
        self.assertEqual(r.status_code, 400)

    def test_errors_do_not_echo_exception_text(self):
        r = self.client.get("/api/summary", params={"day": "foo"})
        self.assertNotIn("psycopg2", r.text)
        self.assertNotIn("InvalidDatetimeFormat", r.text)

    # ── permissions ──────────────────────────────────────────────────────────
    def test_writes_and_raw_logs_need_a_session(self):
        self.assertEqual(self.client.post("/api/alignment-targets", json={"X": 1}).status_code, 401)
        self.assertEqual(self.client.get("/api/raw-log/1").status_code, 401)
        self.assertEqual(self.client.get("/api/tweak/raw-log/1").status_code, 401)
        self.assertEqual(self.client.get("/api/tweak/records").status_code, 401)
        self.assertEqual(self.client.post("/api/reparse").status_code, 401)
        files = {"files": ("a.txt", b"x", "text/plain")}
        self.assertEqual(self.client.post("/api/upload/", files=files, data={"work_order": "X"}).status_code, 401)

    def test_old_hmac_tokens_signed_with_the_default_secret_are_rejected(self):
        import base64, hashlib, hmac, json, time
        b64 = lambda v: base64.urlsafe_b64encode(v).decode().rstrip("=")
        body = b64(json.dumps({"sub": "itadmin", "exp": int(time.time()) + 3600}).encode())
        forged = body + "." + b64(hmac.new(b"change-me-local-secret", body.encode(), hashlib.sha256).digest())
        r = self.client.get("/api/tweak/records", headers={"X-Tweak-Token": forged})
        self.assertEqual(r.status_code, 401)

    def test_uploader_role(self):
        r = self.client.post("/api/admin/users", headers=self.admin,
                             json={"username": "op.it", "display_name": "IT Op", "password": "uploader-pass"})
        self.assertEqual(r.status_code, 200, r.text)
        up = self._login("op.it", "uploader-pass")
        self.assertEqual(self.client.get("/api/auth/me", headers=up).json()["role"], "uploader")
        self.assertEqual(self.client.get("/api/tweak/records", headers=up).status_code, 403)
        self.assertEqual(self.client.post("/api/alignment-targets", headers=up, json={"X": 1}).status_code, 403)
        self.assertEqual(self.client.get("/api/tweak/raw-log/1", headers=up).status_code, 200)
        self.client.patch("/api/admin/users/op.it", headers=self.admin, json={"enabled": False})
        self.assertEqual(self.client.get("/api/auth/me", headers=up).status_code, 401)

    def test_alignment_targets_validate_and_audit(self):
        r = self.client.post("/api/alignment-targets", headers=self.admin, json={"5101-260715003": "abc"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/alignment-targets", headers=self.admin, json={"5101-260715003": 200})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/alignment-targets").json(), {"5101-260715003": 200})
        self.assertGreaterEqual(self._scalar("SELECT COUNT(*) FROM account_audit WHERE action = 'set_alignment_targets'"), 1)

    def test_deletes_need_confirmation(self):
        r = self.client.delete("/api/tweak/delete-scope", params={"work_order": "NOPE"}, headers=self.admin)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "confirm_required")
        r = self.client.delete("/api/tweak/delete-scope", params={"work_order": "NOPE", "confirm": "DELETE"},
                               headers=self.admin)
        self.assertEqual(r.json(), {"deleted": 0})

    def test_failed_requests_do_not_leak_connections(self):
        before = self._scalar("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = %s", (DB_NAME,))
        for _ in range(30):
            self.client.get("/api/summary", params={"day": "foo"})
            self.client.get("/api/summary")
        after = self._scalar("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = %s", (DB_NAME,))
        self.assertLessEqual(after - before, 8)


if __name__ == "__main__":
    unittest.main()
