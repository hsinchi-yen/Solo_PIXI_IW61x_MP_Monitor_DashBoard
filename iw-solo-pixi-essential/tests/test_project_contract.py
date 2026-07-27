import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectContractTests(unittest.TestCase):
    def test_compose_uses_configurable_ports_and_project_scoped_names(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("${IW_DB_PORT:-5434}:5432", compose)
        self.assertIn("${IW_API_PORT:-8003}:8003", compose)
        self.assertIn("${IW_WEB_PORT:-8004}:80", compose)
        self.assertNotIn("container_name:", compose)
        self.assertIn("@postgres:5432/", compose)
        self.assertNotIn("5433:5432", compose)
        self.assertNotIn("8001:80", compose)

        nginx = (ROOT / "nginx" / "default.conf").read_text(encoding="utf-8")
        self.assertIn("proxy_pass http://api:8003;", nginx)
        self.assertNotIn("iw-pixi-api", nginx)

    def test_linux_system_up_script_has_port_conflict_and_health_guards(self):
        script_path = ROOT / "system_up.sh"
        self.assertTrue(script_path.exists())
        script = script_path.read_text(encoding="utf-8")

        self.assertIn("set -Eeuo pipefail", script)
        self.assertIn("choose_port", script)
        self.assertIn("IW_API_PORT", script)
        self.assertIn("IW_WEB_PORT", script)
        self.assertIn("IW_DB_PORT", script)
        self.assertIn(".system_up.env", script)
        self.assertIn("docker compose", script)
        self.assertIn("nginx -s reload", script)
        self.assertIn("SYSTEM_UP_PORT_CHECK_ONLY", script)
        self.assertIn("/health", script)

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
        self.assertIn('id="iwUploadFolder"', html)
        self.assertIn("webkitdirectory", html)
        self.assertIn("webkitRelativePath", html)

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
