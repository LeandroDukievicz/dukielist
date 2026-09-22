from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from .models import Category, Priority, Status, Task


class SQLiteStorage:
    """Persistência local transacional, sem dependências externas."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path).expanduser() if db_path else (
            Path.home() / ".local" / "share" / "dukielist" / "dukielist.db"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        # Multiple terminal instances can share the same database. Give short
        # writes enough time to finish instead of failing immediately with
        # "database is locked" while another instance is saving a task.
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 30000")
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    task_date TEXT NOT NULL,
                    task_time TEXT,
                    priority TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL,
                    view_mode TEXT NOT NULL DEFAULT 'day',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_date ON tasks(task_date);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE TABLE IF NOT EXISTS external_task_links (
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    external_updated_at TEXT,
                    external_url TEXT,
                    pending_completed INTEGER,
                    PRIMARY KEY (source, external_id),
                    UNIQUE (source, task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_external_task_links_task
                    ON external_task_links(task_id);
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
            if "view_mode" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN view_mode TEXT NOT NULL DEFAULT 'day'")
            link_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(external_task_links)")
            }
            if "pending_completed" not in link_columns:
                connection.execute(
                    "ALTER TABLE external_task_links ADD COLUMN pending_completed INTEGER"
                )

    def get_setting(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key=?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    @staticmethod
    def _task_values(task: Task) -> tuple[object, ...]:
        return (
            task.title,
            task.description,
            task.task_date.isoformat(),
            task.task_time.isoformat() if task.task_time else None,
            task.priority.value,
            task.category.value,
            task.status.value,
            task.view_mode.value,
            task.created_at.isoformat(timespec="seconds"),
            task.updated_at.isoformat(timespec="seconds"),
        )

    def create(self, task: Task) -> Task:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tasks
                (title, description, task_date, task_time, priority, category,
                 status, view_mode, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._task_values(task),
            )
            task.id = int(cursor.lastrowid)
        return task

    def update(self, task: Task) -> Task:
        if task.id is None:
            raise ValueError("Não é possível atualizar uma tarefa sem id.")
        values = self._task_values(task)
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT status FROM tasks WHERE id=?", (task.id,)
            ).fetchone()
            connection.execute(
                """
                UPDATE tasks SET title=?, description=?, task_date=?, task_time=?,
                    priority=?, category=?, status=?, view_mode=?, updated_at=?
                    WHERE id=?
                """,
                (*values[:7], values[7], values[9], task.id),
            )
            if previous is not None and previous["status"] != task.status.value:
                connection.execute(
                    """
                    UPDATE external_task_links
                    SET pending_completed=?
                    WHERE task_id=?
                    """,
                    (int(task.completed), task.id),
                )
        return task

    def get(self, task_id: int) -> Task | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return Task.from_row(row) if row else None

    def list_between(
        self,
        start: date,
        end: date,
        *,
        category: Category | None = None,
        priority: Priority | None = None,
        status: Status | None = None,
        query: str = "",
    ) -> list[Task]:
        clauses = ["task_date >= ?", "task_date < ?"]
        params: list[object] = [start.isoformat(), end.isoformat()]
        if category:
            clauses.append("category = ?")
            params.append(category.value)
        if priority:
            clauses.append("priority = ?")
            params.append(priority.value)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if query.strip():
            clauses.append("(title LIKE ? OR description LIKE ?)")
            search = f"%{query.strip()}%"
            params.extend((search, search))
        where = " AND ".join(clauses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM tasks
                WHERE {where}
                ORDER BY task_date ASC,
                         CASE WHEN task_time IS NULL THEN 1 ELSE 0 END,
                         task_time ASC, id ASC
                """,
                params,
            ).fetchall()
        return [Task.from_row(row) for row in rows]

    def delete(self, task_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return cursor.rowcount > 0

    def external_task_id(self, source: str, external_id: str) -> int | None:
        """Retorna a tarefa vinculada a um item externo, quando existir."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT task_id FROM external_task_links
                WHERE source=? AND external_id=?
                """,
                (source, external_id),
            ).fetchone()
        return int(row["task_id"]) if row else None

    def external_completion_links(self, source: str) -> list[dict[str, object]]:
        """Return linked tasks and their local/remote completion metadata."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT links.external_id, links.task_id,
                       links.external_updated_at, links.pending_completed,
                       tasks.status
                FROM external_task_links AS links
                JOIN tasks ON tasks.id = links.task_id
                WHERE links.source=?
                ORDER BY links.external_id
                """,
                (source,),
            ).fetchall()
        return [
            {
                "external_id": str(row["external_id"]),
                "task_id": int(row["task_id"]),
                "external_updated_at": row["external_updated_at"],
                "pending_completed": (
                    None
                    if row["pending_completed"] is None
                    else bool(row["pending_completed"])
                ),
                "completed": row["status"] == Status.COMPLETED.value,
            }
            for row in rows
        ]

    def pending_external_completions(self, source: str) -> list[dict[str, object]]:
        return [
            link
            for link in self.external_completion_links(source)
            if link["pending_completed"] is not None
        ]

    def mark_external_completion_synced(
        self,
        *,
        source: str,
        external_id: str,
        completed: bool,
        external_updated_at: str | None,
        require_pending: bool = True,
    ) -> bool:
        """Acknowledge an outbound status without erasing a newer local change."""
        with self._connect() as connection:
            if require_pending:
                cursor = connection.execute(
                    """
                    UPDATE external_task_links
                    SET pending_completed=NULL, external_updated_at=?
                    WHERE source=? AND external_id=? AND pending_completed=?
                    """,
                    (external_updated_at, source, external_id, int(completed)),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE external_task_links
                    SET external_updated_at=?
                    WHERE source=? AND external_id=? AND pending_completed IS NULL
                    """,
                    (external_updated_at, source, external_id),
                )
        return cursor.rowcount > 0

    def apply_external_completion(
        self,
        *,
        source: str,
        external_id: str,
        completed: bool,
        external_updated_at: str | None,
    ) -> bool:
        """Apply a remote status unless a newer local status is waiting to upload."""
        with self._connect() as connection:
            link = connection.execute(
                """
                SELECT links.task_id, links.pending_completed, tasks.status
                FROM external_task_links AS links
                JOIN tasks ON tasks.id = links.task_id
                WHERE links.source=? AND links.external_id=?
                """,
                (source, external_id),
            ).fetchone()
            if link is None or link["pending_completed"] is not None:
                return False

            status = Status.COMPLETED if completed else Status.PENDING
            changed = link["status"] != status.value
            if changed:
                connection.execute(
                    "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                    (
                        status.value,
                        datetime.now().isoformat(timespec="seconds"),
                        int(link["task_id"]),
                    ),
                )
            connection.execute(
                """
                UPDATE external_task_links SET external_updated_at=?
                WHERE source=? AND external_id=?
                """,
                (external_updated_at, source, external_id),
            )
        return changed

    def sync_external(
        self,
        task: Task,
        *,
        source: str,
        external_id: str,
        external_updated_at: str | None,
        external_url: str | None,
    ) -> str:
        """Cria ou atualiza uma tarefa externa de forma idempotente.

        Alterações locais são preservadas enquanto o item remoto não mudar. O
        retorno é ``created``, ``updated`` ou ``unchanged``.
        """
        with self._connect() as connection:
            link = connection.execute(
                """
                SELECT task_id, external_updated_at, external_url, pending_completed
                FROM external_task_links
                WHERE source=? AND external_id=?
                """,
                (source, external_id),
            ).fetchone()

            if link is None:
                cursor = connection.execute(
                    """
                    INSERT INTO tasks
                    (title, description, task_date, task_time, priority, category,
                     status, view_mode, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._task_values(task),
                )
                task.id = int(cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO external_task_links
                    (source, external_id, task_id, external_updated_at, external_url)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (source, external_id, task.id, external_updated_at, external_url),
                )
                return "created"

            task.id = int(link["task_id"])
            if link["pending_completed"] is not None:
                return "unchanged"
            if link["external_updated_at"] == external_updated_at:
                return "unchanged"

            values = self._task_values(task)
            connection.execute(
                """
                UPDATE tasks SET title=?, description=?, task_date=?, task_time=?,
                    priority=?, category=?, status=?, view_mode=?, updated_at=?
                    WHERE id=?
                """,
                (*values[:8], values[9], task.id),
            )
            connection.execute(
                """
                UPDATE external_task_links
                SET external_updated_at=?, external_url=?
                WHERE source=? AND external_id=?
                """,
                (external_updated_at, external_url, source, external_id),
            )
            return "updated"
