import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectContractTests(unittest.TestCase):
    def test_compose_uses_isolated_ports_and_names(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("5434:5432", compose)
        self.assertIn("8003:8003", compose)
        self.assertIn("8004:80", compose)
        self.assertIn("iw-pixi-postgres", compose)
        self.assertIn("iw-pixi-api", compose)
        self.assertIn("iw-pixi-nginx", compose)
        self.assertNotIn("5433:5432", compose)
        self.assertNotIn("8001:80", compose)

    def test_dashboard_pages_and_shared_assets_exist(self):
        pages = [
            "index.html",
            "work-orders.html",
            "fail-list.html",
            "fail-analysis.html",
            "bt-analysis.html",
            "wifi-analysis.html",
            "advanced.html",
            "upload.html",
            "admin.html",
            "alignment.html",
            "styles.css",
            "common.js",
            "chart-lite.js",
            "ai.js",
        ]
        for page in pages:
            self.assertTrue((ROOT / "static" / page).exists(), page)

        script = (ROOT / "static" / "common.js").read_text(encoding="utf-8")
        script += (ROOT / "static" / "ai.js").read_text(encoding="utf-8")
        for endpoint in [
            "/api/summary",
            "/api/yield-trend",
            "/api/work-order-summary",
            "/api/retries",
            "/api/fail-analysis",
            "/api/fail-list",
            "/api/bt-analysis",
            "/api/wifi-analysis",
            "/api/hourly-throughput",
            "/api/upload/",
            "/api/admin/login",
            "/api/admin/alignment-targets",
            "/api/llm-status",
            "/api/workorders/",
        ]:
            self.assertIn(endpoint, script)

    def test_reference_single_page_dashboard_contract(self):
        dashboard = ROOT / "static" / "solo_pixi_dashboard.html"
        self.assertTrue(dashboard.exists())

        html = dashboard.read_text(encoding="utf-8")
        self.assertIn("IW61x Solo PIXI", html)
        self.assertIn('src="/static/vendor/chart.umd.min.js"', html)
        self.assertIn('src="/static/vendor/marked.min.js"', html)
        for page in [
            "overview",
            "workorders",
            "fails",
            "bt",
            "wifi",
            "advanced",
            "failanalysis",
            "dbtweak",
            "dataalign",
            "upload",
        ]:
            self.assertIn(f'id="page-{page}"', html)

        self.assertIn("Aligned KPI", html)
        self.assertIn("Raw KPI", html)
        self.assertIn("Yield %", html)
        self.assertIn("/api/upload/", html)

        chart = ROOT / "static" / "vendor" / "chart.umd.min.js"
        marked = ROOT / "static" / "vendor" / "marked.min.js"
        self.assertTrue(chart.exists())
        self.assertTrue(marked.exists())
        self.assertGreater(chart.stat().st_size, 150_000)
        self.assertGreater(marked.stat().st_size, 30_000)

    def test_schema_declares_normalized_tables(self):
        schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
        for table in [
            "test_results",
            "test_measurements",
            "upload_batches",
            "data_quality_issues",
            "alignment_targets",
        ]:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", schema)

    def test_desktop_uploader_dependencies_are_installable(self):
        requirements = (ROOT / "requirements-desktop.txt").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("PyQt5", requirements)
        self.assertIn("psycopg2-binary", requirements)
        self.assertIn("pip install -r requirements-desktop.txt", readme)


if __name__ == "__main__":
    unittest.main()
