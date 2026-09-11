import sqlite3
import tempfile
import unittest
from pathlib import Path

from dukielist.storage import SQLiteStorage


class ConnectionTest(unittest.TestCase):
    def test_connection_closed_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(Path(directory) / "tasks.db")
            with storage._connect() as connection:
                self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_rollback_and_close_after_error(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(Path(directory) / "tasks.db")
            with storage._connect() as setup:
                setup.execute("CREATE TABLE rollback_test (value INTEGER)")
            with self.assertRaises(RuntimeError):
                with storage._connect() as connection:
                    connection.execute("INSERT INTO rollback_test VALUES (42)")
                    raise RuntimeError("Falha simulada")
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
            with storage._connect() as check:
                self.assertEqual(check.execute("SELECT COUNT(*) FROM rollback_test").fetchone()[0], 0)
