from __future__ import annotations

import sqlite3
from datetime import date
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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_date ON tasks(task_date);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                """
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
            task.created_at.isoformat(timespec="seconds"),
            task.updated_at.isoformat(timespec="seconds"),
        )

    def create(self, task: Task) -> Task:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tasks
                (title, description, task_date, task_time, priority, category,
                 status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            connection.execute(
                """
                UPDATE tasks SET title=?, description=?, task_date=?, task_time=?,
                    priority=?, category=?, status=?, updated_at=?
                WHERE id=?
                """,
                (*values[:7], values[8], task.id),
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
