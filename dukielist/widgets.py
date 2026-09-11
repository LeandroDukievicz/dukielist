from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Iterable

from rich import box
from rich.table import Table
from rich.text import Text
from textual.message import Message
from textual.widgets import DataTable, Static

from .models import Category, Priority, Task, ViewMode
from .services import Progress


PRIORITY_COLORS = {
    Priority.LOW.value: "#39ff14",
    Priority.MEDIUM.value: "#ffe45e",
    Priority.HIGH.value: "#ff4444",
}

CATEGORY_COLORS = {
    Category.PERSONAL.value: "#39ff14",
    Category.WORK.value: "#ffe45e",
    Category.STUDY.value: "#38bdf8",
    Category.HEALTH.value: "#ff4fd8",
    Category.PLANNING.value: "#a855f7",
}

MODE_LABELS = {
    ViewMode.DAY.value: "DIA",
    ViewMode.WEEK.value: "SEMANA",
    ViewMode.MONTH.value: "MÊS",
}

def gradient_text(value: str, start: str = "#00efff", end: str = "#ff38d1") -> Text:
    """Renderiza o branding com uma variação de cor por caractere."""
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
    return Text("[✓]" if task.completed else "[ ]", style="#39ff14" if task.completed else "#b7c9e9")


def progress_render(progress: Progress, width: int = 34) -> Text:
    """Blocos de progresso com gradiente ciano-magenta e fundo azul-marinho."""
    filled = round(width * progress.completed / progress.total) if progress.total else 0
    result = Text()
    for index in range(width):
        if index < filled:
            ratio = index / max(width - 1, 1)
            result.append("█", style="#00e5ff" if ratio < 0.5 else "#ff2ec4")
        else:
            result.append("█", style="#152853")
    result.append(f"  {progress.completed}/{progress.total}  ({progress.percent}%)", style="#d7e4ff")
    return result


