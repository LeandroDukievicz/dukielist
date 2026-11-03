import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from dukielist.models import Category, Priority, Status, Task, ViewMode
from dukielist.services import TaskFilters, TaskService, month_bounds, shift_month, week_bounds
from dukielist.storage import SQLiteStorage
from dukielist.widgets import AppTitle, DigitalClock, WeekBoard, status_render


class TaskServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteStorage(Path(self.temp_dir.name) / "tasks.db")
        self.service = TaskService(self.storage)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_crud_and_toggle(self) -> None:
        today = date.today()
        task = self.service.create(
            title="Estudar",
            description="Textual",
            start_date=today,
            end_date=today,
            start_time=None,
            end_time=None,
            all_day=True,
            priority=Priority.HIGH,
            category=Category.STUDY,
            view_mode=ViewMode.WEEK,
        )
        self.assertIsNotNone(task.id)
        self.assertEqual(self.service.get(task.id).view_mode, ViewMode.WEEK)
        self.assertEqual(self.service.list_between(today, today + timedelta(days=1))[0].title, "Estudar")

        updated = self.service.toggle(task.id)
        self.assertEqual(updated.status, Status.COMPLETED)
        self.assertEqual(self.service.progress([updated]).percent, 100)

        changed = self.service.update(
            task.id,
            title="Estudar Textual",
            description="Atualizada",
            start_date=today,
            end_date=today,
            start_time=None,
            end_time=None,
            all_day=True,
            priority=Priority.LOW,
            category=Category.PERSONAL,
            status=Status.PENDING,
            view_mode=ViewMode.MONTH,
        )
        self.assertEqual(changed.title, "Estudar Textual")
        self.assertEqual(changed.view_mode, ViewMode.MONTH)
        self.assertEqual(len(self.service.list_between(today, today + timedelta(days=1), TaskFilters(priority=Priority.LOW))), 1)
        self.assertTrue(self.service.delete(task.id))
        self.assertIsNone(self.service.get(task.id))

    def _all_day(self, *, start: date, end: date | None = None) -> Task:
        return self.service.create(
            title="Entregar relatório",
            description="",
            start_date=start,
            end_date=end or start,
            start_time=None,
            end_time=None,
            all_day=True,
            priority=Priority.MEDIUM,
            category=Category.WORK,
        )

    def test_pending_task_becomes_overdue_once_its_last_day_passes(self) -> None:
        today = date.today()
        yesterday = today - timedelta(days=1)

        due_today = self._all_day(start=today)
        self.assertFalse(due_today.is_overdue(today))
        self.assertEqual(due_today.display_state(today), "pending")

        overdue = self._all_day(start=yesterday)
        self.assertTrue(overdue.is_overdue(today))
        self.assertEqual(overdue.display_state(today), "overdue")
        self.assertEqual(status_render(overdue).plain, "[!]")

        # Um intervalo que ainda alcança hoje continua apenas pendente.
        running = self._all_day(start=yesterday, end=today)
        self.assertFalse(running.is_overdue(today))

    def test_overdue_task_is_resolved_as_completed_or_missed(self) -> None:
        yesterday = date.today() - timedelta(days=1)
        task = self._all_day(start=yesterday)

        missed = self.service.set_status(task.id, Status.MISSED)
        self.assertIs(missed.status, Status.MISSED)
        self.assertFalse(missed.completed)
        self.assertTrue(missed.missed)
        # Já resolvida: deixa de pedir atenção como atraso.
        self.assertFalse(missed.is_overdue())
        self.assertEqual(missed.display_state(), "missed")
        self.assertEqual(status_render(missed).plain, "[✗]")
        self.assertIs(self.service.get(task.id).status, Status.MISSED)

        # Marcar depois que concluiu sobrepõe o registro anterior.
        self.assertIs(self.service.toggle(task.id).status, Status.COMPLETED)
        reopened = self.service.set_status(task.id, Status.PENDING)
        self.assertTrue(reopened.is_overdue())

    def test_missed_tasks_are_filterable_and_counted_apart(self) -> None:
        today = date.today()
        yesterday = today - timedelta(days=1)
        done = self._all_day(start=yesterday)
        self.service.toggle(done.id)
        missed = self._all_day(start=yesterday)
        self.service.set_status(missed.id, Status.MISSED)
        self._all_day(start=yesterday)
        self._all_day(start=today)

        period = (yesterday, today + timedelta(days=1))
        only_missed = self.service.list_between(*period, TaskFilters(status=Status.MISSED))
        self.assertEqual([task.id for task in only_missed], [missed.id])

        summary = self.service.breakdown(self.service.list_between(*period), today)
        self.assertEqual(
            (summary.total, summary.completed, summary.pending, summary.overdue, summary.missed),
            (4, 1, 1, 1, 1),
        )
        self.assertEqual(summary.percent(1), 25)
        # O progresso continua medindo apenas o que foi concluído.
        self.assertEqual(self.service.progress(self.service.list_between(*period)).percent, 25)

    def test_each_day_of_a_task_carries_its_own_status(self) -> None:
        monday = date.today() - timedelta(days=date.today().weekday())
        friday = monday + timedelta(days=4)
        task = self._all_day(start=monday, end=friday)

        self.service.set_day_status(task.id, monday, Status.MISSED)
        self.service.set_day_status(task.id, monday + timedelta(days=1), Status.COMPLETED)
        stored = self.service.get(task.id)

        self.assertIs(stored.status_on(monday), Status.MISSED)
        self.assertIs(stored.status_on(monday + timedelta(days=1)), Status.COMPLETED)
        self.assertEqual(stored.display_state(monday, monday), "missed")
        self.assertEqual(status_render(stored, monday).plain, "[✗]")
        self.assertEqual(status_render(stored, monday + timedelta(days=1)).plain, "[✓]")
        # Os dias restantes seguem o status da tarefa.
        for day in (monday + timedelta(days=2), friday):
            self.assertIs(stored.status_on(day), Status.PENDING)
        # E a tarefa inteira continua em aberto: há dias por decidir.
        self.assertIs(stored.status, Status.PENDING)

    def test_a_day_left_undecided_in_the_past_shows_as_overdue(self) -> None:
        monday = date.today() - timedelta(days=date.today().weekday())
        friday = monday + timedelta(days=4)
        wednesday = monday + timedelta(days=2)
        task = self._all_day(start=monday, end=friday)

        # Quarta é "hoje": segunda e terça passaram sem decisão.
        self.assertEqual(task.display_state(wednesday, monday), "overdue")
        self.assertEqual(task.display_state(wednesday, wednesday), "pending")
        self.assertEqual(task.display_state(wednesday, friday), "pending")
        # A tarefa inteira, porém, ainda não está atrasada: a sexta não chegou.
        self.assertFalse(task.is_overdue(wednesday))

        resolved = self.service.set_day_status(task.id, monday, Status.MISSED)
        self.assertEqual(resolved.display_state(wednesday, monday), "missed")

    def test_completing_every_day_completes_the_whole_task(self) -> None:
        monday = date.today() - timedelta(days=date.today().weekday())
        wednesday = monday + timedelta(days=2)
        task = self._all_day(start=monday, end=wednesday)

        for day in (monday, monday + timedelta(days=1)):
            self.assertIs(self.service.set_day_status(task.id, day, Status.COMPLETED).status,
                          Status.PENDING)

        done = self.service.set_day_status(task.id, wednesday, Status.COMPLETED)
        self.assertIs(done.status, Status.COMPLETED)
        self.assertIs(self.service.get(task.id).status, Status.COMPLETED)

        # Um dia que volta a ficar em aberto reabre a tarefa.
        self.assertIs(self.service.set_day_status(task.id, monday, Status.PENDING).status,
                      Status.PENDING)
        # Todos decididos, um deles sem conclusão: a tarefa não foi concluída.
        self.assertIs(self.service.set_day_status(task.id, monday, Status.MISSED).status,
                      Status.MISSED)

    def test_a_status_for_the_whole_task_discards_the_day_marks(self) -> None:
        monday = date.today() - timedelta(days=date.today().weekday())
        friday = monday + timedelta(days=4)
        task = self._all_day(start=monday, end=friday)
        self.service.set_day_status(task.id, monday, Status.MISSED)

        whole = self.service.set_status(task.id, Status.COMPLETED)
        self.assertEqual(whole.day_status, {})
        stored = self.service.get(task.id)
        self.assertEqual(stored.day_status, {})
        self.assertIs(stored.status_on(monday), Status.COMPLETED)

        # Trocar o status pelo formulário tem o mesmo efeito.
        self.service.set_day_status(task.id, monday, Status.MISSED)
        self.assertIs(self.service.get(task.id).status, Status.PENDING)
        edited = self._edit(task.id, monday, friday, Status.COMPLETED)
        self.assertEqual(edited.day_status, {})
        self.assertEqual(self.service.get(task.id).day_status, {})

    def test_an_edit_that_keeps_the_status_keeps_the_day_marks(self) -> None:
        """Renomear ou remarcar a tarefa não apaga o diário dos dias."""
        monday = date.today() - timedelta(days=date.today().weekday())
        friday = monday + timedelta(days=4)
        task = self._all_day(start=monday, end=friday)
        self.service.set_day_status(task.id, monday, Status.MISSED)

        kept = self._edit(task.id, monday, friday, Status.PENDING, title="Outro título")
        self.assertIs(kept.status_on(monday), Status.MISSED)
        self.assertIs(self.service.get(task.id).status_on(monday), Status.MISSED)

    def _edit(
        self,
        task_id: int,
        start: date,
        end: date,
        status: Status,
        *,
        title: str = "Entregar relatório",
    ) -> Task:
        return self.service.update(
            task_id,
            title=title,
            description="",
            start_date=start,
            end_date=end,
            start_time=None,
            end_time=None,
            all_day=True,
            priority=Priority.MEDIUM,
            category=Category.WORK,
            status=status,
        )

    def test_toggle_day_only_touches_the_selected_day(self) -> None:
        monday = date.today() - timedelta(days=date.today().weekday())
        friday = monday + timedelta(days=4)
        task = self._all_day(start=monday, end=friday)

        toggled = self.service.toggle_day(task.id, friday)
        self.assertIs(toggled.status_on(friday), Status.COMPLETED)
        self.assertIs(toggled.status_on(monday), Status.PENDING)
        self.assertIs(self.service.toggle_day(task.id, friday).status_on(friday), Status.PENDING)

    def test_day_marks_outside_the_new_range_are_discarded(self) -> None:
        monday = date.today() - timedelta(days=date.today().weekday())
        friday = monday + timedelta(days=4)
        task = self._all_day(start=monday, end=friday)
        self.service.set_day_status(task.id, friday, Status.MISSED)
        self.service.set_day_status(task.id, monday, Status.COMPLETED)

        shortened = self._edit(task.id, monday, monday + timedelta(days=1), Status.PENDING)
        remaining = self.service.get(shortened.id).day_status
        self.assertNotIn(friday, remaining)
        self.assertIs(remaining[monday], Status.COMPLETED)

    def test_a_day_outside_the_task_is_refused(self) -> None:
        monday = date.today() - timedelta(days=date.today().weekday())
        task = self._all_day(start=monday, end=monday + timedelta(days=1))
        with self.assertRaises(ValueError):
            self.service.set_day_status(task.id, monday + timedelta(days=5), Status.MISSED)

    def test_progress_and_breakdown_can_follow_a_single_day(self) -> None:
        monday = date.today() - timedelta(days=date.today().weekday())
        friday = monday + timedelta(days=4)
        task = self._all_day(start=monday, end=friday)
        self.service.set_day_status(task.id, monday, Status.MISSED)
        self.service.set_day_status(task.id, friday, Status.COMPLETED)
        tasks = [self.service.get(task.id)]

        self.assertEqual(self.service.progress(tasks, friday).percent, 100)
        self.assertEqual(self.service.progress(tasks, monday).percent, 0)
        # Sem dia, vale o status da tarefa, que ainda tem dias por decidir.
        self.assertEqual(self.service.progress(tasks).percent, 0)
        self.assertEqual(self.service.breakdown(tasks, monday, monday).missed, 1)

    def test_period_helpers(self) -> None:
        anchor = date(2026, 9, 10)
        week_start, week_end = week_bounds(anchor)
        month_start, month_end = month_bounds(anchor)
        self.assertEqual(week_start, date(2026, 9, 7))
        self.assertEqual(week_end, date(2026, 9, 14))
        self.assertEqual((month_start, month_end), (date(2026, 9, 1), date(2026, 10, 1)))
        self.assertEqual(shift_month(anchor, 1), date(2026, 10, 10))

    def test_digital_clock_contains_time_and_weekday(self) -> None:
        from rich.console import Console
        from io import StringIO

        output = StringIO()
        Console(file=output, width=40, color_system=None).print(
            DigitalClock().render_time(datetime(2026, 9, 10, 23, 59, 58), compact=True))
        self.assertIn("23:59:58", output.getvalue())
        self.assertIn("Quinta-feira", output.getvalue())

    def test_compact_title_and_week_scrollbar(self) -> None:
        title = AppTitle().render()
        self.assertEqual(title.plain, "DukieList")
        self.assertNotIn("usuario@", title.plain)
        self.assertGreater(len({str(span.style) for span in title.spans}), 1)

        top = WeekBoard._scrollbar(total=9, capacity=3, offset=0, height=8).plain
        bottom = WeekBoard._scrollbar(total=9, capacity=3, offset=6, height=8).plain
        self.assertTrue(top.startswith("█"))
        self.assertTrue(bottom.endswith("█"))
        self.assertIn("│", top)


if __name__ == "__main__":
    unittest.main()
