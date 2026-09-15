import calendar
import tempfile
import unittest
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rich.console import Console
from textual.widgets import Button, Input, Select
from dukielist.app import DukieListApp
from dukielist.models import Category, Priority, ViewMode
from dukielist.gmail import GmailClient, GmailMessage
from dukielist.gmail_screens import GmailInboxScreen, GmailMessageScreen
from dukielist.screens import MainScreen, TaskFormScreen, FilterScreen, HelpScreen, ConfirmDeleteScreen
from dukielist.widgets import CalendarGrid, WeekBoard


class InterfaceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = DukieListApp(db_path=Path(self.temp.name) / "tasks.db")
        for index in range(9):
            self.app.service.create(
                title=f"Tarefa {index}", description="Teste", task_date=date.today(),
                task_time=None, priority=Priority.LOW, category=Category.PERSONAL)

    def tearDown(self):
        self.temp.cleanup()

    async def test_keyboard_crud_and_selection(self):
        async with self.app.run_test(size=(168, 52)) as pilot:
            await pilot.press("enter", "down", "e")
            self.assertIsInstance(self.app.screen, TaskFormScreen)
            self.app.screen.query_one("#form-title", Input).value = "Título editado"
            await pilot.press("ctrl+s")
            await pilot.pause()
            main = self.app.screen
            self.assertEqual(main._current_task().title, "Título editado")
            task_id = main._current_task().id
            await pilot.press("c")
            self.assertTrue(self.app.service.get(task_id).completed)
            await pilot.press("e")
            self.assertEqual(self.app.screen.edit_task.id, task_id)
            await pilot.press("escape", "x")
            self.assertIsInstance(self.app.screen, ConfirmDeleteScreen)
            self.app.screen.query_one("#confirm-delete", Button).focus()
            await pilot.press("enter")
            self.assertIsNone(self.app.service.get(task_id))
            await pilot.press("a")
            self.app.screen.query_one("#form-title", Input).value = "Nova tarefa"
            await pilot.press("ctrl+s")
            await pilot.pause()
            self.assertEqual(main._current_task().title, "Nova tarefa")

    async def test_navigation_at_supported_sizes(self):
        async with self.app.run_test(size=(168, 52)) as pilot:
            await pilot.press("enter")
            main = self.app.screen
            for size in ((168, 52), (140, 44), (100, 36), (80, 30)):
                with self.subTest(size=size):
                    await pilot.resize_terminal(*size)
                    for mode in ("day", "week", "month"):
                        main.action_set_mode(mode)
                        await pilot.pause()
                        tabs = [main.query_one("#" + name) for name in
                                ("mode-day", "mode-week", "mode-month", "go-today")]
                        for tab in tabs:
                            self.assertTrue(tab.parent.region.contains_region(tab.region),
                                            (size, mode, tab.id, tab.region, tab.parent.region))
                        for index, tab in enumerate(tabs):
                            for other in tabs[index + 1:]:
                                self.assertFalse(tab.region.overlaps(other.region),
                                                 (size, mode, tab.id, other.id))
                        controls = [main.query_one("#" + name) for name in
                                    ("previous-period", "next-period", "add-task", "edit-task", "gmail-inbox")]
                        for control in controls:
                            self.assertTrue(control.parent.region.contains_region(control.region),
                                            (size, mode, control.id, control.region, control.parent.region))
                        for index, control in enumerate(controls):
                            for other in controls[index + 1:]:
                                self.assertFalse(control.region.overlaps(other.region))
                        self.assertGreaterEqual(main.query_one("#views").size.height, 8)
                        await pilot.press("right", "down")
                        await pilot.press("a")
                        self.assertIsInstance(self.app.screen, TaskFormScreen)
                        await pilot.press("tab", "tab", "tab", "tab", "enter", "down", "enter")
                        await pilot.press("escape")
                        if isinstance(self.app.screen, TaskFormScreen):
                            await pilot.press("escape")
                        self.assertIsInstance(self.app.screen, MainScreen)
                    for key, screen_type in (("f", FilterScreen), ("h", HelpScreen)):
                        await pilot.press(key)
                        self.assertIsInstance(self.app.screen, screen_type)
                        await pilot.press("tab", "shift+tab", "escape")
                    with patch.object(GmailInboxScreen, "action_refresh"):
                        await pilot.press("g")
                        self.assertIsInstance(self.app.screen, GmailInboxScreen)
                        await pilot.press("escape")
                    main.action_set_mode("day")
                    main.anchor_date = date.today()
                    main._refresh_all()
                    main._focus_active_view()
                    await pilot.pause()
                    await pilot.press("e")
                    self.assertIsInstance(self.app.screen, TaskFormScreen)
                    for button in self.app.screen.query(".form-actions Button"):
                        self.assertGreaterEqual(button.region.x, button.parent.region.x)
                        self.assertLessEqual(button.region.right, button.parent.region.right)
                        button.focus()
                        await pilot.pause()
                        await pilot.wait_for_scheduled_animations()
                        self.assertTrue(self.app.screen.region.contains_region(button.region),
                                        (size, button.id, button.region))
                    await pilot.press("escape")
                    main.query_one("#mode-day").focus()
                    await pilot.press("right", "enter")
                    self.assertEqual(main.mode, ViewMode.WEEK)

    async def test_week_cursor_and_month_enter(self):
        async with self.app.run_test(size=(168, 52)) as pilot:
            await pilot.press("enter", "w")
            board = self.app.screen.query_one(WeekBoard)
            await pilot.press(*(["down"] * 7), "e")
            self.assertEqual(self.app.screen.edit_task.id, board._current_task_id())
            await pilot.press("escape", "right", "a")
            expected = board.start + timedelta(days=board.day_index)
            self.assertEqual(self.app.screen.default_date, expected)
            await pilot.press("escape", "m", "down")
            selected = self.app.screen.query_one(CalendarGrid).selected_date
            await pilot.press("enter")
            self.assertEqual(self.app.screen.mode, ViewMode.DAY)
            self.assertEqual(self.app.screen.anchor_date, selected)

    async def test_go_today_button_and_keyboard_shortcut_in_every_mode(self):
        async with self.app.run_test(size=(168, 52)) as pilot:
            await pilot.press("enter")
            main = self.app.screen
            today = date.today()

            for mode in ViewMode:
                with self.subTest(mode=mode):
                    main.action_set_mode(mode.value)
                    main.anchor_date = date(2025, 1, 15)
                    main.selected_month_day = date(2025, 1, 15)
                    await pilot.press("t")
                    self.assertEqual(main.anchor_date, today)
                    self.assertEqual(main.selected_month_day, today)
                    if mode is ViewMode.WEEK:
                        self.assertEqual(main.query_one(WeekBoard).day_index, today.weekday())

            main.anchor_date = date(2024, 6, 10)
            main.selected_month_day = date(2024, 6, 10)
            main.query_one("#go-today", Button).focus()
            await pilot.press("enter")
            self.assertEqual(main.anchor_date, today)
            self.assertEqual(main.selected_month_day, today)

    async def test_gmail_inbox_and_message_reader(self):
        message = GmailMessage(
            id="message-1",
            thread_id="thread-1",
            sender="Equipe Dukie",
            sender_address="dukielist@example.com",
            subject="Mensagem de teste",
            received_at=datetime.now().astimezone(),
            snippet="Prévia da mensagem",
            unread=True,
            recipient="leandro@example.com",
            body="Conteúdo completo e seguro.",
        )
        with (
            patch.object(GmailClient, "list_inbox", return_value=[message]),
            patch.object(GmailClient, "get_message", return_value=message),
        ):
            async with self.app.run_test(size=(80, 30)) as pilot:
                await pilot.press("enter", "g")
                await pilot.pause()
                self.assertIsInstance(self.app.screen, GmailInboxScreen)
                table = self.app.screen.query_one("#gmail-table")
                self.assertEqual(table.row_count, 1)
                self.assertTrue(self.app.screen.region.contains_region(table.region))

                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(self.app.screen, GmailMessageScreen)
                self.assertIn("Conteúdo completo", self.app.screen.message.body)
                await pilot.press("escape", "escape")
                self.assertIsInstance(self.app.screen, MainScreen)

    async def test_six_week_month_fits_supported_heights(self):
        async with self.app.run_test(size=(168, 52)) as pilot:
            await pilot.press("enter", "m")
            main = self.app.screen
            main.anchor_date = main.selected_month_day = date(2026, 5, 1)
            main._refresh_all()

            for size in ((168, 52), (140, 44), (100, 36), (80, 30)):
                with self.subTest(size=size):
                    await pilot.resize_terminal(*size)
                    await pilot.pause()
                    grid = main.query_one(CalendarGrid)
                    viewport = main.query_one("#views")
                    output = StringIO()
                    Console(file=output, width=grid.size.width, color_system=None).print(grid.render())
                    self.assertEqual(len(grid.render().rows), 6)
                    self.assertEqual(len(output.getvalue().splitlines()), grid.size.height)
                    self.assertEqual(viewport.max_scroll_y, 0)
                    self.assertEqual(
                        len(calendar.Calendar(firstweekday=6).monthdatescalendar(grid.year, grid.month)),
                        len(grid.render().rows),
                    )
