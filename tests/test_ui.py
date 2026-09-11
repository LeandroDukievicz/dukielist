import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from textual.widgets import Button, Input, Select
from dukielist.app import DukieListApp
from dukielist.models import Category, Priority, ViewMode
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
                        controls = [main.query_one("#" + name) for name in
                                    ("previous-period", "next-period", "add-task", "edit-task")]
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
