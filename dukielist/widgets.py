from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Iterable

from rich import box
from rich.table import Table
from rich.panel import Panel
from rich.console import Group
from textual.renderables.digits import Digits
from rich.align import Align
from rich.text import Text
from textual.message import Message
from textual.widgets import DataTable, Static

from .models import Category, Priority, Task, ViewMode
from .services import Progress, TaskService


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

# Marcador, cor e estilo do título por estado exibido. "overdue" é derivado da
# data: assim que o último dia da tarefa passa sem conclusão, o [ ] vira um [!]
# amarelo até o usuário decidir se concluiu ou não.
STATUS_MARKERS: dict[str, tuple[str, str, str]] = {
    "completed": ("[✓]", "#39ff14", "#7b8dad strike"),
    "overdue": ("[!]", "#ffc400", "#ffe9a8"),
    "missed": ("[✗]", "#ff416c", "#ff9aae strike"),
    "pending": ("[ ]", "#b7c9e9", "#e5edff"),
}

STATUS_LEGEND = (
    ("completed", "Concluída"),
    ("pending", "Pendente"),
    ("overdue", "Atrasada (dia passou)"),
    ("missed", "Não concluída"),
)


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
    marker, color, _ = STATUS_MARKERS[task.display_state()]
    return Text(marker, style=color)


def progress_render(progress: Progress, width: int = 34) -> Text:
    """Blocos de progresso com gradiente ciano-magenta e fundo azul-marinho."""
    filled = round(width * progress.completed / progress.total) if progress.total else 0
    result = Text()
    for index in range(width):
        if index < filled:
            ratio = index / max(filled - 1, 1)
            red, green, blue = round(255 * ratio), round(229 - 183 * ratio), round(255 - 59 * ratio)
            result.append("▉ ", style=f"rgb({red},{green},{blue})")
        else:
            result.append("▉ ", style="#152853")
    result.append(f"  {progress.completed}/{progress.total}  ({progress.percent}%)", style="#d7e4ff")
    return result


class AppTitle(Static):
    """Nome compacto do produto exibido na faixa superior."""

    def render(self) -> Text:
        text = gradient_text("DukieList")
        text.justify = "center"
        return text


