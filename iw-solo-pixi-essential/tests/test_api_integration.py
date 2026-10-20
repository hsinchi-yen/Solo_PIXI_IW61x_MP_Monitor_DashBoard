"""API tests against a throwaway PostgreSQL database.

Needs a reachable server (default: the local stack on port 5434). Set IW_TEST_PG_URL to a
maintenance-database URL to point elsewhere; the suite is skipped when it cannot connect.
It creates a database named iw_it_<random>, uploads the acceptance work order through the
web Log Uploader (a zip of each station's Log_all.txt), runs the checks and drops it again.
AI is off in these tests (no LLM_ENABLED), which is also what the fallbacks are tested for.
"""
import io
import json
import os
import secrets
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG_ALL = ROOT / "rawlogs" / "TN329_IW611_LOG" / "TN329_IW611_LOG"
SPLIT_DIR = ROOT / "rawlogs" / "5101-260715003"
WO = "5101-260715003"
ADMIN_URL = os.environ.get("IW_TEST_PG_URL", "postgresql://pixi:pixipass@localhost:5434/postgres")

try:
    import psycopg2
    from fastapi.testclient import TestClient
    _probe = psycopg2.connect(ADMIN_URL, connect_timeout=3)
    _probe.close()
    AVAILABLE = (LOG_ALL / "30" / "Log_all.txt").exists()
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


def _work_order_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for station in ("30", "40"):
            z.write(LOG_ALL / station / "Log_all.txt", f"{WO}/{station}/Log_all.txt")
    return buf.getvalue()


