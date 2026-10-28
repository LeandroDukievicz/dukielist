from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Iterator


class Priority(str, Enum):
    LOW = "baixa"
    MEDIUM = "média"
    HIGH = "alta"


class Category(str, Enum):
    PERSONAL = "pessoal"
    WORK = "trabalho"
    STUDY = "estudo"
    HEALTH = "saúde"
    PLANNING = "planejamento"


class Status(str, Enum):
    PENDING = "pendente"
    COMPLETED = "concluída"


class ViewMode(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@dataclass(slots=True)
class Task:
    id: int | None
    title: str
    description: str
    start_date: date
    end_date: date
    start_time: time | None
    end_time: time | None
    all_day: bool
    priority: Priority
    category: Category
    status: Status
    view_mode: ViewMode = ViewMode.DAY
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def completed(self) -> bool:
        return self.status is Status.COMPLETED

    def occurs_on(self, day: date) -> bool:
        if not self.all_day and self.end_date > self.start_date and self.end_time == time.min:
            return self.start_date <= day < self.end_date
        return self.start_date <= day <= self.end_date

    @property
    def schedule_label(self) -> str:
        if self.all_day:
            if self.start_date == self.end_date:
                return "Dia inteiro"
            return f"Dia inteiro · {self.start_date:%d/%m}–{self.end_date:%d/%m}"
        start = f"{self.start_time:%H:%M}"
        end = f"{self.end_time:%H:%M}"
        if self.start_date == self.end_date:
            return f"{start}–{end}"
        return f"{self.start_date:%d/%m} {start}–{self.end_date:%d/%m} {end}"

    def days_between(self, start: date, end: date) -> Iterator[date]:
        """Datas da tarefa no intervalo semiaberto [start, end)."""
        current = max(self.start_date, start)
        task_end = self.end_date
        if not self.all_day and task_end > self.start_date and self.end_time == time.min:
            task_end -= timedelta(days=1)
        last = min(task_end, end - timedelta(days=1))
        while current <= last:
            yield current
            current += timedelta(days=1)

    @classmethod
    def from_row(cls, row: Any) -> "Task":
        view_mode = ViewMode.DAY
        if "view_mode" in row.keys() and row["view_mode"]:
            try:
                view_mode = ViewMode(row["view_mode"])
            except ValueError:
                view_mode = ViewMode.DAY
        return cls(
            id=int(row["id"]),
            title=str(row["title"]),
            description=str(row["description"] or ""),
            start_date=date.fromisoformat(row["start_date"]),
            end_date=date.fromisoformat(row["end_date"]),
            start_time=time.fromisoformat(row["start_time"]) if row["start_time"] else None,
            end_time=time.fromisoformat(row["end_time"]) if row["end_time"] else None,
            all_day=bool(row["all_day"]),
            priority=Priority(row["priority"]),
            category=Category(row["category"]),
            status=Status(row["status"]),
            view_mode=view_mode,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


def parse_date(value: str) -> date:
    """Aceita DD/MM/AAAA (interface) e AAAA-MM-DD (SQLite/API)."""
    clean = value.strip()
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(clean, pattern).date()
        except ValueError:
            continue
    raise ValueError("Data inválida. Use DD/MM/AAAA.")


def parse_time(value: str) -> time | None:
    clean = value.strip()
    if not clean or clean in {"--:--", "—"}:
        return None
    try:
        return datetime.strptime(clean, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("Horário inválido. Use HH:MM.") from exc


def format_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")
