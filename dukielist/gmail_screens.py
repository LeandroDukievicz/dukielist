"""Textual screens for the read-only Gmail integration."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from .gmail import GmailClient, GmailError, GmailMessage


class GmailInboxScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Fechar"),
        ("q", "close", "Fechar"),
        ("r", "refresh", "Atualizar"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.messages: dict[str, GmailMessage] = {}

    def compose(self) -> ComposeResult:
        yield Vertical(
            Horizontal(
                Static("✉", classes="modal-symbol"),
                Static("GMAIL / CAIXA DE ENTRADA", classes="modal-title"),
                Static("somente leitura", classes="modal-tagline"),
                Button("×", id="gmail-close", classes="close-button"),
                classes="modal-heading",
            ),
            Static(
                "Conectando ao Gmail… Na primeira utilização, autorize no navegador.",
                id="gmail-status",
            ),
            DataTable(id="gmail-table", cursor_type="row", zebra_stripes=True),
            Horizontal(
                Button("↻ ATUALIZAR", id="gmail-refresh", classes="primary-action"),
                Button("FECHAR", id="gmail-footer-close", classes="secondary-action"),
                classes="form-actions",
            ),
            Static("ENTER abre a mensagem  •  R atualiza  •  ESC fecha", classes="modal-footer-help"),
            id="gmail-card",
        )

    def on_mount(self) -> None:
        self._update_responsive_class()
        table = self.query_one("#gmail-table", DataTable)
        table.add_columns("", "De", "Assunto", "Prévia", "Recebido")
        table.focus()
        self.action_refresh()

    def action_refresh(self) -> None:
        self.run_worker(self._load_messages(), group="gmail-list", exclusive=True)

    async def _load_messages(self) -> None:
        status = self.query_one("#gmail-status", Static)
        status.update("Consultando a caixa de entrada…")
        try:
            messages = await asyncio.to_thread(GmailClient().list_inbox)
        except GmailError as exc:
            status.update(str(exc))
            self.notify(str(exc), title="Gmail", severity="error", timeout=12)
            return
        except Exception as exc:  # Keep third-party failures from crashing the TUI.
            message = f"Erro inesperado ao abrir o Gmail: {exc}"
            status.update(message)
            self.notify(message, title="Gmail", severity="error", timeout=12)
            return

        self.messages = {message.id: message for message in messages}
        table = self.query_one("#gmail-table", DataTable)
        table.clear()
        for message in messages:
            marker = Text("●", style="#ff38d1") if message.unread else Text("○", style="#466da0")
            sender = Text(message.sender, style="bold" if message.unread else "")
            subject = Text(message.subject, style="bold" if message.unread else "")
            table.add_row(marker, sender, subject, message.snippet, message.received_label, key=message.id)
        if messages:
            unread = sum(message.unread for message in messages)
            status.update(f"{len(messages)} mensagens recentes da caixa de entrada • {unread} não lidas")
            table.move_cursor(row=0)
        else:
            status.update("A caixa de entrada está vazia.")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        message_id = str(event.row_key.value)
        self.run_worker(
            self._open_message(message_id), group="gmail-open", exclusive=True
        )

    async def _open_message(self, message_id: str) -> None:
        status = self.query_one("#gmail-status", Static)
        status.update("Abrindo mensagem…")
        try:
            message = await asyncio.to_thread(GmailClient().get_message, message_id)
        except GmailError as exc:
            status.update(str(exc))
            self.notify(str(exc), title="Gmail", severity="error", timeout=10)
            return
        except Exception as exc:
            message_text = f"Erro inesperado ao abrir a mensagem: {exc}"
            status.update(message_text)
            self.notify(message_text, title="Gmail", severity="error", timeout=10)
            return
        status.update(f"{len(self.messages)} mensagens recentes da caixa de entrada")
        self.app.push_screen(GmailMessageScreen(message))

    def action_close(self) -> None:
        self.workers.cancel_group(self, "gmail-list")
        self.workers.cancel_group(self, "gmail-open")
        self.dismiss(None)

    def on_resize(self, event: Resize) -> None:
        self._update_responsive_class()

    def _update_responsive_class(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow-layout")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "gmail-refresh":
            self.action_refresh()
        else:
            self.action_close()


class GmailMessageScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Fechar"), ("q", "close", "Fechar")]

    def __init__(self, message: GmailMessage) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        sender = self.message.sender
        if self.message.sender_address and self.message.sender_address != sender:
            sender += f" <{self.message.sender_address}>"
        metadata = Text()
        metadata.append("De: ", style="#00e5ff bold")
        metadata.append(sender + "\n")
        metadata.append("Para: ", style="#00e5ff bold")
        metadata.append((self.message.recipient or "—") + "\n")
        metadata.append("Recebido: ", style="#00e5ff bold")
        metadata.append(self.message.received_label)
        yield Vertical(
            Horizontal(
                Static("✉", classes="modal-symbol"),
                Static(self.message.subject, classes="gmail-message-title"),
                Button("×", id="gmail-message-close", classes="close-button"),
                classes="modal-heading",
            ),
            Static(metadata, id="gmail-message-metadata"),
            VerticalScroll(Static(Text(self.message.body), id="gmail-message-body"), id="gmail-body-scroll"),
            Horizontal(
                Button("VOLTAR", id="gmail-message-back", classes="primary-action"),
                classes="form-actions",
            ),
            Static("Somente leitura • anexos não são baixados", classes="modal-footer-help"),
            id="gmail-message-card",
        )

    def on_mount(self) -> None:
        self._update_responsive_class()
        self.query_one("#gmail-body-scroll", VerticalScroll).focus()

    def on_resize(self, event: Resize) -> None:
        self._update_responsive_class()

    def _update_responsive_class(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow-layout")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_close()
