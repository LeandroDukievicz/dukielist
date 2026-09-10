from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Iterable

from rich.table import Table
from rich.text import Text
from textual.message import Message
from textual.widgets import DataTable, Static

from .models import Category, Priority, Task
from .services import Progress


PRIORITY_COLORS = {
    Priority.LOW.value: "#22f58a",
    Priority.MEDIUM.value: "#ffd426",
    Priority.HIGH.value: "#ff416c",
}

CATEGORY_COLORS = {
    Category.PERSONAL.value: "#19b5fe",
    Category.WORK.value: "#ffd426",
    Category.STUDY.value: "#8b7bff",
    Category.HEALTH.value: "#ef5bcb",
    Category.PLANNING.value: "#b56cff",
}


def gradient_text(value: str, start: str = "#00efff", end: str = "#ff38d1") -> Text:
    """Texto com variação de cor por caractere para o gradiente do branding."""
    text = Text(no_wrap=True)
    if not value:
        return text
    start_rgb = tuple(int(start[i : i + 2], 16) for i in (1, 3, 5))
    end_rgb = tuple(int(end[i : i + 2], 16) for i in (1, 3, 5))
    last = max(len(value) - 1, 1)
    for index, char in enumerate(value):
        ratio = index / last
        rgb = tuple(round(start_rgb[pos] + (end_rgb[pos] - start_rgb[pos]) * ratio) for pos in range(3))
        text.append(char, style=f"bold rgb({rgb[0]},{rgb[1]},{rgb[2]})")
    return text


def status_render(task: Task) -> Text:
    return Text("[✓]" if task.completed else "[ ]", style="#22f58a" if task.completed else "#8fa5c9")


def priority_render(priority: Priority) -> Text:
    return Text(priority.value.capitalize(), style=PRIORITY_COLORS[priority.value])


def category_render(category: Category) -> Text:
    return Text(category.value.capitalize(), style=CATEGORY_COLORS[category.value])


def task_render(task: Task) -> list[Text]:
    title_style = "#7891b8 strike" if task.completed else "#dbe8ff"
    return [
        status_render(task),
        Text(task.title, style=title_style),
        priority_render(task.priority),
        category_render(task.category),
        Text(task.time_label, style="#b8c8e7"),
    ]


def progress_render(progress: Progress, width: int = 30) -> Text:
    filled = round(width * progress.completed / progress.total) if progress.total else 0
    result = Text()
    for index in range(width):
        if index < filled:
            ratio = index / max(width - 1, 1)
            result.append("▰", style="#00e5ff" if ratio < 0.55 else "#d638e8")
        else:
            result.append("▱", style="#1a2c56")
    result.append(f"  {progress.completed}/{progress.total}  ({progress.percent}%)", style="#c9d5f0")
    return result


class BrandHeader(Static):
    def render(self) -> Text:
        result = Text()
        result.append("[", style="#00e5ff bold")
        result.append("✓", style="#00e5ff bold")
        result.append("] ", style="#ff38d1 bold")
        result.append_text(gradient_text("DukieList"))
        result.append("\n  TERMINAL TASK CONTROL", style="#91a7cc")
        return result


class ProgressPanel(Static):
    def update_progress(self, label: str, progress: Progress) -> None:
        line = Text()
        line.append("▸ ", style="#00e5ff bold")
        line.append(label.upper(), style="#b9c8ef bold")
        line.append("\n")
        line.append_text(progress_render(progress, width=34))
        self.update(line)


class SummaryPanel(Static):
    def update_summary(self, progress: Progress, period_label: str, category_counts: dict[str, int]) -> None:
        text = Text()
        text.append("▸ ", style="#ff38d1 bold")
        text.append("RESUMO", style="#ff8be9 bold")
        text.append(f"\n{period_label}\n\n", style="#8fa5c9")
        text.append("Total        ", style="#b8c8e7")
        text.append(f"{progress.total}\n", style="#f2f5ff bold")
        text.append("Concluídas   ", style="#b8c8e7")
        text.append(f"{progress.completed}  ({progress.percent}%)\n", style="#22f58a")
        text.append("Pendentes    ", style="#b8c8e7")
        text.append(f"{progress.total - progress.completed}\n", style="#ffcf4c")
        text.append("\nPOR CATEGORIA\n", style="#7d92ba bold")
        for category, count in category_counts.items():
            text.append("● ", style=CATEGORY_COLORS.get(category, "#b8c8e7"))
            text.append(f"{category.capitalize():<13}", style="#b8c8e7")
            text.append(f"{count}\n", style="#f2f5ff")
        self.update(text)


