import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from dukielist.models import Category, Priority, Status, Task
from dukielist.storage import SQLiteStorage


class ConnectionTest(unittest.TestCase):
    @staticmethod
    def _task(*, status=Status.PENDING):
        return Task(
            id=None,
            title="Cartão",
            description="Trello",
            task_date=date(2026, 9, 20),
            task_time=None,
            priority=Priority.MEDIUM,
            category=Category.PERSONAL,
            status=status,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    def _linked_task(self, storage):
        task = self._task()
        storage.sync_external(
            task,
            source="trello",
            external_id="card-1",
            external_updated_at="v1",
            external_url="https://trello.com/c/card-1",
        )
        return task

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

    def test_external_sync_is_idempotent_and_cascades_on_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(Path(directory) / "tasks.db")
            task = self._task()
            result = storage.sync_external(
                task,
                source="trello",
                external_id="card-1",
                external_updated_at="v1",
                external_url="https://trello.com/c/card-1",
            )
            self.assertEqual(result, "created")
            task_id = task.id
            self.assertEqual(storage.external_task_id("trello", "card-1"), task_id)

            task.title = "Alteração remota"
            self.assertEqual(
                storage.sync_external(
                    task,
                    source="trello",
                    external_id="card-1",
                    external_updated_at="v1",
                    external_url="https://trello.com/c/card-1",
                ),
                "unchanged",
            )
            self.assertEqual(storage.get(task_id).title, "Cartão")

            self.assertEqual(
                storage.sync_external(
                    task,
                    source="trello",
                    external_id="card-1",
                    external_updated_at="v2",
                    external_url="https://trello.com/c/card-1",
                ),
                "updated",
            )
            self.assertEqual(storage.get(task_id).title, "Alteração remota")
            self.assertTrue(storage.delete(task_id))
            self.assertIsNone(storage.external_task_id("trello", "card-1"))

    def test_local_status_change_is_queued_and_remote_import_cannot_overwrite_it(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(Path(directory) / "tasks.db")
            task = self._linked_task(storage)
            task.status = Status.COMPLETED
            task.updated_at = datetime.now()
            storage.update(task)

            pending = storage.pending_external_completions("trello")
            self.assertEqual(len(pending), 1)
            self.assertIs(pending[0]["pending_completed"], True)

            stale_remote = self._task(status=Status.PENDING)
            result = storage.sync_external(
                stale_remote,
                source="trello",
                external_id="card-1",
                external_updated_at="v2",
                external_url="https://trello.com/c/card-1",
            )
            self.assertEqual(result, "unchanged")
            self.assertTrue(storage.get(task.id).completed)

    def test_acknowledgement_does_not_erase_a_newer_local_change(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(Path(directory) / "tasks.db")
            task = self._linked_task(storage)
            task.status = Status.COMPLETED
            storage.update(task)
            task.status = Status.PENDING
            storage.update(task)

            acknowledged = storage.mark_external_completion_synced(
                source="trello",
                external_id="card-1",
                completed=True,
                external_updated_at="v2",
            )
            self.assertFalse(acknowledged)
            pending = storage.pending_external_completions("trello")
            self.assertEqual(pending[0]["pending_completed"], False)

    def test_remote_status_applies_only_without_pending_local_write(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(Path(directory) / "tasks.db")
            task = self._linked_task(storage)

            self.assertTrue(storage.apply_external_completion(
                source="trello",
                external_id="card-1",
                completed=True,
                external_updated_at="v2",
            ))
            self.assertTrue(storage.get(task.id).completed)
            self.assertEqual(storage.pending_external_completions("trello"), [])

            local = storage.get(task.id)
            local.status = Status.PENDING
            storage.update(local)
            self.assertFalse(storage.apply_external_completion(
                source="trello",
                external_id="card-1",
                completed=True,
                external_updated_at="v3",
            ))
            self.assertFalse(storage.get(task.id).completed)

    def test_existing_database_is_migrated_for_completion_outbox(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "old.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    task_date TEXT NOT NULL, task_time TEXT, priority TEXT NOT NULL,
                    category TEXT NOT NULL, status TEXT NOT NULL,
                    view_mode TEXT NOT NULL DEFAULT 'day', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE external_task_links (
                    source TEXT NOT NULL, external_id TEXT NOT NULL,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    external_updated_at TEXT, external_url TEXT,
                    PRIMARY KEY (source, external_id), UNIQUE (source, task_id)
                );
                """
            )
            connection.close()

            storage = SQLiteStorage(db_path)
            with storage._connect() as migrated:
                columns = {
                    row["name"]
                    for row in migrated.execute("PRAGMA table_info(external_task_links)")
                }
            self.assertIn("pending_completed", columns)
