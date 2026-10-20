"""Every API path the web pages call exists on the server (AGENTS.md verification item 7).

The new dashboard (web/js) and the legacy page (web/legacy.html, whose /api/… calls are
rewritten to /api/legacy/…) are both checked against the FastAPI route table.
"""
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("IW_DISABLE_SCHEDULER", "1")

QUOTED_RE = re.compile(r"""['"](/api/[A-Za-z0-9_\-/]*)['"?]""")
TEMPLATE_RE = re.compile(r"`(/api/[^`]*)`")


def _client():
    """A client for route matching only: no lifespan (no DB), no static files behind /api."""
    os.environ["WEB_DIR"] = "/nonexistent"
    for name in [m for m in sys.modules if m in ("config", "app")]:
        sys.modules.pop(name, None)
    import app
    from fastapi.testclient import TestClient
    return TestClient(app.app)


def _called_paths(text):
    """'/api/x' strings and `/api/x/${id}/y${query}` templates → '/api/x', '/api/x/1/y'."""
    found = list(QUOTED_RE.findall(text))
    for tpl in TEMPLATE_RE.findall(text):
        tpl = re.sub(r"(?<=/)\$\{[^{}]*\}", "1", tpl)         # path segments
        found.append(re.split(r"\$\{|\?", tpl)[0])          # drop appended query strings
    out = set()
    for path in found:
        path = path.rstrip("/")
        if path not in ("/api", "/api/auth", "/api/legacy"):      # prefixes in the legacy fetch rewrite
            out.add(path)
    return out


class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = _client()

    def assertServed(self, path):
        # A route that exists answers an unsupported method with 405; an unknown path gives 404.
        status = self.client.request("TRACE", path).status_code
        self.assertEqual(status, 405, f"no route for {path}")

    def test_new_dashboard_calls_existing_routes(self):
        text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "web" / "js").rglob("*.js"))
        paths = _called_paths(text)
        self.assertGreater(len(paths), 25)
        for path in sorted(paths):
            with self.subTest(path=path):
                self.assertServed(path)

    def test_legacy_page_calls_existing_routes(self):
        html = (ROOT / "web" / "legacy.html").read_text(encoding="utf-8")
        paths = {p for p in _called_paths(html) if not p.startswith("/api/legacy")}
        self.assertGreater(len(paths), 15)
        for path in sorted(paths):
            served = path if path.startswith(("/api/auth/", "/api/legacy/")) else "/api/legacy/" + path[5:]
            with self.subTest(path=path):
                self.assertServed(served)


if __name__ == "__main__":
    unittest.main()
