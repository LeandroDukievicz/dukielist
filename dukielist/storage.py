from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .models import Category, Priority, Status, Task


MIRROR_SETTING_KEY = "mirror_sources"


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
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    all_day INTEGER NOT NULL DEFAULT 0,
                    priority TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL,
                    view_mode TEXT NOT NULL DEFAULT 'day',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
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
                CREATE TABLE IF NOT EXISTS external_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    task_id INTEGER NOT NULL,
                    external_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_external_outbox_entry
                    ON external_outbox(source, operation, task_id);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
            if "view_mode" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN view_mode TEXT NOT NULL DEFAULT 'day'")
            legacy_date = "task_date" in columns
            legacy_time = "task_time" in columns
            if "start_date" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN start_date TEXT")
                if legacy_date:
                    connection.execute("UPDATE tasks SET start_date=task_date")
            if "end_date" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN end_date TEXT")
                if legacy_date:
                    connection.execute("UPDATE tasks SET end_date=task_date")
            if "start_time" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN start_time TEXT")
                if legacy_time:
                    connection.execute("UPDATE tasks SET start_time=task_time")
            if "end_time" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN end_time TEXT")
            if "all_day" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN all_day INTEGER NOT NULL DEFAULT 0")
                if legacy_time:
                    connection.execute(
                        "UPDATE tasks SET all_day=CASE WHEN task_time IS NULL THEN 1 ELSE 0 END"
                    )

            # Old tasks had a single date and, optionally, a start time. Keep
            # their date and turn timed entries into one-hour appointments.
            pending = connection.execute(
                "SELECT id, start_date, end_date, start_time, end_time, all_day "
                "FROM tasks WHERE end_date IS NULL OR (all_day=0 AND end_time IS NULL)"
            ).fetchall()
            for row in pending:
                start_date = date.fromisoformat(row["start_date"] or date.today().isoformat())
                end_date = date.fromisoformat(row["end_date"]) if row["end_date"] else start_date
                start_time = time.fromisoformat(row["start_time"]) if row["start_time"] else None
                end_time = time.fromisoformat(row["end_time"]) if row["end_time"] else None
                all_day = bool(row["all_day"])
                if not all_day and start_time is not None and end_time is None:
                    end = datetime.combine(start_date, start_time) + timedelta(hours=1)
                    end_date, end_time = end.date(), end.time()
                connection.execute(
                    "UPDATE tasks SET end_date=?, end_time=? WHERE id=?",
                    (end_date.isoformat(), end_time.isoformat() if end_time else None, row["id"]),
                )
            connection.execute("DROP INDEX IF EXISTS idx_tasks_date")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_start_date ON tasks(start_date)"
            )
            self._legacy_task_columns = {
                name for name in ("task_date", "task_time") if name in columns
            }
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

    def mirror_sources(self) -> tuple[str, ...]:
        """Integrações que recebem uma cópia de cada alteração local."""
        with self._connect() as connection:
            return self._mirror_sources(connection)

    @staticmethod
    def _mirror_sources(connection) -> tuple[str, ...]:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key=?", (MIRROR_SETTING_KEY,)
        ).fetchone()
        if not row:
            return ()
        return tuple(part for part in str(row["value"]).split(",") if part)

    def enable_mirror(self, source: str) -> None:
        with self._connect() as connection:
            sources = set(self._mirror_sources(connection)) | {source}
            connection.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (MIRROR_SETTING_KEY, ",".join(sorted(sources))),
            )

    def disable_mirror(self, source: str) -> None:
        with self._connect() as connection:
            sources = set(self._mirror_sources(connection)) - {source}
            connection.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (MIRROR_SETTING_KEY, ",".join(sorted(sources))),
            )
            connection.execute("DELETE FROM external_outbox WHERE source=?", (source,))

    @staticmethod
    def _enqueue(
        connection,
        source: str,
        operation: str,
        task_id: int,
        external_id: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO external_outbox (source, operation, task_id, external_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source, operation, task_id) DO UPDATE SET
                external_id=excluded.external_id, created_at=excluded.created_at
            """,
            (
                source,
                operation,
                task_id,
                external_id,
                datetime.now().isoformat(timespec="microseconds"),
            ),
        )

    def _enqueue_upsert(self, connection, task_id: int, *, skip: str | None = None) -> None:
        for source in self._mirror_sources(connection):
            if source != skip:
                self._enqueue(connection, source, "upsert", task_id)

    def pending_outbox(self, source: str) -> list[dict[str, object]]:
        """Alterações locais aguardando envio, na ordem em que aconteceram."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, operation, task_id, external_id, created_at FROM external_outbox
                WHERE source=? ORDER BY id
                """,
                (source,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "operation": str(row["operation"]),
                "task_id": int(row["task_id"]),
                "external_id": str(row["external_id"]) if row["external_id"] else None,
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def enqueue_unlinked_tasks(self, source: str) -> None:
        """Prepara tarefas anteriores à ativação de uma integração."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT tasks.id FROM tasks
                WHERE NOT EXISTS (
                    SELECT 1 FROM external_task_links AS links
                    WHERE links.source=? AND links.task_id=tasks.id
                )
                """,
                (source,),
            ).fetchall()
            for row in rows:
                self._enqueue(connection, source, "upsert", int(row["id"]))

    def resolve_outbox(self, entry_id: int, *, created_at: str | None = None) -> bool:
        """Remove um item da fila, a menos que ele tenha mudado desde a leitura."""
        with self._connect() as connection:
            if created_at is None:
                cursor = connection.execute(
                    "DELETE FROM external_outbox WHERE id=?", (entry_id,)
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM external_outbox WHERE id=? AND created_at=?",
                    (entry_id, created_at),
                )
        return cursor.rowcount > 0

    def link_external(
        self,
        *,
        source: str,
        external_id: str,
        task_id: int,
        external_updated_at: str | None = None,
        external_url: str | None = None,
    ) -> None:
        """Registra o item remoto criado a partir de uma tarefa local."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO external_task_links
                (source, external_id, task_id, external_updated_at, external_url)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, external_id) DO UPDATE SET
                    task_id=excluded.task_id,
                    external_updated_at=excluded.external_updated_at,
                    external_url=excluded.external_url,
                    pending_completed=NULL
                """,
                (source, external_id, task_id, external_updated_at, external_url),
            )

    def external_id_for_task(self, source: str, task_id: int) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT external_id FROM external_task_links WHERE source=? AND task_id=?",
                (source, task_id),
            ).fetchone()
        return str(row["external_id"]) if row else None

    def unlink_external(self, source: str, external_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM external_task_links WHERE source=? AND external_id=?",
                (source, external_id),
            )
        return cursor.rowcount > 0

    def _task_fields(self, task: Task) -> tuple[list[str], list[object]]:
        fields = [
            "title", "description", "start_date", "end_date", "start_time",
            "end_time", "all_day", "priority", "category", "status", "view_mode",
            "created_at", "updated_at",
        ]
        values: list[object] = [
            task.title,
            task.description,
            task.start_date.isoformat(),
            task.end_date.isoformat(),
            task.start_time.isoformat() if task.start_time else None,
            task.end_time.isoformat() if task.end_time else None,
            int(task.all_day),
            task.priority.value,
            task.category.value,
            task.status.value,
            task.view_mode.value,
            task.created_at.isoformat(timespec="seconds"),
            task.updated_at.isoformat(timespec="seconds"),
        ]
        if "task_date" in self._legacy_task_columns:
            fields.append("task_date")
            values.append(task.start_date.isoformat())
        if "task_time" in self._legacy_task_columns:
            fields.append("task_time")
            values.append(task.start_time.isoformat() if task.start_time else None)
        return fields, values

    def _insert_task(self, connection, task: Task) -> int:
        fields, values = self._task_fields(task)
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        cursor = connection.execute(
            f"INSERT INTO tasks ({columns}) VALUES ({placeholders})", values
        )
        return int(cursor.lastrowid)

    def _update_task(self, connection, task: Task) -> None:
        if task.id is None:
            raise ValueError("Não é possível atualizar uma tarefa sem id.")
        fields, values = self._task_fields(task)
        updates = [(field, value) for field, value in zip(fields, values) if field != "created_at"]
        assignments = ", ".join(f"{field}=?" for field, _ in updates)
        connection.execute(
            f"UPDATE tasks SET {assignments} WHERE id=?",
            (*[value for _, value in updates], task.id),
        )

    def create(self, task: Task) -> Task:
        with self._connect() as connection:
            task.id = self._insert_task(connection, task)
            self._enqueue_upsert(connection, task.id)
        return task

    def update(self, task: Task) -> Task:
        if task.id is None:
            raise ValueError("Não é possível atualizar uma tarefa sem id.")
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT status FROM tasks WHERE id=?", (task.id,)
            ).fetchone()
            self._update_task(connection, task)
            if previous is not None and previous["status"] != task.status.value:
                connection.execute(
                    """
                    UPDATE external_task_links
                    SET pending_completed=?
                    WHERE task_id=?
                    """,
                    (int(task.completed), task.id),
                )
            if previous is not None:
                self._enqueue_upsert(connection, task.id)
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
        clauses = [
            "start_date < ?",
            "(end_date > ? OR (end_date = ? AND (all_day=1 OR end_time > '00:00:00')))",
        ]
        params: list[object] = [end.isoformat(), start.isoformat(), start.isoformat()]
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
                ORDER BY start_date ASC,
                         CASE WHEN all_day=1 THEN 0 ELSE 1 END,
                         start_time ASC, end_date ASC, end_time ASC, id ASC
                """,
                params,
            ).fetchall()
        return [Task.from_row(row) for row in rows]

    def delete(self, task_id: int) -> bool:
        with self._connect() as connection:
            mirrors = self._mirror_sources(connection)
            # The links disappear with the task (ON DELETE CASCADE), so the
            # remote ids have to be read while the row still exists.
            linked = {
                str(row["source"]): str(row["external_id"])
                for row in connection.execute(
                    "SELECT source, external_id FROM external_task_links WHERE task_id=?",
                    (task_id,),
                )
            }
            cursor = connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            if cursor.rowcount:
                # A queued upsert for a task that no longer exists would only
                # recreate what the delete is about to remove.
                connection.execute(
                    "DELETE FROM external_outbox WHERE task_id=? AND operation='upsert'",
                    (task_id,),
                )
                for source in mirrors:
                    external_id = linked.get(source)
                    if external_id:
                        self._enqueue(connection, source, "delete", task_id, external_id)
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
            # "Não concluída" é uma pendência que o usuário já resolveu em
            # definitivo. Como as integrações só conhecem feito/não feito, uma
            # leitura remota de "não feito" não deve apagar essa decisão.
            if not completed and link["status"] == Status.MISSED.value:
                status = Status.MISSED
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
                self._enqueue_upsert(connection, int(link["task_id"]), skip=source)
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
                task.id = self._insert_task(connection, task)
                connection.execute(
                    """
                    INSERT INTO external_task_links
                    (source, external_id, task_id, external_updated_at, external_url)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (source, external_id, task.id, external_updated_at, external_url),
                )
                self._enqueue_upsert(connection, task.id, skip=source)
                return "created"

            task.id = int(link["task_id"])
            if link["pending_completed"] is not None:
                return "unchanged"
            if link["external_updated_at"] == external_updated_at:
                return "unchanged"

            self._update_task(connection, task)
            connection.execute(
                """
                UPDATE external_task_links
                SET external_updated_at=?, external_url=?
                WHERE source=? AND external_id=?
                """,
                (external_updated_at, external_url, source, external_id),
            )
            self._enqueue_upsert(connection, task.id, skip=source)
            return "updated"
