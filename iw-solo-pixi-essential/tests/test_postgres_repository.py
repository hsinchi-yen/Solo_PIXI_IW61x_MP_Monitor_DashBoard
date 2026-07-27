import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

from parsers.iw61x import parse_iw61x_log_file


extras_stub = ModuleType("psycopg2.extras")
extras_stub.execute_values = MagicMock()
sys.modules.setdefault("psycopg2.extras", extras_stub)

from ingestion.postgres import PostgresRunRepository


ROOT = Path(__file__).resolve().parents[2]
PASS_LOG = (
    ROOT
    / "rawlogs"
    / "5101-260715003"
    / "20260721_142350_001F7B5806CA_001F7B5806CB_PASS.txt"
)


class FakeCursor:
    def __init__(self):
        self.executemany_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, _sql, _params=None):
        return None

    def executemany(self, sql, rows):
        self.executemany_calls.append((sql, rows))

    def fetchall(self):
        return []

    def fetchone(self):
        return (42,)


class FakeConnection:
    def __init__(self):
        self.cursors = []

    def cursor(self):
        cursor = FakeCursor()
        self.cursors.append(cursor)
        return cursor


class PostgresRepositoryTests(unittest.TestCase):
    def test_save_uses_multi_row_insert_for_measurements(self):
        parsed = parse_iw61x_log_file(PASS_LOG)
        connection = FakeConnection()
        repository = PostgresRunRepository(connection)

        with patch("ingestion.postgres.execute_values") as execute_values:
            result_id = repository.save(parsed)

        self.assertEqual(result_id, 42)
        self.assertGreater(len(parsed.measurements), 1000)
        self.assertEqual(execute_values.call_count, 1)
        measurement_rows = execute_values.call_args.args[2]
        self.assertEqual(len(measurement_rows), len(parsed.measurements))
        self.assertFalse(
            any(cursor.executemany_calls for cursor in connection.cursors),
            "save() must not issue one INSERT command per measurement",
        )


if __name__ == "__main__":
    unittest.main()
