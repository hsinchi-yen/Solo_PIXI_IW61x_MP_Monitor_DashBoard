import sys
import unittest
from unittest.mock import MagicMock, patch

sys.modules.setdefault("psycopg2", MagicMock())

from api import db


class DatabaseConfigurationTests(unittest.TestCase):
    @patch("api.db.psycopg2.connect")
    def test_schema_initialization_uses_the_selected_database_url(self, connect):
        selected_url = "postgresql://pixi:pixipass@localhost:5434/selected_database"
        connection = MagicMock()
        connect.return_value = connection

        db.init_schema(selected_url)

        connect.assert_called_once_with(selected_url, connect_timeout=5)
        cursor = connection.cursor.return_value.__enter__.return_value
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn("pg_advisory_xact_lock", statements[0])
        self.assertIn("CREATE TABLE IF NOT EXISTS test_results", statements[1])
        connection.commit.assert_called_once()
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
