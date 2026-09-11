from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .models import Category, Priority, Status, Task, ViewMode
from .storage import SQLiteStorage


@dataclass(slots=True)
class TaskFilters:
    category: Category | None = None
    priority: Priority | None = None
    status: Status | None = None
    query: str = ""

    @property
    def active(self) -> bool:
        return any((self.category, self.priority, self.status, self.query.strip()))


@dataclass(frozen=True, slots=True)
class Progress:
    total: int
    completed: int

    @property
    def percent(self) -> int:
        return round(self.completed / self.total * 100) if self.total else 0


class TaskService:
    def __init__(self, storage: SQLiteStorage) -> None:
        self.storage = storage

    def list_between(self, start: date, end: date, filters: TaskFilters | None = None) -> list[Task]:
        filters = filters or TaskFilters()
        return self.storage.list_between(
            start, end, category=filters.category, priority=filters.priority,
            status=filters.status, query=filters.query,
        )

    def get(self, task_id: int) -> Task | None:
        return self.storage.get(task_id)

    def create(
        self, *, title: str, description: str, task_date: date, task_time: time | None,
        priority: Priority, category: Category, status: Status = Status.PENDING,
        view_mode: ViewMode = ViewMode.DAY,
    ) -> Task:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("O título da tarefa é obrigatório.")
        now = datetime.now()
        return self.storage.create(Task(
            id=None, title=clean_title, description=description.strip(), task_date=task_date,
            task_time=task_time, priority=priority, category=category, status=status,
            view_mode=view_mode,
            created_at=now, updated_at=now,
        ))

    def update(
        self, task_id: int, *, title: str, description: str, task_date: date,
        task_time: time | None, priority: Priority, category: Category, status: Status,
        view_mode: ViewMode | None = None,
    ) -> Task:
        task = self.storage.get(task_id)
        if not task:
            raise ValueError("Tarefa não encontrada.")
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("O título da tarefa é obrigatório.")
        task.title, task.description, task.task_date = clean_title, description.strip(), task_date
        task.task_time, task.priority, task.category, task.status = task_time, priority, category, status
        if view_mode is not None:
            task.view_mode = view_mode
        task.updated_at = datetime.now()
        return self.storage.update(task)

    def toggle(self, task_id: int) -> Task:
        task = self.storage.get(task_id)
        if not task:
            raise ValueError("Tarefa não encontrada.")
        task.status = Status.PENDING if task.completed else Status.COMPLETED
        task.updated_at = datetime.now()
        return self.storage.update(task)

    def delete(self, task_id: int) -> bool:
        return self.storage.delete(task_id)

    @staticmethod
    def progress(tasks: list[Task]) -> Progress:
        return Progress(total=len(tasks), completed=sum(task.completed for task in tasks))


def week_bounds(anchor: date) -> tuple[date, date]:
    start = anchor - timedelta(days=anchor.weekday())
    return start, start + timedelta(days=7)


def month_bounds(anchor: date) -> tuple[date, date]:
    start = anchor.replace(day=1)
    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)
    return start, end


def shift_month(anchor: date, offset: int) -> date:
    index = anchor.year * 12 + anchor.month - 1 + offset
    year, month_index = divmod(index, 12)
    return date(year, month_index + 1, min(anchor.day, 28))
