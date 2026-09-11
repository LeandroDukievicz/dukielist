"""Capturas reais da TUI com banco temporário; nunca altera tarefas do usuário."""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, time, timedelta
from pathlib import Path
import shutil
import subprocess
import tempfile

from dukielist.app import DukieListApp
from dukielist.models import Category, Priority, ViewMode
from dukielist.screens import MainScreen, TaskFormScreen
from textual.widgets import Input


async def capture(output: Path, png: bool) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        app = DukieListApp(db_path=Path(temporary) / "demo.db")
        anchor = date(2026, 9, 10)
        titles = ["Acordar cedo", "Responder e-mails", "Estudar programação",
                  "Ir à academia", "Entregar relatório", "Revisar anotações", "Planejar semana"]
        for offset in range(-9, 21):
            day = anchor + timedelta(days=offset)
            for index in range(3):
                task = app.service.create(
                    title=titles[(offset + index) % len(titles)],
                    description="Organizar os detalhes e reservar tempo para esta tarefa.",
                    task_date=day, task_time=time(7 + index * 3),
                    priority=list(Priority)[index], category=list(Category)[(offset + index) % 5],
                )
                if (offset + index) % 4 == 0:
                    app.service.toggle(task.id)
        async with app.run_test(size=(168, 52)) as pilot:
            async def snap(name: str) -> None:
                await pilot.pause()
                svg = app.export_screenshot()
                (output / f"{name}.svg").write_text(
                    "\n".join(line.rstrip() for line in svg.splitlines()) + "\n", encoding="utf-8")

            await snap("01-inicio")
            await pilot.press("enter")
            main = app.screen
            assert isinstance(main, MainScreen)
            main.anchor_date = main.selected_month_day = anchor
            main._refresh_all()
            await snap("02-dia")
            await pilot.press("w")
            await snap("03-semana")
            await pilot.press("m")
            await snap("04-mes")
            await pilot.press("d", "a")
            assert isinstance(app.screen, TaskFormScreen)
            await snap("05-adicionar")
            await pilot.press("escape", "e")
            assert isinstance(app.screen, TaskFormScreen) and app.screen.edit_task
            app.screen.query_one("#form-title", Input).focus()
            await snap("06-editar")
    if png:
        browser = shutil.which("google-chrome") or shutil.which("chromium")
        if not browser:
            raise SystemExit("Instale Chrome/Chromium para converter SVG em PNG, ou omita --png.")
        for source in output.glob("*.svg"):
            subprocess.run([browser, "--headless", "--no-sandbox", "--disable-gpu",
                            "--hide-scrollbars", "--force-device-scale-factor=1",
                            "--window-size=1680,1050", f"--screenshot={source.with_suffix('.png')}",
                            source.as_uri()], check=True, capture_output=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/screenshots"))
    parser.add_argument("--png", action="store_true")
    args = parser.parse_args()
    asyncio.run(capture(args.output.resolve(), args.png))
