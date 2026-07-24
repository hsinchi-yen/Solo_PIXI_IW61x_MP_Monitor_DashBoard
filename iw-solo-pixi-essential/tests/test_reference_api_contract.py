import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
class ReferenceApiContractTests(unittest.TestCase):
    def test_single_page_dashboard_api_routes_exist(self):
        routes = set()
        for source_file in (ROOT / "api").glob("*.py"):
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call) or not decorator.args:
                        continue
                    func = decorator.func
                    if not isinstance(func, ast.Attribute) or func.attr not in {"get", "post", "delete", "put", "patch"}:
                        continue
                    if not isinstance(decorator.args[0], ast.Constant):
                        continue
                    path = decorator.args[0].value
                    if isinstance(func.value, ast.Name) and func.value.id == "router":
                        if source_file.name == "admin_routes.py":
                            path = f"/api/admin{path}"
                        elif source_file.name == "upload_routes.py":
                            path = f"/api/upload{path}"
                        elif source_file.name == "reference_routes.py":
                            path = f"/api{path}"
                    routes.add((func.attr.upper(), path))
        expected = {
            ("GET", "/api/pass-fail-split"),
            ("GET", "/api/monthly-count"),
            ("GET", "/api/config-analysis"),
            ("GET", "/api/bt-metrics"),
            ("GET", "/api/wifi-metrics"),
            ("GET", "/api/calibration"),
            ("GET", "/api/fails"),
            ("GET", "/api/mac-range"),
            ("GET", "/api/test-duration"),
            ("GET", "/api/alignment-targets"),
            ("POST", "/api/alignment-targets"),
            ("POST", "/api/tweak/login"),
            ("GET", "/api/tweak/records"),
            ("DELETE", "/api/tweak/delete-scope"),
            ("DELETE", "/api/tweak/records/{record_id}"),
            ("GET", "/api/tweak/raw-log/{record_id}"),
            ("POST", "/api/reparse"),
        }
        self.assertTrue(expected.issubset(routes), expected - routes)


if __name__ == "__main__":
    unittest.main()
