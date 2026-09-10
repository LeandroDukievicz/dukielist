from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Input, Select, Static, TextArea

from .models import Category, Priority, Status, Task, ViewMode, format_date, parse_date, parse_time
from .services import TaskFilters, TaskService, month_bounds, shift_month, week_bounds
from .widgets import BrandHeader, CalendarGrid, ProgressPanel, ShortcutsPanel, SummaryPanel, TaskTable


def _select_value(value: Any, enum_type):
    if value is None or value is Select.NULL or value == getattr(Select, "BLANK", object()):
        return None
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return None


class WelcomeScreen(Screen[None]):
    BINDINGS = [
        ("left", "previous", "Anterior"), ("right", "next", "Próximo"),
        ("enter", "select", "Abrir"), ("q", "quit", "Sair"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.selected = 0
        self.modes = [ViewMode.DAY, ViewMode.WEEK, ViewMode.MONTH]

    def compose(self) -> ComposeResult:
        yield Container(
            Static("[ DUKIELIST / START ]", classes="eyebrow"),
            BrandHeader(classes="welcome-brand"),
            Static("Escolha uma visualização para começar", classes="welcome-prompt"),
            Horizontal(
                Button("[ DIA ]\nTarefas de hoje", id="welcome-day", classes="mode-card"),
                Button("[ SEMANA ]\nRitmo dos próximos dias", id="welcome-week", classes="mode-card"),
                Button("[ MÊS ]\nMapa do mês", id="welcome-month", classes="mode-card"),
                id="mode-chooser",
            ),
            Static("← → selecionar   •   ENTER abrir   •   TAB navegar   •   Q sair", classes="welcome-help"),
            Static("SQLite local  /  teclado first  /  v1.0.0", classes="welcome-meta"),
            id="welcome-card",
        )

    def on_mount(self) -> None:
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        for index, mode in enumerate(self.modes):
            self.query_one(f"#welcome-{mode.value}", Button).set_class(index == self.selected, "selected")
        self.query_one(f"#welcome-{self.modes[self.selected].value}", Button).focus()

    def action_previous(self) -> None:
        self.selected = (self.selected - 1) % len(self.modes)
        self._refresh_selection()

    def action_next(self) -> None:
        self.selected = (self.selected + 1) % len(self.modes)
        self._refresh_selection()

    def action_select(self) -> None:
        self.app.start_main(self.modes[self.selected])

    def action_quit(self) -> None:
        self.app.exit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("welcome-"):
            self.selected = self.modes.index(ViewMode(event.button.id.removeprefix("welcome-")))
            self.app.start_main(self.modes[self.selected])


class DayView(Vertical):
    def compose(self) -> ComposeResult:
        yield TaskTable(id="day-table")
        yield Static("Nenhuma tarefa neste período. Pressione A para adicionar.", classes="empty-state", id="day-empty")


class WeekView(Vertical):
    def compose(self) -> ComposeResult:
        yield TaskTable(id="week-table")
        yield Static("Nenhuma tarefa nesta semana. Pressione A para adicionar.", classes="empty-state", id="week-empty")


class MonthView(Vertical):
    def compose(self) -> ComposeResult:
        yield CalendarGrid(id="month-calendar")
        yield Static("TAREFAS DO DIA SELECIONADO", classes="subheading", id="month-selected-label")
        yield TaskTable(id="month-table")
        yield Static("Use as setas no calendário e ENTER para escolher um dia.", classes="calendar-help")


class MainScreen(Screen[None]):
    BINDINGS = [
        ("d", "set_mode('day')", "Dia"), ("w", "set_mode('week')", "Semana"),
        ("m", "set_mode('month')", "Mês"), ("a", "add_task", "Adicionar"),
        ("e", "edit_task", "Editar"), ("c", "toggle_task", "Concluir"),
        ("x", "delete_task", "Excluir"), ("f", "filter_tasks", "Filtrar"),
        ("n", "next_period", "Próximo"), ("b", "previous_period", "Anterior"),
        ("h", "show_help", "Ajuda"), ("?", "show_help", "Ajuda"),
        ("q", "quit", "Sair"), ("tab", "focus_next", "Próximo campo"),
        ("shift+tab", "focus_previous", "Campo anterior"),
    ]

    def __init__(self, mode: ViewMode, service: TaskService) -> None:
        super().__init__()
        self.mode, self.service = mode, service
        self.anchor_date = date.today()
        self.selected_month_day = date.today()
        self.filters = TaskFilters()
        self.selected_task_id: int | None = None
        self.task_ids: dict[str, dict[str, int]] = {}

    def compose(self) -> ComposeResult:
        yield Container(
            BrandHeader(id="brand-header"),
            Horizontal(
                Static("◫  VISUALIZAÇÃO", classes="tabs-label"),
                Button("[ DIA ]", id="mode-day", classes="view-tab"),
                Button("[ SEMANA ]", id="mode-week", classes="view-tab"),
                Button("[ MÊS ]", id="mode-month", classes="view-tab"),
                Static("D / W / M alternam o modo  •  N/B mudam o período", classes="tab-hint"),
                id="view-tabs",
            ),
            Horizontal(
                Vertical(
                    Horizontal(
                        Button("‹", id="previous-period", classes="period-arrow"),
                        Static(id="period-title", classes="period-title"),
                        Button("›", id="next-period", classes="period-arrow"),
                        Button("＋ ADICIONAR", id="add-task", classes="primary-action"),
                        Button("✎ EDITAR", id="edit-task", classes="secondary-action"),
                        id="period-bar",
                    ),
                    Vertical(
                        DayView(id="day-view", classes="view-panel"),
                        WeekView(id="week-view", classes="view-panel"),
                        MonthView(id="month-view", classes="view-panel"),
                        id="views",
                    ),
                    ProgressPanel(id="progress-panel"),
                    id="main-column",
                ),
                Vertical(
                    SummaryPanel(id="summary-panel"),
                    ShortcutsPanel(id="shortcuts-panel"),
                    id="sidebar",
                ),
                id="body",
            ),
            Horizontal(
                Static("ENTER seleciona  •  ESC fecha modal", classes="footer-left"),
                Static("DukieList  v1.0.0  ·  SQLite local  ·  CTRL+C também sai", classes="footer-right"),
                id="app-footer",
            ),
            id="main-shell",
        )

    def on_mount(self) -> None:
        self._refresh_all()
        self.query_one(f"#mode-{self.mode.value}", Button).focus()

    def _period_range(self) -> tuple[date, date]:
        if self.mode is ViewMode.DAY:
            return self.anchor_date, self.anchor_date + timedelta(days=1)
        if self.mode is ViewMode.WEEK:
            return week_bounds(self.anchor_date)
        return month_bounds(self.anchor_date)

    def _period_label(self) -> str:
        if self.mode is ViewMode.DAY:
            names = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
            months = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
            return f"{names[self.anchor_date.weekday()].capitalize()}, {self.anchor_date.day:02d} de {months[self.anchor_date.month - 1]} de {self.anchor_date.year}"
        if self.mode is ViewMode.WEEK:
            start, end = week_bounds(self.anchor_date)
            return f"Semana de {start:%d/%m} a {(end - timedelta(days=1)):%d/%m/%Y}"
        months = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
        return f"{months[self.anchor_date.month - 1]} de {self.anchor_date.year}"

    def _refresh_all(self) -> None:
        start, end = self._period_range()
        tasks = self.service.list_between(start, end, self.filters)
        self.query_one("#period-title", Static).update(self._period_label())
        self._set_active_mode()
        day_tasks = self.service.list_between(self.anchor_date, self.anchor_date + timedelta(days=1), self.filters)
        self._fill_table("day-table", day_tasks)
        week_start, week_end = week_bounds(self.anchor_date)
        self._fill_table("week-table", self.service.list_between(week_start, week_end, self.filters))
        month_start, month_end = month_bounds(self.anchor_date)
        month_tasks = self.service.list_between(month_start, month_end, self.filters)
        calendar_grid = self.query_one("#month-calendar", CalendarGrid)
        calendar_grid.set_calendar(self.anchor_date.year, self.anchor_date.month, month_tasks, self.selected_month_day)
        selected_day_tasks = [task for task in month_tasks if task.task_date == self.selected_month_day]
        self._fill_table("month-table", selected_day_tasks)
        self.query_one("#month-selected-label", Static).update(f"TAREFAS DE {self.selected_month_day:%d/%m/%Y}")
        self._update_summary(tasks, self._period_label())
        self._update_empty_states()

    def _fill_table(self, table_id: str, tasks: list[Task]) -> None:
        table = self.query_one(f"#{table_id}", TaskTable)
        if not table.columns:
            table.configure_table()
        table.fill(tasks)
        self.task_ids[table_id] = {str(task.id): task.id for task in tasks if task.id is not None}
        if tasks and self.selected_task_id not in self.task_ids[table_id].values():
            self.selected_task_id = tasks[0].id

    def _update_empty_states(self) -> None:
        for table_id, empty_id in (("day-table", "day-empty"), ("week-table", "week-empty")):
            table = self.query_one(f"#{table_id}", TaskTable)
            self.query_one(f"#{empty_id}", Static).display = not bool(table.rows)

    def _update_summary(self, tasks: list[Task], label: str) -> None:
        category_counts = {category.value: 0 for category in Category}
        for task in tasks:
            category_counts[task.category.value] += 1
        progress = self.service.progress(tasks)
        self.query_one("#summary-panel", SummaryPanel).update_summary(progress, label, category_counts)
        self.query_one("#progress-panel", ProgressPanel).update_progress(f"Progresso do {self.mode.value}", progress)
        self.query_one("#shortcuts-panel", ShortcutsPanel).update_mode(self.mode.value, self.filters.active)

    def _set_active_mode(self) -> None:
        for mode in ViewMode:
            self.query_one(f"#mode-{mode.value}", Button).set_class(mode is self.mode, "active")
            self.query_one(f"#{mode.value}-view", Vertical).set_class(mode is self.mode, "active-view")

    def _current_task(self) -> Task | None:
        active_table = f"{self.mode.value}-table"
        if self.selected_task_id not in self.task_ids.get(active_table, {}).values():
            return None
        return self.service.get(self.selected_task_id) if self.selected_task_id else None

    def action_set_mode(self, mode: str) -> None:
        self.mode = ViewMode(mode)
        if self.mode is ViewMode.MONTH:
            self.selected_month_day = self.anchor_date
        self._refresh_all()

    def action_add_task(self) -> None:
        default_date = self.selected_month_day if self.mode is ViewMode.MONTH else self.anchor_date
        self.app.push_screen(TaskFormScreen(self.service, mode=self.mode, default_date=default_date), self._on_form_result)

    def action_edit_task(self) -> None:
        task = self._current_task()
        if not task:
            self.notify("Selecione uma tarefa primeiro.", title="Nenhuma tarefa", severity="warning")
            return
        self.app.push_screen(TaskFormScreen(self.service, mode=self.mode, task=task), self._on_form_result)

    def action_toggle_task(self) -> None:
        task = self._current_task()
        if not task:
            self.notify("Selecione uma tarefa primeiro.", title="Nenhuma tarefa", severity="warning")
            return
        try:
            updated = self.service.toggle(task.id)
            self.notify("Tarefa concluída." if updated.completed else "Tarefa reaberta.", title="Atualizado", severity="information")
            self._refresh_all()
        except ValueError as exc:
            self.notify(str(exc), title="Erro", severity="error")

    def action_delete_task(self) -> None:
        task = self._current_task()
        if not task:
            self.notify("Selecione uma tarefa primeiro.", title="Nenhuma tarefa", severity="warning")
            return
        self.app.push_screen(ConfirmDeleteScreen(task), self._on_delete_result)

    def action_filter_tasks(self) -> None:
        self.app.push_screen(FilterScreen(self.filters), self._on_filter_result)

    def action_next_period(self) -> None:
        if self.mode is ViewMode.DAY:
            self.anchor_date += timedelta(days=1)
        elif self.mode is ViewMode.WEEK:
            self.anchor_date += timedelta(days=7)
        else:
            self.anchor_date = shift_month(self.anchor_date, 1)
            self.selected_month_day = self.anchor_date
        self._refresh_all()

    def action_previous_period(self) -> None:
        if self.mode is ViewMode.DAY:
            self.anchor_date -= timedelta(days=1)
        elif self.mode is ViewMode.WEEK:
            self.anchor_date -= timedelta(days=7)
        else:
            self.anchor_date = shift_month(self.anchor_date, -1)
            self.selected_month_day = self.anchor_date
        self._refresh_all()

    def action_show_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_quit(self) -> None:
        self.app.exit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("mode-"):
            self.action_set_mode(button_id.removeprefix("mode-"))
        elif button_id == "add-task":
            self.action_add_task()
        elif button_id == "edit-task":
            self.action_edit_task()
        elif button_id == "previous-period":
            self.action_previous_period()
        elif button_id == "next-period":
            self.action_next_period()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._remember_row(event)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._remember_row(event)

    def _remember_row(self, event) -> None:
        table_id = event.data_table.id or ""
        self.selected_task_id = self.task_ids.get(table_id, {}).get(str(event.row_key.value))

    def on_calendar_grid_day_selected(self, event: CalendarGrid.DaySelected) -> None:
        self.selected_month_day = event.day
        if event.day.month != self.anchor_date.month or event.day.year != self.anchor_date.year:
            self.anchor_date = event.day
        self._refresh_all()

    def _on_form_result(self, result: dict[str, Any] | None) -> None:
        if not result:
            return
        self.selected_task_id = result.get("task_id")
        saved_task = self.service.get(self.selected_task_id) if self.selected_task_id else None
        if saved_task:
            self.anchor_date = saved_task.task_date
            self.selected_month_day = saved_task.task_date
        if result.get("view_mode"):
            self.mode = ViewMode(result["view_mode"])
        self._refresh_all()
        action = "criada" if result.get("action") == "created" else "atualizada"
        self.notify(f"Tarefa {action} com sucesso.", title="DukieList", severity="information")

    def _on_delete_result(self, result: int | None) -> None:
        if result is None:
            return
        try:
            if self.service.delete(result):
                self.selected_task_id = None
                self._refresh_all()
                self.notify("Tarefa removida.", title="DukieList", severity="information")
        except Exception as exc:
            self.notify(str(exc), title="Erro ao remover", severity="error")

    def _on_filter_result(self, filters: TaskFilters | None) -> None:
        if filters is None:
            return
        self.filters = filters
        self.selected_task_id = None
        self._refresh_all()
        self.notify("Filtros aplicados." if filters.active else "Filtros limpos.", title="DukieList", severity="information")


class TaskFormScreen(ModalScreen[dict[str, Any] | None]):
    BINDINGS = [("escape", "cancel", "Cancelar"), ("ctrl+s", "save", "Salvar")]

    def __init__(self, service: TaskService, *, mode: ViewMode, default_date: date | None = None, task: Task | None = None) -> None:
        super().__init__()
        self.service, self.mode, self.edit_task = service, mode, task
        self.default_date = default_date or date.today()

    def compose(self) -> ComposeResult:
        task = self.edit_task
        title = "EDITAR TAREFA" if task else "ADICIONAR TAREFA"
        yield Container(
            VerticalScroll(
                Horizontal(
                    Static("✎  " if task else "＋  ", classes="modal-symbol"),
                    Static(title, classes="modal-title"),
                    Button("×", id="form-close", classes="close-button"),
                    classes="modal-heading",
                ),
                Static("Preencha os dados e confirme com ENTER ou CTRL+S.", classes="modal-subtitle"),
                Static("TÍTULO", classes="field-label"),
                Input(value=task.title if task else "", placeholder="Ex.: Estudar programação", id="form-title"),
                Static("DESCRIÇÃO", classes="field-label"),
                TextArea(task.description if task else "", id="form-description"),
                Horizontal(
                    Vertical(Static("DATA", classes="field-label"), Input(value=format_date(task.task_date if task else self.default_date), placeholder="DD/MM/AAAA", id="form-date"), classes="field-half"),
                    Vertical(Static("HORÁRIO (OPCIONAL)", classes="field-label"), Input(value=task.time_label if task else "", placeholder="HH:MM", id="form-time"), classes="field-half"),
                    classes="fields-row",
                ),
                Horizontal(
                    Vertical(Static("PRIORIDADE", classes="field-label"), Select([("Baixa", Priority.LOW.value), ("Média", Priority.MEDIUM.value), ("Alta", Priority.HIGH.value)], value=task.priority.value if task else Priority.MEDIUM.value, id="form-priority"), classes="field-half"),
                    Vertical(Static("CATEGORIA", classes="field-label"), Select([(item.value.capitalize(), item.value) for item in Category], value=task.category.value if task else Category.PERSONAL.value, id="form-category"), classes="field-half"),
                    classes="fields-row",
                ),
                Horizontal(
                    Vertical(Static("VISUALIZAÇÃO APÓS SALVAR", classes="field-label"), Select([("Dia", ViewMode.DAY.value), ("Semana", ViewMode.WEEK.value), ("Mês", ViewMode.MONTH.value)], value=self.mode.value, id="form-view-mode"), classes="field-half"),
                    Vertical(Static("STATUS", classes="field-label"), Select([("Pendente", Status.PENDING.value), ("Concluída", Status.COMPLETED.value)], value=task.status.value if task else Status.PENDING.value, id="form-status"), classes="field-half"),
                    classes="fields-row",
                ),
                Static("────────────────────────────────────────", classes="modal-divider"),
                Horizontal(Button("[ SALVAR ]", id="form-save", classes="primary-action"), Button("[ CANCELAR ]", id="form-cancel", classes="secondary-action"), classes="form-actions"),
                id="form-card",
            ),
            id="form-overlay",
        )

    def on_mount(self) -> None:
        self.query_one("#form-title", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        try:
            title = self.query_one("#form-title", Input).value
            description = self.query_one("#form-description", TextArea).text
            task_date = parse_date(self.query_one("#form-date", Input).value)
            task_time = parse_time(self.query_one("#form-time", Input).value)
            priority = Priority(self.query_one("#form-priority", Select).value)
            category = Category(self.query_one("#form-category", Select).value)
            status = Status(self.query_one("#form-status", Select).value)
            view_mode = str(self.query_one("#form-view-mode", Select).value)
            if self.edit_task:
                saved = self.service.update(self.edit_task.id, title=title, description=description, task_date=task_date, task_time=task_time, priority=priority, category=category, status=status)
                action = "updated"
            else:
                saved = self.service.create(title=title, description=description, task_date=task_date, task_time=task_time, priority=priority, category=category, status=status)
                action = "created"
            self.dismiss({"action": action, "task_id": saved.id, "view_mode": view_mode})
        except (ValueError, TypeError) as exc:
            self.notify(str(exc), title="Verifique os campos", severity="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-save":
            self.action_save()
        elif event.button.id in {"form-cancel", "form-close"}:
            self.action_cancel()


class ConfirmDeleteScreen(ModalScreen[int | None]):
    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, task: Task) -> None:
        super().__init__()
        self.confirm_task = task

    def compose(self) -> ComposeResult:
        yield Container(
            Static("×  EXCLUIR TAREFA", classes="modal-title danger"),
            Static("Esta ação não pode ser desfeita.", classes="modal-subtitle"),
            Static(self.confirm_task.title, classes="confirm-task"),
            Static("Deseja realmente remover esta tarefa?", classes="confirm-question"),
            Horizontal(Button("[ EXCLUIR ]", id="confirm-delete", classes="danger-action"), Button("[ CANCELAR ]", id="confirm-cancel", classes="secondary-action"), classes="form-actions"),
            id="confirm-card",
        )

    def on_mount(self) -> None:
        self.query_one("#confirm-cancel", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self.confirm_task.id if event.button.id == "confirm-delete" else None)


class FilterScreen(ModalScreen[TaskFilters | None]):
    BINDINGS = [("escape", "cancel", "Cancelar"), ("ctrl+enter", "apply", "Aplicar")]

    def __init__(self, filters: TaskFilters) -> None:
        super().__init__()
        self.filters = filters

    def compose(self) -> ComposeResult:
        yield Container(
            Static("⌕  FILTRAR TAREFAS", classes="modal-title"),
            Static("Combine os campos ou deixe tudo vazio para limpar.", classes="modal-subtitle"),
            Static("BUSCAR", classes="field-label"),
            Input(value=self.filters.query, placeholder="Título ou descrição", id="filter-query"),
            Static("CATEGORIA", classes="field-label"),
            Select([(item.value.capitalize(), item.value) for item in Category], value=self.filters.category.value if self.filters.category else Select.NULL, allow_blank=True, prompt="Todas", id="filter-category"),
            Static("PRIORIDADE", classes="field-label"),
            Select([(item.value.capitalize(), item.value) for item in Priority], value=self.filters.priority.value if self.filters.priority else Select.NULL, allow_blank=True, prompt="Todas", id="filter-priority"),
            Static("STATUS", classes="field-label"),
            Select([(item.value.capitalize(), item.value) for item in Status], value=self.filters.status.value if self.filters.status else Select.NULL, allow_blank=True, prompt="Todos", id="filter-status"),
            Horizontal(Button("[ APLICAR ]", id="filter-apply", classes="primary-action"), Button("[ CANCELAR ]", id="filter-cancel", classes="secondary-action"), classes="form-actions"),
            id="filter-card",
        )

    def on_mount(self) -> None:
        self.query_one("#filter-query", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_apply(self) -> None:
        self.dismiss(TaskFilters(
            category=_select_value(self.query_one("#filter-category", Select).value, Category),
            priority=_select_value(self.query_one("#filter-priority", Select).value, Priority),
            status=_select_value(self.query_one("#filter-status", Select).value, Status),
            query=self.query_one("#filter-query", Input).value,
        ))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_apply() if event.button.id == "filter-apply" else self.action_cancel()


class HelpScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Fechar"), ("h", "close", "Fechar")]

    def compose(self) -> ComposeResult:
        text = Text()
        text.append("NAVEGAÇÃO\n", style="#00e5ff bold")
        text.append("↑ ↓ ← →  ", style="#ff38d1 bold")
        text.append("navegar entre itens / períodos\n")
        text.append("TAB / SHIFT+TAB  ", style="#ff38d1 bold")
        text.append("mover o foco\n")
        text.append("ENTER  ", style="#ff38d1 bold")
        text.append("selecionar / confirmar\n")
        text.append("ESC  ", style="#ff38d1 bold")
        text.append("fechar modal\n\n")
        text.append("AÇÕES\n", style="#00e5ff bold")
        for key, label in (("D / W / M", "alternar Dia, Semana ou Mês"), ("A", "adicionar tarefa"), ("E", "editar tarefa selecionada"), ("C", "concluir / reabrir tarefa"), ("X", "excluir com confirmação"), ("F", "filtrar por texto, categoria, prioridade e status"), ("N / B", "próximo / período anterior"), ("Q", "sair do DukieList")):
            text.append(f"{key:<10}", style="#ff38d1 bold")
            text.append(f" {label}\n", style="#d2def4")
        yield Container(Static("?  ATALHOS DO DUKIELIST", classes="modal-title"), Static(text, classes="help-copy"), Button("[ FECHAR ]", id="help-close", classes="primary-action"), id="help-card")

    def on_mount(self) -> None:
        self.query_one("#help-close", Button).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_close()
