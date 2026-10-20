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
        for name in ("iw-pixi-pgdata", "iw-pixi-uploads", "iw-pixi-net"):
            self.assertIn(name, compose)
        # AI stays off unless the server's .env turns it on (decision Q13).
        self.assertIn("LLM_ENABLED: ${LLM_ENABLED:-false}", compose)
        self.assertIn("LLM_API_BASE: ${LLM_API_BASE:-}", compose)

        nginx = (ROOT / "nginx" / "default.conf").read_text(encoding="utf-8")
        self.assertIn("server api:8003;", nginx)
        self.assertIn("root /usr/share/nginx/html;", nginx)
        self.assertIn("proxy_buffering off;", nginx)

    def test_code_is_baked_into_the_image(self):
        dockerfile = (ROOT / "api" / "Dockerfile").read_text(encoding="utf-8")
        for line in ("COPY parsers/ /app/parsers/", "COPY ingestion/ /app/ingestion/", "COPY api/ /app/api/",
                     "COPY schema.sql /app/schema.sql", "COPY web/ /app/web/"):
            self.assertIn(line, dockerfile)
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
        self.assertNotIn("web/", ignored, "the API serves the dashboard on 8003, so web/ must be in the image")
        self.assertIn("USER iw", dockerfile)

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
        self.assertIn("SYSTEM_UP_PORT_CHECK_ONLY", script)
        self.assertIn("--force-recreate nginx", script)
        self.assertIn("/health", script)

    def test_deploy_scripts_back_up_and_roll_back(self):
        deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
        for text in ("pg_dump", "oldbackup/$(date +%y%m%d)", "--exclude '.env'", "--exclude '.system_up.env'",
                     "bash system_up.sh", "health check FAILED", "tar xzf"):
            self.assertIn(text, deploy)
        backup = (ROOT / "scripts" / "backup_db.sh").read_text(encoding="utf-8")
        self.assertIn("KEEP_DAYS:-14", backup)

    def test_web_app_files_exist(self):
        web = ROOT / "web"
        for rel in ["index.html", "css/theme.css", "css/base.css", "css/pages.css", "js/main.js",
                    "js/report.js", "js/core/api.js", "js/core/i18n.js", "js/core/strings.en.js",
                    "js/core/strings.zh.js", "vendor/chart.umd-4.4.1.min.js"]:
            self.assertTrue((web / rel).exists(), rel)
        # Reports are rendered as escaped text, not Markdown, so no Markdown parser / sanitizer is shipped.
        self.assertEqual(sorted(p.name for p in (web / "vendor").iterdir()), ["chart.umd-4.4.1.min.js"])
        for page in ["overview", "workorders", "fails", "measurements", "failanalysis", "dbtweak", "dataalign",
                     "logupload", "accounts"]:
            self.assertTrue((web / "js" / "pages" / f"{page}.js").exists(), page)
        html = (web / "index.html").read_text(encoding="utf-8")
        for page in ["overview", "workorders", "fails", "measurements", "failanalysis", "dbtweak", "dataalign",
                     "logupload", "accounts"]:
            self.assertIn(f'id="page-{page}"', html)
        self.assertFalse((ROOT / "static").exists(), "the pre-2.0 static/ pages were removed (Q23)")
        self.assertFalse((web / "legacy.html").exists(), "the pre-2.0 page is removed with the 2.0 deploy (Q28)")
        self.assertFalse((ROOT / "api" / "legacy").exists())

    def test_schema_declares_normalized_tables(self):
        schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
        for table in ["test_results", "test_measurements", "upload_batches", "data_quality_issues",
                      "alignment_targets", "app_users", "app_sessions"]:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", schema)

    def test_desktop_uploader_dependencies_are_installable(self):
        requirements = (ROOT / "requirements-desktop.txt").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("PyQt5", requirements)
        self.assertIn("psycopg2-binary", requirements)
        self.assertIn("pip install -r requirements-desktop.txt", readme)


if __name__ == "__main__":
    unittest.main()