class DigitalClock(Static):
    """Horário local em dígitos grandes, com atualização a cada segundo."""

    WEEKDAYS = ("Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira",
                "Sexta-feira", "Sábado", "Domingo")

    def on_mount(self) -> None:
        self.set_interval(1, self.refresh)

    def render_time(self, now: datetime, *, compact: bool = False):
        weekday = self.WEEKDAYS[now.weekday()]
        caption = Text(f"{weekday} · {now:%d/%m/%Y}", style="#b8d7f7", justify="center")
        if compact:
            return Group(Text(f"{now:%H:%M:%S}", style="bold #00e5ff", justify="center"), caption)
        return Group(Align.center(Digits(f"{now:%H:%M:%S}", style="bold #00e5ff")), caption)

    def render(self):
        return self.render_time(datetime.now(), compact=0 < self.size.height < 4)


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
        width = max(6, min(44, ((self.size.width or 100) - 24) // 2))
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
                ("←→", "Dia anterior / próximo"), ("↑↓", "Escolher tarefa"),
                ("a", "Adicionar tarefa"), ("c", "Concluir tarefa"),
                ("k", "Marcar não concluída"), ("u", "Reabrir tarefa"),
                ("x", "Deletar tarefa"), ("e", "Editar tarefa"),
                ("v", "Ver todas as tarefas"), ("f", "Filtrar por categoria"),
                ("p", "Filtrar por prioridade"), ("l", "Limpar concluídas"),
                ("h", "Mostrar ajuda"), ("q", "Sair"),
            ]
        if mode == ViewMode.WEEK.value:
            return [
                ("←→", "Dia, trocando de semana"), ("↑↓", "Tarefas do dia"),
                ("a", "Adicionar tarefa"), ("e", "Editar tarefa"),
                ("x", "Deletar tarefa"), ("v", "Ver tarefa"),
                ("s", "Marcar como concluída"), ("u", "Desmarcar tarefa"),
                ("k", "Marcar não concluída"),
                ("n", "Ir para próxima semana"), ("b", "Ir para semana anterior"),
                ("h", "Mostrar ajuda"), ("q", "Sair"),
            ]
        return [
            ("←→", "Dia, trocando de mês"), ("↑↓", "Semana anterior / próxima"),
            ("a", "Adicionar tarefa (no dia)"), ("e", "Editar tarefa (do dia)"),
            ("k", "Marcar não concluída"), ("v", "Ver tarefas do dia"),
            ("b", "Mês anterior"),
            ("n", "Próximo mês"), ("h", "Mostrar ajuda"), ("q", "Sair"),
        ]

    def _render_sidebar(self) -> Text:
        text = Text()
        if self.mode == ViewMode.MONTH.value:
            self._line(text, "RESUMO DO MÊS")
            summary = TaskService.breakdown(self.tasks)
            text.append(f"Total de tarefas  {summary.total}\n", style="#d5e2fb")
            for state, label, amount in (
                ("completed", "Concluídas", summary.completed),
                ("pending", "Pendentes", summary.pending),
                ("overdue", "Atrasadas", summary.overdue),
                ("missed", "Não concluídas", summary.missed),
            ):
                text.append(
                    f"{label:<17} {amount}  ({summary.percent(amount)}%)\n",
                    style=STATUS_MARKERS[state][1],
                )
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
            text.append(f"{key:<3}", style="#00e5ff bold")
            text.append(f" {label}\n", style="#d4e1f8")

        if self.filters_active:
            text.append("\n● filtros ativos\n", style="#ff38d1 bold")

        text.append("\n")
        self._line(text, "STATUS")
        for state, label in STATUS_LEGEND:
            marker, color, _ = STATUS_MARKERS[state]
            text.append(f"{marker} ", style=color + " bold")
            text.append(f"{label}\n", style="#d4e1f8")

        if self.mode != ViewMode.MONTH.value:
            text.append("\n")
            self._line(text, "CATEGORIAS")
            for category in Category:
                text.append("● ", style=CATEGORY_COLORS[category.value])
                text.append(f"{category.value.capitalize()}\n", style="#d4e1f8")
        return text


class CalendarGrid(Static):
    class DaySelected(Message):
        def __init__(self, day: date, confirmed: bool = False) -> None:
            self.day = day
            self.confirmed = confirmed
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
        month_start = date(year, month, 1)
        month_end = date(year + (month == 12), month % 12 + 1, 1)
        for task in tasks:
            for day in task.days_between(month_start, month_end):
                self.tasks_by_day.setdefault(day, []).append(task)
        self.refresh()

    def render(self) -> Table:
        return self._render_calendar()

    def _render_calendar(self) -> Table:
        weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(self.year, self.month)
        available_height = self.size.height or 24
        # Em alturas muito reduzidas, retirar apenas as linhas horizontais internas
        # permite manter as seis semanas na tela sem esconder nenhum dia.
        show_lines = available_height >= len(weeks) * 2 + len(weeks) + 3
        fixed_height = 4 if not show_lines else len(weeks) + 3
        data_height = max(len(weeks), available_height - fixed_height)
        cell_height, extra_rows = divmod(data_height, len(weeks))
        table = Table(expand=True, show_edge=True, show_lines=show_lines, box=box.SQUARE,
                      border_style="#24558d", header_style="bold #b8d7f7", padding=(0, 1))
        for heading in ("Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"):
            table.add_column(heading, justify="left", style="#b8cbea", ratio=1)
        for week_index, week in enumerate(weeks):
            row_height = cell_height + (1 if week_index < extra_rows else 0)
            cells: list[Text] = []
            for current in week:
                in_month = current.month == self.month
                tasks = self.tasks_by_day.get(current, [])
                day_style = "#e8efff bold" if in_month else "#3e557a"
                cell = Text(f"{current.day:02d}", style=day_style)
                # O calendário não tem espaço para o [ ] de cada tarefa, então o
                # atraso do dia aparece como um ! ao lado dos pontos.
                alert = next(
                    (
                        STATUS_MARKERS[state][1]
                        for state in ("overdue", "missed")
                        if any(task.display_state() == state for task in tasks)
                    ),
                    None,
                )
                if row_height == 1:
                    cell.append(" ")
                    if current == self.selected_date:
                        cell.append("▸", style="#00e5ff bold")
                    for task in tasks[:3]:
                        cell.append("●", style=CATEGORY_COLORS[task.category.value])
                    if alert:
                        cell.append("!", style=alert + " bold")
                else:
                    cell.append("\n")
                    if current == self.selected_date:
                        cell.append("▸ ", style="#00e5ff bold")
                    for task in tasks[:3]:
                        cell.append("●", style=CATEGORY_COLORS[task.category.value])
                    if alert:
                        cell.append(" !", style=alert + " bold")
                    cell.append("\n" * (row_height - 2))
                if current == self.selected_date:
                    cell_width = max(4, (self.size.width - 8) // 7 - 2)
                    lines = cell.split("\n")
                    for line in lines:
                        line.pad_right(max(0, cell_width - line.cell_len))
                    cell = Text("\n").join(lines)
                    cell.stylize("on #123a65")
                    cell.stylize("bold")
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
            self.post_message(self.DaySelected(self.selected_date, confirmed=True))
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
        def __init__(self, task_id: int | None, confirmed: bool = False) -> None:
            self.task_id = task_id
            self.confirmed = confirmed
            super().__init__()

    class DayShiftRequested(Message):
        """Pedido de mover o cursor um dia; nas pontas o destino cai na semana vizinha."""

        def __init__(self, delta: int) -> None:
            self.delta = delta
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
            for day in task.days_between(start, start + timedelta(days=7)):
                self.tasks_by_day.setdefault(day, []).append(task)
        self.day_index = min(max(self.day_index, 0), 6)
        current_tasks = self._current_day_tasks()
        self.task_index = min(self.task_index, max(len(current_tasks) - 1, 0))
        self.refresh()

    @property
    def selected_day(self) -> date:
        return self.start + timedelta(days=self.day_index)

    def _current_day_tasks(self) -> list[Task]:
        return self.tasks_by_day.get(self.selected_day, [])

    def _current_task_id(self) -> int | None:
        tasks = self._current_day_tasks()
        return tasks[self.task_index].id if tasks and self.task_index < len(tasks) else None

    def _task_line(self, cell: Text, task: Task, selected: bool) -> None:
        start = len(cell)
        marker, marker_color, title_style = STATUS_MARKERS[task.display_state()]
        from textwrap import wrap

        column_width = max(10, (self.size.width // (7 if self.size.width >= 112 else 3 if self.size.width >= 65 else 1)) - 5)
        title = "\n".join(wrap(f"{marker} {task.title}", column_width)[:2])
        cell.append(title + "\n", style=title_style)
        cell.stylize(f"{marker_color} bold", start, start + len(marker))
        cell.append(task.schedule_label + "\n", style="#00e5ff")
        cell.append(f"{task.priority.value.capitalize()}", style=PRIORITY_COLORS[task.priority.value])
        cell.append(" | ", style="#7390b9")
        cell.append(task.category.value.capitalize() + "\n", style=CATEGORY_COLORS[task.category.value])
        if selected:
            cell.stylize("on #124b74", start, len(cell))

    @staticmethod
    def _scrollbar(total: int, capacity: int, offset: int, height: int) -> Text:
        """Barra proporcional para a janela de tarefas visível em uma coluna."""
        track_height = max(1, height)
        thumb_height = max(1, round(track_height * capacity / total))
        travel = track_height - thumb_height
        max_offset = max(1, total - capacity)
        thumb_start = round(travel * offset / max_offset)
        bar = Text()
        for row in range(track_height):
            bar.append("█" if thumb_start <= row < thumb_start + thumb_height else "│",
                       style="#00e5ff" if thumb_start <= row < thumb_start + thumb_height else "#24558d")
            if row < track_height - 1:
                bar.append("\n")
        return bar

    def render(self) -> Table:
        return self._render_board()

    def _render_board(self) -> Table:
        # Em telas estreitas acompanhamos o dia selecionado, sem comprimir sete colunas.
        width = self.size.width or 140
        visible = 7 if width >= 112 else 3 if width >= 65 else 1
        first = min(max(self.day_index - visible // 2, 0), 7 - visible)
        height = max(10, self.size.height or 24)
        capacity = max(1, (height - 6) // 5)
        table = Table.grid(expand=True, padding=(0, 0))
        names = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
        panels = []
        for index in range(first, first + visible):
            table.add_column(ratio=1)
            current = self.start + timedelta(days=index)
            tasks = self.tasks_by_day.get(current, [])
            offset = max(0, self.task_index - capacity + 1) if index == self.day_index else 0
            cell = Text()
            for position, task in enumerate(tasks[offset:offset + capacity], offset):
                self._task_line(cell, task, index == self.day_index and position == self.task_index)
                cell.append("\n")
            if not tasks:
                cell.append("Sem tarefas\n", style="#647fa6")
            content = cell
            if len(tasks) > capacity:
                content_grid = Table.grid(expand=True, padding=(0, 0))
                content_grid.add_column(ratio=1)
                content_grid.add_column(width=1)
                content_grid.add_row(cell, self._scrollbar(len(tasks), capacity, offset, height - 4))
                content = content_grid
            heading = Text(f"{names[index]}  {current:%d/%m}", style="bold #00e5ff")
            selected = index == self.day_index
            color = "#00e5ff" if selected else ("#a855f7" if index % 2 else "#466dc2")
            panels.append(Panel(content, title=heading,
                                border_style=color, box=box.ROUNDED, height=height,
                                padding=(1, 1), style="on #0a1b31" if selected else "on #050d1d"))
        table.add_row(*panels)
        return table

    def on_key(self, event) -> None:
        if event.key in {"left", "right"}:
            # Quem decide o destino é a tela: no domingo e na segunda o cursor
            # atravessa para a semana vizinha em vez de voltar ao outro extremo.
            self.post_message(self.DayShiftRequested(1 if event.key == "right" else -1))
            event.stop()
        elif event.key in {"up", "down"}:
            self._move_task_cursor(1 if event.key == "down" else -1)
            event.stop()
        elif event.key == "enter":
            self.post_message(self.TaskCursorChanged(self._current_task_id(), confirmed=True))
            event.stop()

    def on_mouse_scroll_down(self, event) -> None:
        self._move_task_cursor(1)
        event.stop()

    def on_mouse_scroll_up(self, event) -> None:
        self._move_task_cursor(-1)
        event.stop()

    def _move_task_cursor(self, delta: int) -> None:
        tasks = self._current_day_tasks()
        if tasks:
            self.task_index = (self.task_index + delta) % len(tasks)
            self.update(self._render_board())
            self.post_message(self.TaskCursorChanged(self._current_task_id()))


class TaskTable(DataTable):
    can_focus = True

    def configure_table(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns("#", "STATUS", "TAREFA")

    def fill(self, tasks: list[Task]) -> None:
        self.clear(columns=False)
        for index, task in enumerate(tasks, start=1):
            title = Text()
            title.append(f"{task.schedule_label}  ", style="#00e5ff")
            title.append(task.title, style=STATUS_MARKERS[task.display_state()][2])
            self.add_row(
                Text(str(index), style="#7390b9"),
                status_render(task),
                title,
                key=str(task.id),
            )
