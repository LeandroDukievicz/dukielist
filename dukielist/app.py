from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from textual.app import App

from .google_calendar import (
    CALENDAR_SETTING_KEY as GOOGLE_CALENDAR_SETTING,
    DEFAULT_CALENDAR_ID as GOOGLE_DEFAULT_CALENDAR,
    DEFAULT_TIMEZONE as GOOGLE_TIMEZONE,
    GoogleAuthRequiredError,
    GoogleCalendarError,
    SOURCE as GOOGLE_SOURCE,
    authorize as authorize_google,
    build_client as build_google_client,
    is_authorized as google_is_authorized,
    sync_google_calendar,
)
from .logs import setup_logging
from .screens import MainScreen, WelcomeScreen
from .services import TaskService
from .google_calendar import calendar_id_for as google_calendar_id
from .storage import SQLiteStorage
from .trello import (
    DEFAULT_TIMEZONE,
    TrelloClient,
    TrelloError,
    load_credentials,
    sync_linked_completions,
    sync_trello,
)


class DukieListApp(App[None]):
    TITLE = "DukieList"
    SUB_TITLE = "Terminal task control"
    CSS_PATH = "theme.tcss"

    def __init__(
        self,
        db_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        setup_logging()
        self.storage = SQLiteStorage(db_path)
        self.service = TaskService(self.storage)
        self._trello_sync_lock = asyncio.Lock()
        self._trello_timer = None
        self._google_sync_lock = asyncio.Lock()
        self._google_timer = None
        self._google_warned = False
        self._google_read_warned = False
        self._google_auth_in_progress = False

    def on_mount(self) -> None:
        self.push_screen(WelcomeScreen())

    def start_main(self, mode) -> None:
        self.push_screen(MainScreen(mode, self.service))
        if self._trello_timer is None:
            self._trello_timer = self.set_interval(60, self._schedule_trello_pull)
        self.call_after_refresh(self._schedule_trello_pull)
        if self._google_enabled():
            self._start_google_updates()

    def _start_google_updates(self) -> None:
        if self._google_timer is None:
            self._google_timer = self.set_interval(120, self._schedule_google_pull)
        self.call_after_refresh(self._schedule_google_pull)

    def request_google_auth(self) -> None:
        if self._google_auth_in_progress:
            self.notify("A autorização do Google já está em andamento.", title="Google Agenda")
            return
        if self._google_enabled():
            self.notify("Agenda conectada; sincronizando agora.", title="Google Agenda")
            self._schedule_google_pull()
            return
        self._google_auth_in_progress = True
        self.notify("Abrindo o navegador para conectar sua agenda…", title="Google Agenda")
        self.run_worker(self._authorize_google_from_ui(), group="google-auth")

    async def _authorize_google_from_ui(self) -> None:
        try:
            calendar_name = await asyncio.to_thread(
                authorize_google,
                calendar_id=google_calendar_id(self.storage),
                quiet=True,
            )
        except GoogleCalendarError as exc:
            self.notify(str(exc), title="Google Agenda", severity="error", timeout=12)
        else:
            self.storage.enable_mirror(GOOGLE_SOURCE)
            self.notify(f"Agenda {calendar_name} conectada.", title="Google Agenda")
            self._start_google_updates()
        finally:
            self._google_auth_in_progress = False

    def _google_enabled(self) -> bool:
        return google_is_authorized() and GOOGLE_SOURCE in self.storage.mirror_sources()

    def request_external_sync(self) -> None:
        """Envia uma alteração local sem travar o teclado."""
        self.request_trello_completion_sync()
        self.request_google_sync()

    def request_google_sync(self) -> None:
        if not self._google_enabled():
            return
        self.run_worker(
            self._sync_google(outbound_only=True, notify_errors=True),
            group="google-calendar",
        )

    def _schedule_google_pull(self) -> None:
        if self._google_sync_lock.locked():
            return
        self.run_worker(
            self._sync_google(outbound_only=False, notify_errors=False),
            group="google-calendar",
        )

    async def _sync_google(self, *, outbound_only: bool, notify_errors: bool) -> None:
        async with self._google_sync_lock:
            if outbound_only and not self.storage.pending_outbox(GOOGLE_SOURCE):
                return
            try:
                stats = await asyncio.to_thread(self._run_google_sync, outbound_only)
            except GoogleAuthRequiredError as exc:
                # Um aviso por sessão evita notificações repetidas a cada leitura.
                if not self._google_warned:
                    self._google_warned = True
                    self.notify(
                        f"{exc}",
                        title="Google Calendar",
                        severity="warning",
                        timeout=10,
                    )
            except GoogleCalendarError as exc:
                if notify_errors:
                    self.notify(
                        f"Alteração salva localmente; envio pendente: {exc}",
                        title="Google Calendar",
                        severity="warning",
                        timeout=8,
                    )
                elif not self._google_read_warned:
                    # A leitura periódica falhava em silêncio: a agenda parava de
                    # chegar sem nenhum sinal. Um aviso por sessão basta.
                    self._google_read_warned = True
                    self.notify(
                        f"Não foi possível ler a agenda do Google: {exc}",
                        title="Google Calendar",
                        severity="warning",
                        timeout=8,
                    )
            else:
                self._google_warned = False
                self._google_read_warned = False
                if not outbound_only and isinstance(self.screen, MainScreen):
                    self.screen._refresh_all()
                if not notify_errors:
                    return
                if stats.failed_remote:
                    self.notify(
                        "Alteração salva, mas um evento vinculado não está mais "
                        "acessível; a tentativa ficou pendente.",
                        title="Google Calendar",
                        severity="warning",
                        timeout=8,
                    )
                elif stats.created_remote or stats.updated_remote or stats.deleted_remote:
                    self.notify(
                        "Agenda do Google atualizada.",
                        title="Sincronizado",
                        severity="information",
                    )

    def _run_google_sync(self, outbound_only: bool):
        return sync_google_calendar(
            self.storage,
            build_google_client(self.storage),
            outbound_only=outbound_only,
        )

    def request_trello_completion_sync(self) -> None:
        """Upload a local status without blocking keyboard interaction."""
        self.run_worker(
            self._sync_trello_completions(outbound_only=True, notify_errors=True),
            group="trello-completion",
        )

    def _schedule_trello_pull(self) -> None:
        if self._trello_sync_lock.locked():
            return
        self.run_worker(
            self._sync_trello_completions(outbound_only=False, notify_errors=False),
            group="trello-completion",
        )

    async def _sync_trello_completions(
        self, *, outbound_only: bool, notify_errors: bool
    ) -> None:
        async with self._trello_sync_lock:
            links = self.storage.external_completion_links("trello")
            if not links:
                return
            if outbound_only and not any(
                link["pending_completed"] is not None for link in links
            ):
                return

            try:
                stats = await asyncio.to_thread(
                    sync_linked_completions,
                    self.storage,
                    TrelloClient(load_credentials()),
                    outbound_only=outbound_only,
                )
            except TrelloError as exc:
                if notify_errors:
                    self.notify(
                        f"Alteração salva localmente; sincronização pendente: {exc}",
                        title="Trello",
                        severity="warning",
                        timeout=8,
                    )
            else:
                # Another DukieList process may have changed the shared DB.
                # Refresh after every full cycle, even when this process did
                # not itself pull a Trello change.
                if not outbound_only and isinstance(self.screen, MainScreen):
                    self.screen._refresh_all()
                if stats.failed and notify_errors:
                    self.notify(
                        "Alteração salva, mas um cartão vinculado não está mais acessível; "
                        "a tentativa ficou pendente.",
                        title="Trello",
                        severity="warning",
                        timeout=8,
                    )
                elif stats.pushed and notify_errors:
                    self.notify(
                        "Status atualizado também no Trello.",
                        title="Sincronizado",
                        severity="information",
                    )


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="DukieList — gerenciador de tarefas no terminal")
    parser.add_argument("--db", type=Path, help="caminho alternativo para o banco SQLite")
    commands = parser.add_subparsers(dest="command")
    sync_parser = commands.add_parser("sync", help="sincronizar tarefas externas")
    providers = sync_parser.add_subparsers(dest="provider", required=True)
    trello_parser = providers.add_parser("trello", help="importar compromissos do Trello")
    trello_parser.add_argument("--env-file", type=Path, help="arquivo .env com as credenciais")
    trello_parser.add_argument(
        "--board",
        action="append",
        default=[],
        help="nome ou ID do quadro; pode ser usado mais de uma vez",
    )
    trello_parser.add_argument(
        "--from-date",
        type=date.fromisoformat,
        default=date.today(),
        metavar="AAAA-MM-DD",
        help="data inicial para novas importações (padrão: hoje)",
    )
    trello_parser.add_argument(
        "--include-completed",
        action="store_true",
        help="também importar cartões já concluídos",
    )
    trello_parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"fuso dos prazos (padrão: {DEFAULT_TIMEZONE})",
    )
    google_parser = providers.add_parser(
        "google", help="sincronizar com o Google Calendar nos dois sentidos"
    )
    google_parser.add_argument(
        "--calendar",
        help=f"ID da agenda (padrão: {GOOGLE_DEFAULT_CALENDAR})",
    )
    google_parser.add_argument(
        "--from-date",
        type=date.fromisoformat,
        metavar="AAAA-MM-DD",
        help="início da janela lida da agenda (padrão: 7 dias atrás)",
    )
    google_parser.add_argument(
        "--to-date",
        type=date.fromisoformat,
        metavar="AAAA-MM-DD",
        help="fim da janela lida da agenda (padrão: 180 dias à frente)",
    )
    google_parser.add_argument(
        "--push-only",
        action="store_true",
        help="apenas enviar alterações locais, sem ler a agenda",
    )
    google_parser.add_argument(
        "--timezone",
        default=GOOGLE_TIMEZONE,
        help=f"fuso dos horários (padrão: {GOOGLE_TIMEZONE})",
    )

    auth_parser = commands.add_parser("auth", help="autorizar integrações externas")
    auth_providers = auth_parser.add_subparsers(dest="provider", required=True)
    google_auth = auth_providers.add_parser(
        "google", help="autorizar o acesso ao Google Calendar"
    )
    google_auth.add_argument(
        "--credentials",
        type=Path,
        help="JSON do cliente OAuth (padrão: ~/.config/dukielist/google_credentials.json)",
    )
    google_auth.add_argument("--calendar", help="ID da agenda a ser usada")

    args = parser.parse_args()

    if args.command == "auth" and args.provider == "google":
        storage = SQLiteStorage(args.db)
        if args.calendar:
            storage.set_setting(GOOGLE_CALENDAR_SETTING, args.calendar)
        try:
            calendar_name = authorize_google(
                credentials_path=args.credentials,
                calendar_id=google_calendar_id(storage),
            )
        except GoogleCalendarError as exc:
            parser.error(str(exc))
        storage.enable_mirror(GOOGLE_SOURCE)
        print(f"Google Calendar autorizado para a agenda {calendar_name}.")
        try:
            stats = sync_google_calendar(storage, build_google_client(storage))
        except GoogleCalendarError as exc:
            print(f"Sincronização inicial pendente: {exc}")
            print("Tente novamente com 'dukielist sync google'.")
            return
        print(
            f"Sincronização inicial concluída: {stats.created_local} evento(s) "
            f"recebido(s) e {stats.created_remote} tarefa(s) enviada(s)."
        )
        return

    if args.command == "sync" and args.provider == "google":
        storage = SQLiteStorage(args.db)
        if args.calendar:
            storage.set_setting(GOOGLE_CALENDAR_SETTING, args.calendar)
        storage.enable_mirror(GOOGLE_SOURCE)
        try:
            stats = sync_google_calendar(
                storage,
                build_google_client(storage),
                start=args.from_date,
                end=args.to_date,
                timezone_name=args.timezone,
                outbound_only=args.push_only,
            )
        except GoogleCalendarError as exc:
            parser.error(str(exc))
        print(f"Google Calendar sincronizado ({google_calendar_id(storage)}).")
        print(
            f"Enviados — criados: {stats.created_remote} | "
            f"atualizados: {stats.updated_remote} | removidos: {stats.deleted_remote}"
        )
        if not args.push_only:
            print(
                f"Recebidos — consultados: {stats.fetched} | "
                f"criados: {stats.created_local} | atualizados: {stats.updated_local} | "
                f"sem mudanças: {stats.unchanged_local} | removidos: {stats.deleted_local}"
            )
        if stats.failed_remote:
            print(f"Pendentes por evento indisponível: {stats.failed_remote}")
        if stats.skipped:
            print(f"Ignorados: {stats.skipped}")
        return

    if args.command == "sync" and args.provider == "trello":
        try:
            stats = sync_trello(
                SQLiteStorage(args.db),
                TrelloClient(load_credentials(args.env_file)),
                boards=args.board,
                start_date=args.from_date,
                include_completed=args.include_completed,
                timezone_name=args.timezone,
            )
        except TrelloError as exc:
            parser.error(str(exc))
        scope = ", ".join(args.board) if args.board else "cartões atribuídos a você"
        print(f"Trello sincronizado ({scope}).")
        print(
            f"Consultados: {stats.fetched} | Elegíveis: {stats.eligible} | "
            f"Criados: {stats.created} | Atualizados: {stats.updated} | "
            f"Sem mudanças: {stats.unchanged}"
        )
        print(f"Conclusões enviadas do DukieList para o Trello: {stats.pushed_completions}")
        if stats.failed_completions:
            print(
                "Conclusões pendentes por cartão indisponível: "
                f"{stats.failed_completions}"
            )
        print(
            f"Ignorados — sem prazo: {stats.skipped_undated} | "
            f"anteriores à data inicial: {stats.skipped_before_start} | "
            f"já concluídos: {stats.skipped_completed}"
        )
        return

    DukieListApp(db_path=args.db).run()
