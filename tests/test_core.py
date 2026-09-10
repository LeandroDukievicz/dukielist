import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from dukielist.models import Category, Priority, Status
from dukielist.services import TaskFilters, TaskService, month_bounds, shift_month, week_bounds
from dukielist.storage import SQLiteStorage


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
            task_date=today,
            task_time=None,
            priority=Priority.HIGH,
            category=Category.STUDY,
        )
        self.assertIsNotNone(task.id)
        self.assertEqual(self.service.list_between(today, today + timedelta(days=1))[0].title, "Estudar")

        updated = self.service.toggle(task.id)
        self.assertEqual(updated.status, Status.COMPLETED)
        self.assertEqual(self.service.progress([updated]).percent, 100)

        changed = self.service.update(
            task.id,
            title="Estudar Textual",
            description="Atualizada",
            task_date=today,
            task_time=None,
            priority=Priority.LOW,
            category=Category.PERSONAL,
            status=Status.PENDING,
        )
        self.assertEqual(changed.title, "Estudar Textual")
        self.assertEqual(len(self.service.list_between(today, today + timedelta(days=1), TaskFilters(priority=Priority.LOW))), 1)
        self.assertTrue(self.service.delete(task.id))
        self.assertIsNone(self.service.get(task.id))

    def test_period_helpers(self) -> None:
        anchor = date(2026, 9, 10)
        week_start, week_end = week_bounds(anchor)
        month_start, month_end = month_bounds(anchor)
        self.assertEqual(week_start, date(2026, 9, 7))
        self.assertEqual(week_end, date(2026, 9, 14))
        self.assertEqual((month_start, month_end), (date(2026, 9, 1), date(2026, 10, 1)))
        self.assertEqual(shift_month(anchor, 1), date(2026, 10, 10))


if __name__ == "__main__":
    unittest.main()