class TerminalChrome(Static):
    """Barra superior que simula a janela de terminal das referências."""

    def render(self) -> Text:
        traffic = "●  ●  ●"
        host = "usuario@meu-pc: ~"
        padding = max(3, ((self.size.width or 100) - len(traffic) - len(host)) // 2)
        text = Text()
        text.append("●", style="#ff4b5c")
        text.append("  ●", style="#ffd447")
        text.append("  ●", style="#35e56b")
        text.append(" " * padding, style="")
        text.append(host, style="#d4e0ff")
        return text


class BrandHeader(Static):
    def render(self) -> Text:
        result = Text()
        result.append("[", style="#00e5ff bold")
        result.append("✓", style="#00e5ff bold")
        result.append("] ", style="#ff38d1 bold")
        result.append_text(gradient_text("DukieList"))
        return result


class AnalogClock(Static):
    """Relógio analógico que redesenha os três ponteiros a cada segundo."""

    _WIDTH = 15
    _HEIGHT = 7
    _CENTER = (7, 3)
    _HOUR_COLOR = "#ff38d1"
    _MINUTE_COLOR = "#00e5ff"
    _SECOND_COLOR = "#ff4fd8"

    def on_mount(self) -> None:
        self.set_interval(1, self._tick)

    def _tick(self) -> None:
        self.refresh()

    @classmethod
    def _hand_endpoint(
        cls, angle: float, radius_x: float, radius_y: float, center: tuple[int, int] | None = None
    ) -> tuple[int, int]:
        from math import cos, radians, sin

        center = center or cls._CENTER
        return (
            round(center[0] + sin(radians(angle)) * radius_x),
            round(center[1] - cos(radians(angle)) * radius_y),
        )

    @classmethod
    def _draw_hand(
        cls,
        grid: list[list[tuple[str, str]]],
        endpoint: tuple[int, int],
        color: str,
        center: tuple[int, int] | None = None,
    ) -> None:
        x0, y0 = center or cls._CENTER
        x1, y1 = endpoint
        steps = max(abs(x1 - x0), abs(y1 - y0)) * 3 or 1
        previous = (x0, y0)
        for step in range(1, steps + 1):
            x = round(x0 + (x1 - x0) * step / steps)
            y = round(y0 + (y1 - y0) * step / steps)
            if (x, y) == previous:
                continue
            if x == previous[0]:
                glyph = "│"
            elif y == previous[1]:
                glyph = "─"
            elif (x - previous[0]) * (y - previous[1]) < 0:
                glyph = "╱"
            else:
                glyph = "╲"
            if 0 <= y < len(grid) and 0 <= x < len(grid[0]):
                grid[y][x] = (glyph, color)
            previous = (x, y)

    @staticmethod
    def _weekday_label(now: datetime) -> str:
        return ("SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM")[now.weekday()]

    def render(self) -> Text:
        now = datetime.now()
        # ``size.height`` excludes bordas; o cabeçalho grande possui nove linhas úteis.
        if (self.size.height or 9) < 8:
            return self._render_compact(now)

        grid = [[("·", "#24558d") for _ in range(self._WIDTH)] for _ in range(self._HEIGHT)]

        # Marcações cardeais e intermediárias do mostrador.
        marks = {
            (7, 0): ("12", "#dbe8ff"),
            (10, 1): ("1", "#7891b8"),
            (12, 3): ("3", "#dbe8ff"),
            (10, 5): ("5", "#7891b8"),
            (7, 6): ("6", "#dbe8ff"),
            (4, 5): ("7", "#7891b8"),
            (1, 3): ("9", "#dbe8ff"),
            (4, 1): ("11", "#7891b8"),
        }
        for (x, y), value in marks.items():
            glyph, color = value
            for offset, char in enumerate(glyph):
                if 0 <= x + offset < self._WIDTH:
                    grid[y][x + offset] = (char, color)

        # Horário contínuo: hora inclui a fração dos minutos.
        hour_angle = (now.hour % 12 + now.minute / 60) * 30
        minute_angle = (now.minute + now.second / 60) * 6
        second_angle = now.second * 6
        self._draw_hand(grid, self._hand_endpoint(hour_angle, 3.0, 1.7), self._HOUR_COLOR)
        self._draw_hand(grid, self._hand_endpoint(minute_angle, 4.5, 2.5), self._MINUTE_COLOR)
        self._draw_hand(grid, self._hand_endpoint(second_angle, 5.2, 2.8), self._SECOND_COLOR)
        grid[self._CENTER[1]][self._CENTER[0]] = ("●", "#ffffff")

        text = Text()
        for row in grid:
            for glyph, color in row:
                text.append(glyph, style=color)
            text.append("\n")
        text.append(f"{self._weekday_label(now)}  ·  {now:%H:%M:%S}", style="#dbe8ff bold")
        text.append("\nH", style=self._HOUR_COLOR + " bold")
        text.append("  M", style=self._MINUTE_COLOR + " bold")
        text.append("  S", style=self._SECOND_COLOR + " bold")
        return text

    def _render_compact(self, now: datetime) -> Text:
        """Versão reduzida para terminais baixos, mantendo dia da semana e os três ponteiros."""
        width, height = 15, 5
        center = (7, 2)
        grid = [[("·", "#24558d") for _ in range(width)] for _ in range(height)]
        marks = {
            (7, 0): ("12", "#dbe8ff"),
            (1, 2): ("9", "#dbe8ff"),
            (12, 2): ("3", "#dbe8ff"),
            (7, 4): ("6", "#dbe8ff"),
        }
        for (x, y), (glyph, color) in marks.items():
            for offset, char in enumerate(glyph):
                if 0 <= x + offset < width:
                    grid[y][x + offset] = (char, color)

        hour_angle = (now.hour % 12 + now.minute / 60) * 30
        minute_angle = (now.minute + now.second / 60) * 6
        second_angle = now.second * 6
        self._draw_hand(grid, self._hand_endpoint(hour_angle, 2.5, 1.1, center), self._HOUR_COLOR, center)
        self._draw_hand(grid, self._hand_endpoint(minute_angle, 4.0, 1.7, center), self._MINUTE_COLOR, center)
        self._draw_hand(grid, self._hand_endpoint(second_angle, 5.0, 1.9, center), self._SECOND_COLOR, center)
        grid[center[1]][center[0]] = ("●", "#ffffff")

        text = Text()
        for row in grid:
            for glyph, color in row:
                text.append(glyph, style=color)
            text.append("\n")
        text.append(f"{self._weekday_label(now)}  ·  {now:%H:%M:%S}", style="#dbe8ff bold")
        return text


class CommandPrompt(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_visible = True

    def on_mount(self) -> None:
        self.set_interval(0.65, self._blink)

    def _blink(self) -> None:
        self.cursor_visible = not self.cursor_visible
        self.refresh()

    def render(self) -> Text:
        text = Text()
        text.append("Escolha um comando:", style="#00e5ff bold")
        text.append("  ▌" if self.cursor_visible else "  ", style="#d7e4ff")
        return text


class ModeCard(Static):
    class Selected(Message):
        def __init__(self, mode: str) -> None:
            self.mode = mode
            super().__init__()

    can_focus = True

    def __init__(self, mode: str, label: str, description: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.mode = mode
        self.label = label
        self.description = description

    def render(self) -> Text:
        text = Text()
        text.append(f"[ {self.label} ]\n", style="#eaf3ff bold")
        text.append(self.description, style="#9bb2d6")
        return text

    def on_key(self, event) -> None:
        if event.key == "enter":
            self.post_message(self.Selected(self.mode))
            event.stop()


class ProgressPanel(Static):
    def update_progress(self, label: str, progress: Progress) -> None:
        self.label = label
        self.progress = progress
        self.update(self._render_progress())

    def _render_progress(self) -> Text:
        width = max(18, min(44, (self.size.width or 100) - 42))
        line = Text()
        line.append("▸ ", style="#00e5ff bold")
        line.append(self.label, style="#9fdcff bold")
        line.append("\n")
        line.append_text(progress_render(self.progress, width))
        return line

    def render(self) -> Text:
        if not hasattr(self, "progress"):
            return Text("▸ PROGRESSO", style="#9fdcff bold")
        return self._render_progress()


class SidebarPanel(Static):
    """Painel lateral único, com conteúdo contextual por modo."""

    def update_for_mode(self, mode: str, tasks: list[Task], filters_active: bool = False) -> None:
        self.mode = mode
        self.tasks = tasks
        self.filters_active = filters_active
        self.update(self._render_sidebar())

    @staticmethod
    def _line(text: Text, label: str, style: str = "#ff38d1") -> None:
        text.append("▸ ", style=style + " bold")
        text.append(label, style="#ff65d9 bold")
        text.append("  ───────────", style="#235c8f")
        text.append("\n")

    @staticmethod
    def _commands(mode: str) -> list[tuple[str, str]]:
        if mode == ViewMode.DAY.value:
            return [
                ("a", "Adicionar tarefa"), ("c", "Concluir tarefa"),
                ("d", "Deletar tarefa"), ("e", "Editar tarefa"),
                ("v", "Ver todas as tarefas"), ("f", "Filtrar por categoria"),
                ("p", "Filtrar por prioridade"), ("l", "Limpar concluídas"),
                ("h", "Mostrar ajuda"), ("q", "Sair"),
            ]
        if mode == ViewMode.WEEK.value:
            return [
                ("a", "Adicionar tarefa"), ("e", "Editar tarefa"),
                ("d", "Deletar tarefa"), ("v", "Ver tarefa"),
                ("s", "Marcar como concluída"), ("u", "Desmarcar tarefa"),
                ("n", "Ir para próxima semana"), ("b", "Ir para semana anterior"),
                ("h", "Mostrar ajuda"), ("q", "Sair"),
            ]
        return [
            ("a", "Adicionar tarefa (no dia)"), ("e", "Editar tarefa (do dia)"),
            ("v", "Ver tarefas do dia"), ("n", "Mês anterior"),
            ("m", "Próximo mês"), ("h", "Mostrar ajuda"), ("q", "Sair"),
        ]

    def _render_sidebar(self) -> Text:
        text = Text()
        if self.mode == ViewMode.MONTH.value:
            self._line(text, "RESUMO DO MÊS")
            total = len(self.tasks)
            completed = sum(task.completed for task in self.tasks)
            pending = total - completed
            completed_percent = round(completed / total * 100) if total else 0
            pending_percent = round(pending / total * 100) if total else 0
            text.append(f"Total de tarefas  {total}\n", style="#d5e2fb")
            text.append(f"Concluídas         {completed}  ({completed_percent}%)\n", style="#39ff14")
            text.append(f"Pendentes          {pending}  ({pending_percent}%)\n", style="#ffe45e")
            text.append("\nPor prioridade:\n", style="#a9bee4 bold")
            priorities = {priority.value: 0 for priority in Priority}
            for task in self.tasks:
                priorities[task.priority.value] += 1
            for priority in Priority:
                text.append("● ", style=PRIORITY_COLORS[priority.value])
                text.append(f"{priority.value.capitalize():<9}", style="#c8d5ee")
                text.append(f"{priorities[priority.value]}\n", style="#f2f5ff")
            text.append("──────────────\n", style="#24558d")
            text.append("Por categoria:\n", style="#a9bee4 bold")
            categories = {category.value: 0 for category in Category}
            for task in self.tasks:
                categories[task.category.value] += 1
            for category in Category:
                text.append("● ", style=CATEGORY_COLORS[category.value])
                text.append(f"{category.value.capitalize():<13}", style="#c8d5ee")
                text.append(f"{categories[category.value]}\n", style="#f2f5ff")
            text.append("\n")
            self._line(text, "COMANDOS (modo mês)", style="#00e5ff")
        else:
            self._line(text, "COMANDOS")
            text.append(f"modo {MODE_LABELS.get(self.mode, self.mode).lower()}\n\n", style="#809bc4")

        for key, label in self._commands(self.mode):
            text.append(f"{key:<2}", style="#00e5ff bold")
            text.append(f" {label}\n", style="#d4e1f8")

        if self.filters_active:
            text.append("\n● filtros ativos\n", style="#ff38d1 bold")

        if self.mode != ViewMode.MONTH.value:
            text.append("\n")
            self._line(text, "CATEGORIAS")
            for category in Category:
                text.append("● ", style=CATEGORY_COLORS[category.value])
                text.append(f"{category.value.capitalize()}\n", style="#d4e1f8")
        return text


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
        table = Table(expand=True, show_edge=True, box=box.SQUARE, padding=(0, 1))
        for heading in ("Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"):
            table.add_column(heading, justify="left", style="#b8cbea", ratio=1)
        weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(self.year, self.month)
        for week in weeks:
            cells: list[Text] = []
            for current in week:
                in_month = current.month == self.month
                tasks = self.tasks_by_day.get(current, [])
                cell = Text(f"{current.day:02d}\n", style="#e8efff bold" if in_month else "#3e557a")
                if current == date.today():
                    cell.append("hoje\n", style="#00e5ff bold")
                else:
                    cell.append("\n")
                for task in tasks[:3]:
                    cell.append("●", style=CATEGORY_COLORS[task.category.value])
                if current == self.selected_date:
                    cell.stylize("on #123a65")
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
            event.stop()
            return
        if step is not None:
            self.selected_date += timedelta(days=step)
            self.year, self.month = self.selected_date.year, self.selected_date.month
            self.update(self._render_calendar())
            self.post_message(self.DaySelected(self.selected_date))
            event.stop()


class WeekBoard(Static):
    class TaskCursorChanged(Message):
        def __init__(self, task_id: int | None) -> None:
            self.task_id = task_id
            super().__init__()

    can_focus = True

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.start = date.today() - timedelta(days=date.today().weekday())
        self.tasks_by_day: dict[date, list[Task]] = {}
        self.day_index = date.today().weekday()
        self.task_index = 0

    def set_week(self, start: date, tasks: list[Task]) -> None:
        self.start = start
        self.tasks_by_day = {}
        for task in tasks:
            self.tasks_by_day.setdefault(task.task_date, []).append(task)
        self.day_index = min(max(self.day_index, 0), 6)
        current_tasks = self._current_day_tasks()
        self.task_index = min(self.task_index, max(len(current_tasks) - 1, 0))
        self.update(self._render_board())

    def _current_day_tasks(self) -> list[Task]:
        return self.tasks_by_day.get(self.start + timedelta(days=self.day_index), [])

    def _current_task_id(self) -> int | None:
        tasks = self._current_day_tasks()
        return tasks[self.task_index].id if tasks and self.task_index < len(tasks) else None

    @staticmethod
    def _task_line(cell: Text, task: Task, selected: bool) -> None:
        start = len(cell)
        marker = "[✓]" if task.completed else "[ ]"
        title_style = "#7b8dad strike" if task.completed else "#e5edff"
        cell.append(f"{marker} {task.title[:19]}\n", style=title_style)
        cell.append(f"{task.priority.value.capitalize()}", style=PRIORITY_COLORS[task.priority.value])
        cell.append(" | ", style="#7390b9")
        cell.append(f"{task.category.value.capitalize()}\n", style=CATEGORY_COLORS[task.category.value])
        if selected:
            cell.stylize("on #124b74", start, len(cell))

    def _render_board(self) -> Table:
        table = Table(expand=True, show_edge=True, box=box.ROUNDED, padding=(0, 1))
        day_names = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
        for index, name in enumerate(day_names):
            style = "#ff38d1 bold" if index == 6 else "#00e5ff bold"
            table.add_column(name, style=style, ratio=1)

        cells: list[Text] = []
        for index, name in enumerate(day_names):
            current = self.start + timedelta(days=index)
            day_tasks = self.tasks_by_day.get(current, [])
            header_style = "#ff38d1 bold" if index == 6 else "#00e5ff bold"
            cell = Text(f"{name}  {current:%d/%m}\n", style=header_style)
            cell.append("─" * 13 + "\n", style="#245a91")
            if current == date.today():
                cell.append("HOJE\n", style="#00e5ff bold")
            else:
                cell.append("\n")
            if not day_tasks:
                cell.append("—\n", style="#5f789d")
            for task_position, task in enumerate(day_tasks[:5]):
                self._task_line(cell, task, index == self.day_index and task_position == self.task_index)
            if len(day_tasks) > 5:
                cell.append(f"+ {len(day_tasks) - 5} tarefas\n", style="#91a7cc")
            cell.append("\n" + "─" * 13 + "\n", style="#ff38d1")
            cell.append("+ Adicionar tarefa", style="#ff65d9")
            if index == self.day_index:
                cell.stylize("on #0b2b4c")
            cells.append(cell)
        table.add_row(*cells)
        return table

    def on_key(self, event) -> None:
        if event.key in {"left", "right"}:
            self.day_index = (self.day_index + (1 if event.key == "right" else -1)) % 7
            self.task_index = 0
            self.update(self._render_board())
            self.post_message(self.TaskCursorChanged(self._current_task_id()))
            event.stop()
        elif event.key in {"up", "down"}:
            tasks = self._current_day_tasks()
            if tasks:
                delta = 1 if event.key == "down" else -1
                self.task_index = (self.task_index + delta) % len(tasks)
                self.update(self._render_board())
                self.post_message(self.TaskCursorChanged(self._current_task_id()))
            event.stop()
        elif event.key == "enter":
            self.post_message(self.TaskCursorChanged(self._current_task_id()))
            event.stop()


class TaskTable(DataTable):
    can_focus = True

    def configure_table(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns("#", "STATUS", "TAREFA")

    def fill(self, tasks: list[Task]) -> None:
        self.clear(columns=False)
        for index, task in enumerate(tasks, start=1):
            self.add_row(
                Text(str(index), style="#7390b9"),
                status_render(task),
                Text(task.title, style="#7b8dad strike" if task.completed else "#e5edff"),
                key=str(task.id),
            )
