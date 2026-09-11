from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    RadioButton,
    RadioSet,
    Select,
    Static,
    TextArea,
)

from .models import Category, Priority, Status, Task, ViewMode, format_date, parse_date, parse_time
from .services import TaskFilters, TaskService, month_bounds, shift_month, week_bounds
from .widgets import (
    BrandHeader,
    CalendarGrid,
    CommandPrompt,
    HeaderInfo,
    HeaderSlogan,
    ModeCard,
    ProgressPanel,
    QuotePanel,
    SidebarPanel,
    TaskTable,
    TerminalChrome,
    WeekBoard,
)


QUOTES = {
    ViewMode.DAY: "Disciplina hoje, resultados amanhã.",
    ViewMode.WEEK: "Grandes resultados vêm de semanas consistentes.",
    ViewMode.MONTH: "Consistência hoje, liberdade amanhã.",
}


def _select_value(value: Any, enum_type):
    if value is None or value is Select.NULL or value == getattr(Select, "BLANK", object()):
        return None
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return None


class WelcomeScreen(Screen[None]):
    BINDINGS = [
        ("left", "previous", "Anterior"),
        ("right", "next", "Próximo"),
        ("enter", "select", "Abrir"),
        ("q", "quit", "Sair"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.selected = 0
        self.modes = [ViewMode.DAY, ViewMode.WEEK, ViewMode.MONTH]

    def compose(self) -> ComposeResult:
        yield Container(
            TerminalChrome(id="welcome-chrome"),
            Container(
                Static("[ DUKIELIST / START ]", classes="eyebrow"),
                BrandHeader(classes="welcome-brand"),
                Static("Escolha uma visualização para começar", classes="welcome-prompt"),
                Horizontal(
                    ModeCard("day", "DIA", "Tarefas de hoje", id="welcome-day", classes="mode-card"),
                    ModeCard("week", "SEMANA", "Ritmo dos próximos dias", id="welcome-week", classes="mode-card"),
                    ModeCard("month", "MÊS", "Mapa do mês", id="welcome-month", classes="mode-card"),
                    id="mode-chooser",
                ),
                Static("← → selecionar   •   ENTER abrir   •   TAB navegar   •   Q sair", classes="welcome-help"),
                Static("SQLite local  /  teclado first  /  v1.0.0", classes="welcome-meta"),
                id="welcome-card",
            ),
            id="welcome-shell",
        )

    def on_mount(self) -> None:
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        for index, mode in enumerate(self.modes):
            self.query_one(f"#welcome-{mode.value}", ModeCard).set_class(index == self.selected, "selected")
        self.query_one(f"#welcome-{self.modes[self.selected].value}", ModeCard).focus()

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

    def on_mode_card_selected(self, event: ModeCard.Selected) -> None:
        self.selected = self.modes.index(ViewMode(event.mode))
        self.app.start_main(self.modes[self.selected])


class DayView(Vertical):
    def compose(self) -> ComposeResult:
        yield TaskTable(id="day-table")
        yield Static("Nenhuma tarefa neste período. Pressione A para adicionar.", classes="empty-state", id="day-empty")


class WeekView(Vertical):
    def compose(self) -> ComposeResult:
        yield WeekBoard(id="week-board")
        yield TaskTable(id="week-table")


class MonthView(Vertical):
    def compose(self) -> ComposeResult:
        yield CalendarGrid(id="month-calendar")
        # Mantém a seleção de tarefas do mês disponível para editar/excluir sem ocupar a tela.
        yield TaskTable(id="month-table")


class MainScreen(Screen[None]):
    BINDINGS = [
        ("d", "day_command", "Dia / deletar"),
        ("w", "set_mode('week')", "Semana"),
        ("m", "month_command", "Mês / próximo"),
        ("a", "add_task", "Adicionar"),
        ("e", "edit_task", "Editar"),
        ("c", "toggle_task", "Concluir"),
        ("s", "mark_completed", "Marcar concluída"),
        ("u", "mark_pending", "Desmarcar"),
        ("x", "delete_task", "Excluir"),
        ("delete", "delete_task", "Excluir"),
        ("f", "filter_category", "Filtrar categoria"),
        ("p", "filter_priority", "Filtrar prioridade"),
        ("l", "clear_completed", "Limpar concluídas"),
        ("v", "view_tasks", "Ver tarefas"),
        ("n", "contextual_next", "Próximo"),
        ("b", "contextual_previous", "Anterior"),
        ("h", "show_help", "Ajuda"),
        ("?", "show_help", "Ajuda"),
        ("q", "quit", "Sair"),
        ("tab", "app.focus_next", "Próximo campo"),
        ("shift+tab", "app.focus_previous", "Campo anterior"),
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
            TerminalChrome(id="terminal-chrome"),
            Horizontal(
                BrandHeader(id="brand-header"),
                HeaderSlogan(id="header-slogan"),
                HeaderInfo(id="header-info"),
                id="brand-row",
            ),
            Horizontal(
                Vertical(
                    Horizontal(
                        Static("▦", classes="tabs-icon"),
                        Static("Visualização:", classes="tabs-label"),
                        Button(Text("[ Dia ]"), id="mode-day", classes="view-tab"),
                        Button(Text("[ Semana ]"), id="mode-week", classes="view-tab"),
                        Button(Text("[ Mês ]"), id="mode-month", classes="view-tab"),
                        Static("Use ← → para navegar e ENTER para confirmar", classes="tab-hint"),
                        id="view-tabs",
                    ),
                    Horizontal(
                        Button("‹", id="previous-period", classes="period-arrow"),
                        Static(id="period-title", classes="period-title"),
                        Button("›", id="next-period", classes="period-arrow"),
                        Button(Text("[ ＋ Adicionar tarefa ]"), id="add-task", classes="primary-action"),
                        Button(Text("[ ✎ Editar tarefa ]"), id="edit-task", classes="secondary-action"),
                        id="period-bar",
                    ),
                    VerticalScroll(
                        DayView(id="day-view", classes="view-panel"),
                        WeekView(id="week-view", classes="view-panel"),
                        MonthView(id="month-view", classes="view-panel"),
                        id="views",
                    ),
                    ProgressPanel(id="progress-panel"),
                    id="main-area",
                ),
                Vertical(
                    QuotePanel(id="quote-panel"),
                    VerticalScroll(SidebarPanel(id="sidebar"), id="sidebar-scroll"),
                    id="right-rail",
                ),
                id="workspace-row",
            ),
            Vertical(
                CommandPrompt(id="command-prompt"),
                Horizontal(
                    Static("DukieList v1.0.0", classes="footer-brand"),
                    Static("Produtividade no terminal", classes="footer-productivity"),
                    Static("∞", classes="footer-infinity"),
                    id="status-bar",
                ),
                id="app-footer",
            ),
            id="main-shell",
        )

    def on_mount(self) -> None:
        self._refresh_all()
        self._focus_active_view()

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
            last = end - timedelta(days=1)
            months = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
            return f"Semana de {start.day:02d} de {months[start.month - 1]} a {last.day:02d} de {months[last.month - 1]} de {last.year}"
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
        week_tasks = self.service.list_between(week_start, week_end, self.filters)
        self._fill_table("week-table", week_tasks)
        self.query_one("#week-board", WeekBoard).set_week(week_start, week_tasks)

        month_start, month_end = month_bounds(self.anchor_date)
        month_tasks = self.service.list_between(month_start, month_end, self.filters)
        self.query_one("#month-calendar", CalendarGrid).set_calendar(
            self.anchor_date.year, self.anchor_date.month, month_tasks, self.selected_month_day
        )
        selected_day_tasks = [task for task in month_tasks if task.task_date == self.selected_month_day]
        self._fill_table("month-table", selected_day_tasks)

        mode_label = {ViewMode.DAY: "Progresso do dia", ViewMode.WEEK: "Progresso da semana", ViewMode.MONTH: "Progresso do mês"}[self.mode]
        self.query_one("#progress-panel", ProgressPanel).update_progress(mode_label, self.service.progress(tasks), QUOTES[self.mode])
        self.query_one("#quote-panel", QuotePanel).update_quote("Foco transforma planos em realidade.")
        self.query_one("#sidebar", SidebarPanel).update_for_mode(self.mode.value, tasks, self.filters.active)
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
        table = self.query_one("#day-table", TaskTable)
        table.display = bool(table.rows)
        self.query_one("#day-empty", Static).display = not bool(table.rows)

    def _set_active_mode(self) -> None:
        for mode in ViewMode:
            self.query_one(f"#mode-{mode.value}", Button).set_class(mode is self.mode, "active")
            self.query_one(f"#{mode.value}-view", Vertical).set_class(mode is self.mode, "active-view")

    def _focus_active_view(self) -> None:
        focus_targets = {
            ViewMode.DAY: "#day-table",
            ViewMode.WEEK: "#week-board",
            ViewMode.MONTH: "#month-calendar",
        }
        self.query_one(focus_targets[self.mode]).focus()

    def _current_task(self) -> Task | None:
        active_table = f"{self.mode.value}-table"
        if self.selected_task_id not in self.task_ids.get(active_table, {}).values():
            return None
        return self.service.get(self.selected_task_id) if self.selected_task_id else None

    def action_day_command(self) -> None:
        self.action_delete_task() if self.mode is ViewMode.DAY else self.action_set_mode("day")

    def action_month_command(self) -> None:
        self.action_next_period() if self.mode is ViewMode.MONTH else self.action_set_mode("month")

    def action_set_mode(self, mode: str) -> None:
        self.mode = ViewMode(mode)
        if self.mode is ViewMode.MONTH:
            self.selected_month_day = self.anchor_date
        self._refresh_all()
        self._focus_active_view()

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

    def action_mark_completed(self) -> None:
        task = self._current_task()
        if task and not task.completed:
            self.action_toggle_task()

    def action_mark_pending(self) -> None:
        task = self._current_task()
        if task and task.completed:
            self.action_toggle_task()

    def action_delete_task(self) -> None:
        task = self._current_task()
        if not task:
            self.notify("Selecione uma tarefa primeiro.", title="Nenhuma tarefa", severity="warning")
            return
        self.app.push_screen(ConfirmDeleteScreen(task), self._on_delete_result)

    def action_view_tasks(self) -> None:
        if self.mode is ViewMode.MONTH:
            self.mode = ViewMode.DAY
            self.anchor_date = self.selected_month_day
            self._refresh_all()
            self._focus_active_view()
        elif self.filters.active:
            self.filters = TaskFilters()
            self.selected_task_id = None
            self._refresh_all()
            self.notify("Filtros limpos; todas as tarefas estão visíveis.", title="DukieList", severity="information")

    def action_clear_completed(self) -> None:
        start, end = self._period_range()
        completed = self.service.list_between(start, end, TaskFilters(status=Status.COMPLETED))
        for task in completed:
            if task.id is not None:
                self.service.delete(task.id)
        self.selected_task_id = None
        self._refresh_all()
        self.notify(f"{len(completed)} tarefa(s) concluída(s) removida(s).", title="DukieList", severity="information")

    def action_filter_category(self) -> None:
        self.app.push_screen(FilterScreen(self.filters, focus_field="category"), self._on_filter_result)

    def action_filter_priority(self) -> None:
        self.app.push_screen(FilterScreen(self.filters, focus_field="priority"), self._on_filter_result)

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

    def action_contextual_next(self) -> None:
        if self.mode is ViewMode.MONTH:
            self.action_previous_period()
        else:
            self.action_next_period()

    def action_contextual_previous(self) -> None:
        self.action_previous_period()

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
        table_id = event.data_table.id or ""
        self.selected_task_id = self.task_ids.get(table_id, {}).get(str(event.row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table_id = event.data_table.id or ""
        self.selected_task_id = self.task_ids.get(table_id, {}).get(str(event.row_key.value))

    def on_week_board_task_cursor_changed(self, event: WeekBoard.TaskCursorChanged) -> None:
        if event.task_id is not None:
            self.selected_task_id = event.task_id

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
    BINDINGS = [("escape", "cancel", "Cancelar"), ("s", "save", "Salvar"), ("ctrl+s", "save", "Salvar")]

    def __init__(self, service: TaskService, *, mode: ViewMode, default_date: date | None = None, task: Task | None = None) -> None:
        super().__init__()
        self.service, self.mode, self.edit_task = service, mode, task
        self.default_date = default_date or date.today()

    def compose(self) -> ComposeResult:
        task = self.edit_task
        editing = task is not None
        title = "Editar tarefa" if editing else "Adicionar tarefa"
        yield Container(
            Horizontal(
                Static("✎" if editing else "+", classes="modal-symbol"),
                Static(title, classes="modal-title"),
                Static("Altere os dados da sua tarefa." if editing else "Dê um passo hoje para um amanhã melhor.", classes="modal-tagline"),
                Button("×", id="form-close", classes="close-button"),
                classes="modal-heading",
            ),
            Horizontal(Static("Título da tarefa:", classes="form-label"), Input(value=task.title if task else "", placeholder="Digite o título da tarefa...", id="form-title"), classes="form-row"),
            Horizontal(Static("Descrição:", classes="form-label"), TextArea(task.description if task else "", placeholder="Digite uma descrição (opcional)...", id="form-description"), classes="form-row description-row"),
            Horizontal(Static("Data:", classes="form-label"), Input(value=format_date(task.task_date if task else self.default_date), placeholder="DD/MM/AAAA", id="form-date"), Static("▣", classes="field-icon"), classes="form-row compact-row"),
            Horizontal(Static("Horário:", classes="form-label"), Input(value=task.time_label if task else "", placeholder="--:--", id="form-time"), Static("◷", classes="field-icon"), classes="form-row compact-row"),
            Horizontal(Static("Prioridade:", classes="form-label"), Select([("Baixa", Priority.LOW.value), ("Média", Priority.MEDIUM.value), ("Alta", Priority.HIGH.value)], value=task.priority.value if task else Priority.LOW.value, id="form-priority"), classes="form-row compact-row"),
            Horizontal(Static("Categoria:", classes="form-label"), Select([(item.value.capitalize(), item.value) for item in Category], value=task.category.value if task else Category.PERSONAL.value, id="form-category"), classes="form-row compact-row"),
            Horizontal(
                Static("Visualização:", classes="form-label"),
                RadioSet(
                    RadioButton("Dia", value=(task.view_mode if task else self.mode) is ViewMode.DAY, id="view-day"),
                    RadioButton("Semana", value=(task.view_mode if task else self.mode) is ViewMode.WEEK, id="view-week"),
                    RadioButton("Mês", value=(task.view_mode if task else self.mode) is ViewMode.MONTH, id="view-month"),
                    id="form-view-mode",
                    classes="view-radio",
                ),
                classes="form-row radio-row",
            ),
            *([Horizontal(Static("Status:", classes="form-label"), Select([("Pendente", Status.PENDING.value), ("Concluída", Status.COMPLETED.value)], value=task.status.value, id="form-status"), classes="form-row compact-row")] if editing else []),
            Static("────────────────────────────────────────────────────────", classes="modal-divider"),
            Horizontal(
                Button(Text("[ Salvar alterações ]" if editing else "[ Salvar ]"), id="form-save", classes="primary-action"),
                *([Button(Text("[ Marcar como concluída ]"), id="form-complete", classes="complete-action")] if editing else []),
                Button(Text("[ Cancelar ]"), id="form-cancel", classes="secondary-action"),
                classes="form-actions",
            ),
            Static("Use TAB para navegar entre os campos e ENTER para confirmar.", classes="modal-footer-help"),
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
            status = Status(self.query_one("#form-status", Select).value) if self.edit_task else Status.PENDING
            view_mode = next(button.id.removeprefix("view-") for button in self.query_one("#form-view-mode", RadioSet).query(RadioButton) if button.value)
            selected_view_mode = ViewMode(view_mode)
            if self.edit_task:
                saved = self.service.update(self.edit_task.id, title=title, description=description, task_date=task_date, task_time=task_time, priority=priority, category=category, status=status, view_mode=selected_view_mode)
                action = "updated"
            else:
                saved = self.service.create(title=title, description=description, task_date=task_date, task_time=task_time, priority=priority, category=category, status=Status.PENDING, view_mode=selected_view_mode)
                action = "created"
            self.dismiss({"action": action, "task_id": saved.id, "view_mode": view_mode})
        except (ValueError, TypeError, StopIteration) as exc:
            self.notify(str(exc), title="Verifique os campos", severity="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-save":
            self.action_save()
        elif event.button.id == "form-complete":
            self.query_one("#form-status", Select).value = Status.COMPLETED.value
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
            Static("×  DELETAR TAREFA", classes="modal-title danger"),
            Static("Esta ação não pode ser desfeita.", classes="modal-subtitle"),
            Static(self.confirm_task.title, classes="confirm-task"),
            Static("Deseja realmente remover esta tarefa?", classes="confirm-question"),
            Horizontal(Button(Text("[ Deletar ]"), id="confirm-delete", classes="danger-action"), Button(Text("[ Cancelar ]"), id="confirm-cancel", classes="secondary-action"), classes="form-actions"),
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

    def __init__(self, filters: TaskFilters, focus_field: str = "query") -> None:
        super().__init__()
        self.filters = filters
        self.focus_field = focus_field

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
            Horizontal(Button(Text("[ Aplicar ]"), id="filter-apply", classes="primary-action"), Button(Text("[ Cancelar ]"), id="filter-cancel", classes="secondary-action"), classes="form-actions"),
            id="filter-card",
        )

    def on_mount(self) -> None:
        self.query_one(f"#filter-{self.focus_field}", Input if self.focus_field == "query" else Select).focus()

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
        text.append("navegar entre itens, dias e períodos\n")
        text.append("TAB / SHIFT+TAB  ", style="#ff38d1 bold")
        text.append("mover o foco pelos controles\n")
        text.append("ENTER  ", style="#ff38d1 bold")
        text.append("selecionar / confirmar\n")
        text.append("ESC  ", style="#ff38d1 bold")
        text.append("fechar modal\n\n")
        text.append("ATALHOS\n", style="#00e5ff bold")
        for key, label in (("D / W / M", "alternar modos; D exclui no modo dia e M avança no mês"), ("A", "adicionar tarefa"), ("E", "editar tarefa selecionada"), ("C / S / U", "alternar, concluir ou desmarcar"), ("X / DELETE", "excluir com confirmação"), ("F / P", "filtrar categoria ou prioridade"), ("V", "ver tarefas / limpar filtros"), ("N / B", "navegar período"), ("L", "limpar concluídas"), ("Q", "sair do DukieList")):
            text.append(f"{key:<12}", style="#ff38d1 bold")
            text.append(f" {label}\n", style="#d2def4")
        yield Container(Static("?  ATALHOS DO DUKIELIST", classes="modal-title"), Static(text, classes="help-copy"), Button(Text("[ Fechar ]"), id="help-close", classes="primary-action"), id="help-card")

    def on_mount(self) -> None:
        self.query_one("#help-close", Button).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_close()
