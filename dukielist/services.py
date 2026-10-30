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


@dataclass(frozen=True, slots=True)
class StatusBreakdown:
    """Contagem por estado exibido, separando atraso de pendência em dia."""

    total: int
    completed: int
    pending: int
    overdue: int
    missed: int

    def percent(self, amount: int) -> int:
        return round(amount / self.total * 100) if self.total else 0


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
        self, *, title: str, description: str, start_date: date, end_date: date,
        start_time: time | None, end_time: time | None, all_day: bool,
        priority: Priority, category: Category, status: Status = Status.PENDING,
        view_mode: ViewMode = ViewMode.DAY,
    ) -> Task:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("O título da tarefa é obrigatório.")
        self._validate_schedule(start_date, end_date, start_time, end_time, all_day)
        if all_day:
            start_time = end_time = None
        now = datetime.now()
        return self.storage.create(Task(
            id=None, title=clean_title, description=description.strip(),
            start_date=start_date, end_date=end_date, start_time=start_time,
            end_time=end_time, all_day=all_day, priority=priority, category=category,
            status=status,
            view_mode=view_mode,
            created_at=now, updated_at=now,
        ))

    def update(
        self, task_id: int, *, title: str, description: str, start_date: date,
        end_date: date, start_time: time | None, end_time: time | None, all_day: bool,
        priority: Priority, category: Category, status: Status,
        view_mode: ViewMode | None = None,
    ) -> Task:
        task = self.storage.get(task_id)
        if not task:
            raise ValueError("Tarefa não encontrada.")
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("O título da tarefa é obrigatório.")
        self._validate_schedule(start_date, end_date, start_time, end_time, all_day)
        if all_day:
            start_time = end_time = None
        task.title, task.description = clean_title, description.strip()
        task.start_date, task.end_date = start_date, end_date
        task.start_time, task.end_time, task.all_day = start_time, end_time, all_day
        task.priority, task.category, task.status = priority, category, status
        if view_mode is not None:
            task.view_mode = view_mode
        task.updated_at = datetime.now()
        return self.storage.update(task)

    @staticmethod
    def _validate_schedule(
        start_date: date,
        end_date: date,
        start_time: time | None,
        end_time: time | None,
        all_day: bool,
    ) -> None:
        if end_date < start_date:
            raise ValueError("A data de término deve ser igual ou posterior à data de início.")
        if all_day:
            return
        if start_time is None or end_time is None:
            raise ValueError("Informe os horários de início e término ou marque o dia inteiro.")
        if end_date == start_date and end_time <= start_time:
            raise ValueError("O término deve acontecer depois do início.")

    def toggle(self, task_id: int) -> Task:
        task = self._require(task_id)
        return self._save_status(task, Status.PENDING if task.completed else Status.COMPLETED)

    def set_status(self, task_id: int, status: Status) -> Task:
        return self._save_status(self._require(task_id), status)

    def _require(self, task_id: int) -> Task:
        task = self.storage.get(task_id)
        if not task:
            raise ValueError("Tarefa não encontrada.")
        return task

    def _save_status(self, task: Task, status: Status) -> Task:
        task.status = status
        task.updated_at = datetime.now()
        return self.storage.update(task)

    def delete(self, task_id: int) -> bool:
        return self.storage.delete(task_id)

    @staticmethod
    def progress(tasks: list[Task]) -> Progress:
        return Progress(total=len(tasks), completed=sum(task.completed for task in tasks))

    @staticmethod
    def breakdown(tasks: list[Task], today: date | None = None) -> StatusBreakdown:
        counts = {"completed": 0, "pending": 0, "overdue": 0, "missed": 0}
        for task in tasks:
            counts[task.display_state(today)] += 1
        return StatusBreakdown(total=len(tasks), **counts)


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