class ShortcutsPanel(Static):
    def update_mode(self, mode: str, filters_active: bool = False) -> None:
        text = Text()
        text.append("▸ ", style="#00e5ff bold")
        text.append("COMANDOS", style="#ff8be9 bold")
        text.append(f"  [{mode.upper()}]\n\n", style="#a4b4d3")
        rows = [
            ("a", "Adicionar tarefa"),
            ("e", "Editar selecionada"),
            ("c", "Concluir / reabrir"),
            ("x", "Excluir selecionada"),
            ("f", "Filtrar tarefas"),
            ("n", "Próximo período"),
            ("b", "Período anterior"),
            ("h", "Ajuda de atalhos"),
            ("q", "Sair"),
        ]
        for key, label in rows:
            text.append(f" {key} ", style="#00e5ff bold")
            text.append(f" {label}\n", style="#c8d5ee")
        if filters_active:
            text.append("\n● filtros ativos", style="#ff38d1 bold")
        self.update(text)


class CalendarGrid(Static):
    class DaySelected(Message):
        def __init__(self, day: date) -> None:
            self.day = day
            super().__init__()

    can_focus = True

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.selected_date = date.today()
        self.year = self.selected_date.year
        self.month = self.selected_date.month
        self.tasks_by_day: dict[date, list[Task]] = {}

    def set_calendar(self, year: int, month: int, tasks: Iterable[Task], selected: date) -> None:
        self.year = year
        self.month = month
        self.selected_date = selected
        self.tasks_by_day = {}
        for task in tasks:
            self.tasks_by_day.setdefault(task.task_date, []).append(task)
        self.update(self._render_calendar())

    def _render_calendar(self) -> Table:
        table = Table(expand=True, show_edge=False, box=None, padding=(0, 1))
        for heading in ("DOM", "SEG", "TER", "QUA", "QUI", "SEX", "SÁB"):
            table.add_column(heading, justify="left", style="#a9bee4", ratio=1)
        weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(self.year, self.month)
        for week in weeks:
            cells: list[Text] = []
            for day_number in week:
                if not day_number:
                    cells.append(Text(""))
                    continue
                current = date(self.year, self.month, day_number)
                tasks = self.tasks_by_day.get(current, [])
                cell = Text(f"{day_number:02d}\n", style="#e5edff bold")
                cell.append(" hoje" if current == date.today() else "     ", style="#00e5ff" if current == date.today() else "")
                for task in tasks[:4]:
                    cell.append("●", style=CATEGORY_COLORS[task.category.value])
                if len(tasks) > 4:
                    cell.append(f" +{len(tasks)-4}", style="#91a7cc")
                if current == self.selected_date:
                    cell.stylize("on #12345b")
                    cell.stylize("#ffffff bold")
                cells.append(cell)
            table.add_row(*cells)
        return table

    def on_key(self, event) -> None:
        step: int | None = None
        if event.key == "left":
            step = -1
        elif event.key == "right":
            step = 1
        elif event.key == "up":
            step = -7
        elif event.key == "down":
            step = 7
        elif event.key == "enter":
            self.post_message(self.DaySelected(self.selected_date))
        if step is not None:
            self.selected_date += timedelta(days=step)
            self.year, self.month = self.selected_date.year, self.selected_date.month
            self.update(self._render_calendar())
            self.post_message(self.DaySelected(self.selected_date))
            event.stop()


class TaskTable(DataTable):
    can_focus = True

    def configure_table(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns("#", "STATUS", "TAREFA", "PRIORIDADE", "CATEGORIA", "HORÁRIO")

    def fill(self, tasks: list[Task]) -> None:
        self.clear(columns=False)
        for index, task in enumerate(tasks, start=1):
            self.add_row(
                Text(str(index), style="#738bb7"),
                status_render(task),
                Text(task.title, style="#7891b8 strike" if task.completed else "#dbe8ff"),
                priority_render(task.priority),
                category_render(task.category),
                Text(task.time_label, style="#b8c8e7"),
                key=str(task.id),
            )
