from __future__ import annotations

import argparse
from pathlib import Path

from textual.app import App

from .screens import MainScreen, WelcomeScreen
from .services import TaskService
from .storage import SQLiteStorage


class DukieListApp(App[None]):
    TITLE = "DukieList"
    SUB_TITLE = "Terminal task control"
    CSS_PATH = "theme.tcss"

    def __init__(self, db_path: str | Path | None = None) -> None:
        super().__init__()
        self.storage = SQLiteStorage(db_path)
        self.service = TaskService(self.storage)

    def on_mount(self) -> None:
        self.push_screen(WelcomeScreen())

    def start_main(self, mode) -> None:
        self.push_screen(MainScreen(mode, self.service))


def main() -> None:
    parser = argparse.ArgumentParser(description="DukieList — gerenciador de tarefas no terminal")
    parser.add_argument("--db", type=Path, help="caminho alternativo para o banco SQLite")
    args = parser.parse_args()
    DukieListApp(db_path=args.db).run()