def _ndjson(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


@unittest.skipUnless(AVAILABLE, "PostgreSQL or the acceptance logs are not available")
class ApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _admin_exec(f"CREATE DATABASE {DB_NAME}")
        os.environ.update({
            "DATABASE_URL": ADMIN_URL.rsplit("/", 1)[0] + f"/{DB_NAME}",
            "ADMIN_USER": "itadmin", "ADMIN_PASS": "it-admin-pass", "IW_DISABLE_SCHEDULER": "1",
            "UPLOAD_TMP_DIR": f"/tmp/{DB_NAME}", "LLM_ENABLED": "false",
        })
        for name in [m for m in sys.modules if m in ("config", "db", "app", "routers", "services")
                     or m.startswith(("routers.", "services."))]:
            sys.modules.pop(name, None)
        import app as app_module
        import db as db_module
        cls.db_module = db_module
        cls.client = TestClient(app_module.app)
        cls.client.__enter__()                  # runs the lifespan: schema, new tables, views
        cls.admin = cls._login("itadmin", "it-admin-pass")
        r = cls.client.post("/api/admin/users", headers=cls.admin,
                            json={"username": "op.it", "display_name": "IT Op", "password": "uploader-pass"})
        assert r.status_code == 200, r.text
        cls.uploader = cls._login("op.it", "uploader-pass")
        cls.zip_bytes = _work_order_zip()
        cls.first_upload = cls._upload(cls.uploader, 120)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.db_module.close_pool()
        _admin_exec(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")

    @classmethod
    def _login(cls, username, password):
        r = cls.client.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return {"X-Tweak-Token": r.json()["token"]}

    @classmethod
    def _analyze(cls, headers, files=None):
        files = files or [("files", (f"{WO}.zip", cls.zip_bytes, "application/zip"))]
        r = cls.client.post("/api/log-upload/analyze", headers=headers, files=files)
        assert r.status_code == 200, r.text
        return r.json()

    @classmethod
    def _upload(cls, headers, qty):
        upload = cls._analyze(headers)
        r = cls.client.post("/api/log-upload/commit", headers=headers,
                            json={"upload_id": upload["upload_id"], "work_order": WO, "planned_qty": qty, "mode": "form"})
        assert r.status_code == 200, r.text
        events = _ndjson(r)
        assert events[-1]["type"] == "done", events[-1]
        return {"upload": upload, "done": events[-1]}

    def _scalar(self, sql, params=None):
        conn = self.db_module.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()[0]
        finally:
            conn.close()

    # ── web upload ───────────────────────────────────────────────────────────
    def test_analyze_reads_both_stations_like_the_desktop_splitter(self):
        facts = self.first_upload["upload"]["facts"]
        self.assertEqual(facts["total_records"], 180)
        self.assertEqual((facts["pass"], facts["fail"], facts["stop"]), (121, 13, 46))
        self.assertEqual(facts["unique_mac1"], 120)
        self.assertEqual(facts["unknown_mac"], 7)
        self.assertEqual(facts["inferred_wo"], WO)
        self.assertEqual({s["station"]: s["records"] for s in facts["stations"]}, {"30": 78, "40": 102})
        self.assertIn("180", self.first_upload["upload"]["chat_opening"])

    def test_commit_wrote_every_run_and_recorded_the_batch(self):
        self.assertEqual(self.first_upload["done"]["uploaded"], 180)
        upload_id = self.first_upload["upload"]["upload_id"]
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM upload_runs WHERE upload_id = %s", (upload_id,)), 180)
        self.assertEqual(self._scalar("SELECT uploaded FROM upload_batches ORDER BY id LIMIT 1"), 180)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM data_quality_issues WHERE batch_id IS NULL"), 0)
        self.assertEqual(self._scalar("SELECT target_total FROM alignment_targets WHERE work_order = %s", (WO,)), 120)

    def test_web_split_hashes_match_the_desktop_split_files(self):
        files = [("files", (p.name, p.read_bytes(), "text/plain")) for p in sorted(SPLIT_DIR.glob("2026*.txt"))]
        facts = self._analyze(self.uploader, files)["facts"]
        self.assertEqual(facts["existing_by_wo"], {WO: 180})

    def test_rules(self):
        upload_id = self._analyze(self.uploader)["upload_id"]
        check = lambda wo, qty: self.client.post("/api/log-upload/validate", headers=self.uploader,
                                                 json={"upload_id": upload_id, "work_order": wo, "planned_qty": qty}).json()
        codes = lambda v: {i["code"]: i["level"] for i in v["issues"]}
        bad = check("WO-1", "")
        self.assertFalse(bad["can_upload"])
        self.assertEqual(codes(bad)["R1"], "error")
        self.assertEqual(codes(bad)["R2"], "error")
        other = check("5101-260715999", 100)
        self.assertTrue(other["can_upload"])
        self.assertTrue(other["needs_ack"])                     # R3 differs from the zip, R4 rows in another WO
        self.assertEqual(codes(other)["R3"], "warn")
        self.assertEqual(codes(other)["R4"], "warn")
        same = check(WO, 130)
        self.assertEqual(codes(same)["R6"], "info")             # re-upload: all skipped
        self.assertEqual(codes(same)["R7"], "info")             # target 120 → 130

    def test_chat_falls_back_to_the_form_when_ai_is_off(self):
        upload_id = self._analyze(self.uploader)["upload_id"]
        r = self.client.post("/api/log-upload/chat", headers=self.uploader,
                             json={"upload_id": upload_id, "lang": "en",
                                   "messages": [{"role": "user", "content": f"work order {WO}, 120 units"}]})
        events = _ndjson(r)
        self.assertEqual(events[0]["type"], "extract")
        self.assertEqual(events[0]["work_order"], WO)          # regex still reads the work order
        self.assertEqual(events[1]["type"], "validation")
        self.assertEqual(events[-1]["type"], "fallback")

    def test_uploads_are_private_to_their_uploader(self):
        upload_id = self.first_upload["upload"]["upload_id"]
        other = self._login_new_uploader("op.other")
        self.assertEqual(self.client.get(f"/api/log-upload/{upload_id}", headers=other).status_code, 403)
        self.assertEqual(self.client.get(f"/api/log-upload/{upload_id}", headers=self.admin).status_code, 200)
        mine = self.client.get("/api/log-upload/history", headers=self.uploader).json()["items"]
        self.assertTrue(all(i["username"] == "op.it" for i in mine))
        self.assertEqual(self.client.get("/api/log-upload/history", headers=other).json()["items"], [])

    def _login_new_uploader(self, name):
        self.client.post("/api/admin/users", headers=self.admin,
                         json={"username": name, "display_name": name, "password": "uploader-pass"})
        return self._login(name, "uploader-pass")

    def test_undo_removes_only_that_batch_then_upload_again(self):
        second = self._upload(self.uploader, 125)               # all 180 already there → skipped
        self.assertEqual((second["done"]["uploaded"], second["done"]["skipped"]), (0, 180))
        r = self.client.delete(f"/api/log-upload/{self.first_upload['upload']['upload_id']}", headers=self.uploader)
        self.assertEqual(r.status_code, 400)                    # needs confirm=DELETE
        r = self.client.delete(f"/api/log-upload/{self.first_upload['upload']['upload_id']}?confirm=DELETE",
                               headers=self.uploader)
        self.assertEqual(r.json()["reverted"], 180)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM test_results"), 0)
        again = self._upload(self.uploader, 120)
        self.assertEqual(again["done"]["uploaded"], 180)
        type(self).first_upload = again
        self.assertEqual(self.client.get("/api/summary").json()["units"], 120)

    # ── numbers ──────────────────────────────────────────────────────────────
    def test_three_yields_units_and_runs(self):
        s = self.client.get("/api/summary").json()
        self.assertEqual(s["units"], 120)
        self.assertEqual(s["yield"]["latest"], {"pass": 119, "fail": 1, "stop": 0, "yield_pct": 99.17})
        self.assertEqual(s["yield"]["first_pass"]["yield_pct"], 72.5)
        self.assertEqual(s["yield"]["any_pass"]["yield_pct"], 99.17)
        self.assertEqual((s["attempts"]["total"], s["attempts"]["stop"], s["attempts"]["unknown_mac"]), (180, 46, 7))
        self.assertEqual(s["retry_units"], 33)

    def test_overview_defaults_to_the_latest_work_order(self):
        d = self.client.get("/api/overview?latest=true").json()
        self.assertEqual(d["scope"]["params"], {"work_order": WO})
        self.assertEqual(d["summary"]["units"], 120)
        self.assertEqual(d["top_fail_steps"][0]["step"], "(STOP)")
        self.assertEqual(d["top_fail_steps"][1]["step"], "SAVE_NVRAM")
        self.assertEqual(d["top_fail_steps"][1]["attempts"], 13)
        self.assertEqual(sum(p["pass"] + p["fail"] + p["stop"] for p in d["hourly"]["points"]) <= 180, True)

    def test_date_filters_and_iso_week(self):
        self.assertEqual(self.client.get("/api/summary?date_from=2026-07-21&date_to=2026-07-21").json()["attempts"]["total"]
                         + self.client.get("/api/summary?day=2026-07-22").json()["attempts"]["total"], 180)
        self.assertEqual(self.client.get("/api/summary?year=2026&week=30").json()["attempts"]["total"], 180)
        self.assertEqual(self.client.get("/api/summary?year=2025&week=30").json()["attempts"]["total"], 0)

    def test_fail_list_is_complete_and_paged(self):
        page = self.client.get("/api/fails?page=2&page_size=50").json()
        self.assertEqual(page["total"], 59)
        self.assertEqual(len(page["items"]), 9)
        stops = self.client.get("/api/fails?result=STOP&page_size=200").json()
        self.assertEqual(stops["total"], 46)
        for row in page["items"]:
            self.assertNotIn("/", row["source_file"] or "")

    def test_spec_check_and_histograms(self):
        d = self.client.get("/api/measurements").json()
        self.assertEqual(sum(d["spec"]["status"].values()), 271)
        worst = d["spec"]["worst"][0]
        self.assertEqual(worst["status"], "fail")
        per = self.client.get("/api/measurements/items?technology=WIFI&search=PER").json()["items"]
        ofdm = [i for i in per if i["metric_name"] == "PER" and i["rate"] == "OFDM54" and i["band"] == "5GHz"][0]
        self.assertEqual((ofdm["standard"], ofdm["n_fail"]), ("11a", 32))       # legacy rate reported as 11a
        bt_ber = [r for r in d["bt"]["rx"] if "ber" in r]
        self.assertEqual(sorted(r["frequency_mhz"] for r in bt_ber), [2403, 2478])
        h = self.client.get("/api/measurements/histogram", params={
            "technology": "BT", "standard": "BDR", "direction": "TX", "metric_name": "POWER_AVERAGE_DBM"}).json()
        self.assertEqual(h["n"], 136)
        self.assertEqual((h["limit_low"], h["limit_high"]), (-4.0, 10.0))

    def test_alignment_uses_the_latest_result(self):
        d = self.client.get("/api/alignment").json()
        item = d["items"][0]
        self.assertEqual((item["units"], item["target_total"], item["pass"]), (120, 120, 119))
        self.assertEqual(item["aligned_yield"], 99.17)

    def test_reports_say_ai_is_off(self):
        r = self.client.post(f"/api/reports/{WO}/generate")
        events = _ndjson(r)
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["code"], "llm_disabled")
        latest = self.client.get(f"/api/reports/{WO}").json()
        self.assertTrue(latest["has_data"])
        self.assertFalse(latest["llm"]["enabled"])

    def test_queued_report_for_a_removed_work_order_is_skipped(self):
        # An upload queues a report; if the upload is undone before the worker runs it, the job
        # finds no data and must be recorded as skipped, not failed.
        import config
        from services import reports
        with self.db_module.db() as conn:
            job = self.db_module.fetch_one(conn, "INSERT INTO ai_reports (work_order, status, trigger) "
                                                 "VALUES ('9999-000000000', 'queued', 'upload') RETURNING id")["id"]
        original = config.llm_configured
        config.llm_configured = lambda: True
        try:
            reports.process_queue_once()
        finally:
            config.llm_configured = original
        with self.db_module.db() as conn:
            row = self.db_module.fetch_one(conn, "SELECT status, error FROM ai_reports WHERE id = %s", (job,))
            self.db_module.execute(conn, "DELETE FROM ai_reports WHERE id = %s", (job,))
        self.assertEqual((row["status"], row["error"]), ("skipped", "no data"))

    # ── input validation ─────────────────────────────────────────────────────
    def test_bad_parameters_are_400_not_500(self):
        for params in ({"date_from": "foo"}, {"day": "2026-13-45"}, {"week": 99, "year": 2026}, {"month": 13, "year": 2026},
                       {"week": 3}, {"date_from": "2026-07-22", "date_to": "2026-07-21"}):
            r = self.client.get("/api/summary", params=params)
            self.assertEqual(r.status_code, 400, params)
            self.assertNotIn("psycopg2", r.text)

    # ── permissions ──────────────────────────────────────────────────────────
    def test_writes_and_raw_logs_need_a_session(self):
        for method, path in (("post", "/api/alignment-targets"), ("get", "/api/raw-log/1"), ("get", "/api/tweak/records"),
                             ("post", "/api/reparse")):
            r = getattr(self.client, method)(path, **({"json": {}} if method == "post" else {}))
            self.assertEqual(r.status_code, 401, path)
        files = [("files", ("a.txt", b"x", "text/plain"))]
        self.assertEqual(self.client.post("/api/log-upload/analyze", files=files).status_code, 401)

    def test_uploader_cannot_do_administrator_work(self):
        self.assertEqual(self.client.get("/api/tweak/records", headers=self.uploader).status_code, 403)
        self.assertEqual(self.client.post("/api/alignment-targets", headers=self.uploader, json={WO: 1}).status_code, 403)
        self.assertEqual(self.client.get("/api/admin/users", headers=self.uploader).status_code, 403)
        self.assertEqual(self.client.get("/api/raw-log/1", headers=self.uploader).status_code in (200, 404), True)

    def test_old_hmac_tokens_signed_with_the_default_secret_are_rejected(self):
        import base64, hashlib, hmac, time
        b64 = lambda v: base64.urlsafe_b64encode(v).decode().rstrip("=")
        body = b64(json.dumps({"sub": "itadmin", "exp": int(time.time()) + 3600}).encode())
        forged = body + "." + b64(hmac.new(b"change-me-local-secret", body.encode(), hashlib.sha256).digest())
        self.assertEqual(self.client.get("/api/tweak/records", headers={"X-Tweak-Token": forged}).status_code, 401)

    def test_deletes_need_confirmation(self):
        r = self.client.delete("/api/tweak/delete-scope", params={"work_order": "NOPE"}, headers=self.admin)
        self.assertEqual(r.json()["code"], "confirm_required")
        r = self.client.delete("/api/tweak/delete-scope", params={"work_order": "NOPE", "confirm": "DELETE"},
                               headers=self.admin)
        self.assertEqual(r.json()["deleted"], 0)

    def test_failed_requests_do_not_leak_connections(self):
        before = self._scalar("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = %s", (DB_NAME,))
        for _ in range(30):
            self.client.get("/api/summary", params={"day": "foo"})
            self.client.get("/api/summary")
        after = self._scalar("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = %s", (DB_NAME,))
        self.assertLessEqual(after - before, 8)


if __name__ == "__main__":
    unittest.main()
