from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum
from typing import Any


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
    task_date: date
    task_time: time | None
    priority: Priority
    category: Category
    status: Status
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def completed(self) -> bool:
        return self.status is Status.COMPLETED

    @property
    def time_label(self) -> str:
        return self.task_time.strftime("%H:%M") if self.task_time else "—"

    @classmethod
    def from_row(cls, row: Any) -> "Task":
        return cls(
            id=int(row["id"]),
            title=str(row["title"]),
            description=str(row["description"] or ""),
            task_date=date.fromisoformat(row["task_date"]),
            task_time=time.fromisoformat(row["task_time"]) if row["task_time"] else None,
            priority=Priority(row["priority"]),
            category=Category(row["category"]),
            status=Status(row["status"]),
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
