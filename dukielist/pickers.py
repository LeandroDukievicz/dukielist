"""Keyboard-first date picker used by task forms."""

from __future__ import annotations

import calendar
from datetime import date

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .models import format_date
from .widgets import CalendarGrid


MONTH_NAMES = (
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)


def shift_selected_month(selected: date, offset: int) -> date:
    """Move a date by whole months, clamping its day when necessary."""
    month_index = selected.year * 12 + selected.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(selected.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class DatePickerScreen(ModalScreen[date | None]):
    """Full calendar picker with mouse controls and complete keyboard flow."""

    BINDINGS = [
        ("escape", "cancel", "Cancelar"),
        ("t", "today", "Hoje"),
        ("home", "today", "Hoje"),
        ("pageup", "previous_month", "Mês anterior"),
        ("pagedown", "next_month", "Próximo mês"),
    ]

    def __init__(self, selected: date) -> None:
        super().__init__()
        self.selected = selected

    def compose(self) -> ComposeResult:
        yield Vertical(
            Horizontal(
                Static("▣", classes="modal-symbol"),
                Static("ESCOLHER DATA", classes="modal-title"),
                Static("calendário", classes="modal-tagline"),
                Button("×", id="date-picker-close", classes="close-button"),
                classes="modal-heading",
            ),
            Horizontal(
                Button("‹", id="date-picker-previous", classes="period-arrow"),
                Static(id="date-picker-title"),
                Button("›", id="date-picker-next", classes="period-arrow"),
                id="date-picker-navigation",
            ),
            CalendarGrid(id="date-picker-calendar"),
            Horizontal(
                Button(Text("[ Escolher ]"), id="date-picker-confirm", classes="primary-action"),
                Button(Text("[ Hoje ]"), id="date-picker-today", classes="secondary-action"),
                Button(Text("[ Cancelar ]"), id="date-picker-cancel", classes="secondary-action"),
                classes="form-actions",
            ),
            Static(
                "← → dia  •  ↑ ↓ semana  •  PgUp/PgDn mês  •  ENTER escolher  •  T hoje",
                classes="modal-footer-help",
            ),
            id="date-picker-card",
        )

    def on_mount(self) -> None:
        self._update_responsive_class()
        self._refresh_calendar()
        self.query_one("#date-picker-calendar", CalendarGrid).focus()

    def on_resize(self, event: Resize) -> None:
        self._update_responsive_class()

    def _update_responsive_class(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow-layout")

    def _refresh_calendar(self) -> None:
        self.query_one("#date-picker-title", Static).update(
            f"{MONTH_NAMES[self.selected.month - 1]} de {self.selected.year}  •  "
            f"{format_date(self.selected)}"
        )
        self.query_one("#date-picker-calendar", CalendarGrid).set_calendar(
            self.selected.year, self.selected.month, [], self.selected
        )

    def action_previous_month(self) -> None:
        self.selected = shift_selected_month(self.selected, -1)
        self._refresh_calendar()

    def action_next_month(self) -> None:
        self.selected = shift_selected_month(self.selected, 1)
        self._refresh_calendar()

    def action_today(self) -> None:
        self.selected = date.today()
        self._refresh_calendar()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_calendar_grid_day_selected(self, event: CalendarGrid.DaySelected) -> None:
        self.selected = event.day
        if event.confirmed:
            self.dismiss(self.selected)
        else:
            self._refresh_calendar()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "date-picker-previous":
            self.action_previous_month()
            self.query_one("#date-picker-calendar", CalendarGrid).focus()
        elif button_id == "date-picker-next":
            self.action_next_month()
            self.query_one("#date-picker-calendar", CalendarGrid).focus()
        elif button_id == "date-picker-confirm":
            self.dismiss(self.selected)
        elif button_id == "date-picker-today":
            self.action_today()
            self.query_one("#date-picker-calendar", CalendarGrid).focus()
        elif button_id in {"date-picker-cancel", "date-picker-close"}:
            self.action_cancel()
