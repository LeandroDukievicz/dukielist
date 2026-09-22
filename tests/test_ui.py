import calendar
import tempfile
import unittest
from datetime import date, time, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rich.console import Console
from textual.widgets import Button, Input, Select
from dukielist.app import DukieListApp
from dukielist.models import Category, Priority, ViewMode
from dukielist.pickers import DatePickerScreen, shift_selected_month
from dukielist.screens import MainScreen, TaskFormScreen, FilterScreen, HelpScreen, ConfirmDeleteScreen
from dukielist.widgets import CalendarGrid, WeekBoard
from dukielist.weather import ForecastDay, WeatherForecast, WeatherError, WeatherConnectionError
from dukielist.weather_screens import WeatherScreen


class InterfaceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.auto_forecast = WeatherForecast(
            "Maringá, Paraná",
            tuple(
                ForecastDay(date.today() + timedelta(days=index),
                            17 + index, 27 + index, float(index), 40 + index * 10, 61)
                for index in range(4)
            ),
            automatic=True,
        )
        self.location_patch = patch(
            "dukielist.screens.fetch_local_forecast", return_value=self.auto_forecast
        )
        self.location_patch.start()
        self.addCleanup(self.location_patch.stop)
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
                    weather_button = main.query_one("#weather-open", Button)
                    self.assertTrue(main.query_one("#app-footer").region.contains_region(
                        weather_button.region
                    ), size)
                    if size[0] >= 160:
                        strip = main.query_one("#weather-strip")
                        self.assertTrue(main.query_one("#view-tabs").region.contains_region(
                            strip.region
                        ), size)
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

    async def test_weather_screen_displays_four_days_and_persists_city(self):
        forecast = WeatherForecast(
            "Curitiba, Paraná",
            tuple(
                ForecastDay(date(2026, 9, 21) + timedelta(days=index),
                            12, 22, float(index), 50, 61)
                for index in range(4)
            ),
        )
        with patch("dukielist.weather_screens.fetch_forecast", return_value=forecast):
            async with self.app.run_test(size=(80, 30)) as pilot:
                await pilot.press("enter", "r")
                self.assertIsInstance(self.app.screen, WeatherScreen)
                field = self.app.screen.query_one("#weather-city", Input)
                field.value = "Curitiba, Paraná"
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(self.app.storage.get_setting("weather_city"), "Curitiba, Paraná")
                self.assertIn("3,0 mm", self.app.screen.query_one("#weather-results").render().plain)
                self.assertTrue(self.app.screen.region.contains_region(
                    self.app.screen.query_one("#weather-card").region
                ))
                await pilot.press("escape")
                self.assertIsInstance(self.app.screen, MainScreen)
                self.assertEqual(self.app.screen.weather_forecast.location, "Curitiba, Paraná")

    async def test_header_forecast_arrows_show_today_and_next_three_days(self):
        async with self.app.run_test(size=(168, 52)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            main = self.app.screen
            self.assertEqual(main.weather_forecast.location, "Maringá, Paraná")
            self.assertIn("Hoje", main.query_one("#weather-summary").render().plain)
            self.assertTrue(main.query_one("#weather-prev", Button).disabled)
            for index in range(1, 4):
                self.assertFalse(main.query_one("#weather-next", Button).disabled)
                await pilot.click("#weather-next")
                await pilot.pause(0.1)
                self.assertEqual(main.weather_day_index, index)
                self.assertIn(f"{index},0 mm", main.query_one("#weather-summary").render().plain)
            self.assertTrue(main.query_one("#weather-next", Button).disabled)
            await pilot.click("#weather-prev")
            await pilot.pause(0.1)
            self.assertEqual(main.weather_day_index, 2)

    async def test_header_forecast_fits_at_visibility_boundary(self):
        async with self.app.run_test(size=(160, 44)) as pilot:
            await pilot.press("enter")
            main = self.app.screen
            await pilot.pause()
            strip = main.query_one("#weather-strip")
            tabs = main.query_one("#view-tabs")
            self.assertTrue(tabs.region.contains_region(strip.region))
            self.assertFalse(strip.region.overlaps(main.query_one("#go-today").region))

    async def test_manual_city_can_return_to_current_location(self):
        manual_forecast = WeatherForecast("Curitiba, Paraná", self.auto_forecast.days)
        with (
            patch("dukielist.weather_screens.fetch_forecast", return_value=manual_forecast),
            patch("dukielist.weather_screens.fetch_local_forecast",
                  return_value=self.auto_forecast),
        ):
            async with self.app.run_test(size=(168, 52)) as pilot:
                await pilot.press("enter")
                main = self.app.screen
                await pilot.press("r")
                self.app.screen.query_one("#weather-city", Input).value = "Curitiba, Paraná"
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(main.weather_forecast.location, "Curitiba, Paraná")
                self.assertTrue(main._weather_manual_override)
                await pilot.click("#weather-local")
                await pilot.pause()
                self.assertEqual(main.weather_forecast.location, "Maringá, Paraná")
                self.assertFalse(main._weather_manual_override)

    async def test_connection_error_warns_without_blocking_tasks(self):
        with (
            patch("dukielist.screens.fetch_local_forecast",
                  side_effect=WeatherConnectionError("Sem internet")),
            patch.object(self.app, "notify") as notify,
        ):
            async with self.app.run_test(size=(168, 52)) as pilot:
                await pilot.press("enter")
                await pilot.pause()
                main = self.app.screen
                self.assertIn("Previsão indisponível", main.query_one("#weather-summary").render().plain)
                self.assertTrue(main.query_one("#weather-next", Button).disabled)
                self.assertTrue(any(
                    call.kwargs.get("title") == "Previsão do tempo" for call in notify.call_args_list
                ))
                await pilot.press("a")
                self.assertIsInstance(self.app.screen, TaskFormScreen)

    async def test_weather_error_does_not_save_city(self):
        with patch("dukielist.weather_screens.fetch_forecast", side_effect=WeatherError("Sem conexão")):
            async with self.app.run_test(size=(80, 30)) as pilot:
                await pilot.press("enter", "r")
                self.app.screen.query_one("#weather-city", Input).value = "Curitiba"
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsNone(self.app.storage.get_setting("weather_city"))
                self.assertIn("Sem conexão", self.app.screen.query_one("#weather-status").render().plain)

    async def test_keyboard_date_picker_and_hour_minute_selects(self):
        async with self.app.run_test(size=(80, 30)) as pilot:
            await pilot.press("enter", "a")
            form = self.app.screen
            self.assertIsInstance(form, TaskFormScreen)
            form.query_one("#form-title", Input).value = "Consulta"

            start = form.selected_date
            form.query_one("#form-date", Button).focus()
            await pilot.press("enter")
            self.assertIsInstance(self.app.screen, DatePickerScreen)
            self.assertTrue(
                self.app.screen.region.contains_region(
                    self.app.screen.query_one("#date-picker-calendar").region
                )
            )
            await pilot.press("right", "down", "enter")
            self.assertIs(self.app.screen, form)
            self.assertEqual(form.selected_date, start + timedelta(days=8))

            hour = form.query_one("#form-hour", Select)
            hour.focus()
            await pilot.press("enter", "1", "4", "enter")
            minute = form.query_one("#form-minute", Select)
            minute.focus()
            await pilot.press("enter", "3", "5", "enter")
            self.assertEqual(hour.value, 14)
            self.assertEqual(minute.value, 35)

            form.query_one("#form-save", Button).focus()
            await pilot.press("enter")
            tasks = self.app.service.list_between(
                start + timedelta(days=8), start + timedelta(days=9)
            )
            saved = next(task for task in tasks if task.title == "Consulta")
            self.assertEqual(saved.task_time, time(14, 35))

            await pilot.press("e")
            edit_form = self.app.screen
            self.assertEqual(edit_form.query_one("#form-hour", Select).value, 14)
            self.assertEqual(edit_form.query_one("#form-minute", Select).value, 35)
            edit_form.query_one("#form-hour", Select).value = Select.NULL
            edit_form.query_one("#form-save", Button).focus()
            await pilot.press("enter")
            self.assertIsNone(self.app.service.get(saved.id).task_time)

    def test_date_picker_month_shift_clamps_invalid_days(self):
        self.assertEqual(shift_selected_month(date(2024, 1, 31), 1), date(2024, 2, 29))
        self.assertEqual(shift_selected_month(date(2025, 12, 31), 1), date(2026, 1, 31))

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
