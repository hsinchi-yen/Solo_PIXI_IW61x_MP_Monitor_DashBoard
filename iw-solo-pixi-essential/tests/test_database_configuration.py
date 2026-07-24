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
        connection.cursor.return_value.execute.assert_called_once()
        connection.commit.assert_called_once()
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
