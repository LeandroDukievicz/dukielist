from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from textual.app import App

from .screens import MainScreen, WelcomeScreen
from .services import TaskService
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

    def __init__(self, db_path: str | Path | None = None) -> None:
        super().__init__()
        self.storage = SQLiteStorage(db_path)
        self.service = TaskService(self.storage)
        self._trello_sync_lock = asyncio.Lock()
        self._trello_timer = None

    def on_mount(self) -> None:
        self.push_screen(WelcomeScreen())

    def start_main(self, mode) -> None:
        self.push_screen(MainScreen(mode, self.service))
        if self._trello_timer is None:
            self._trello_timer = self.set_interval(60, self._schedule_trello_pull)
        self.call_after_refresh(self._schedule_trello_pull)

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
    args = parser.parse_args()

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
        print(f"Conclusões enviadas da DukieList para o Trello: {stats.pushed_completions}")
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
