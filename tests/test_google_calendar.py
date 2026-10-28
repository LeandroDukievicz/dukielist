import json
import tempfile
import unittest
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

from dukielist.google_calendar import (
    COMPLETED_PREFIX,
    DEFAULT_TIMEZONE,
    SOURCE,
    GoogleAuthRequiredError,
    GoogleCalendarClient,
    GoogleCalendarError,
    GoogleEventUnavailableError,
    credentials_file,
    event_to_task,
    push_pending,
    sync_google_calendar,
    task_to_event,
)
from dukielist.models import Category, Priority, Status, ViewMode
from dukielist.services import TaskService
from dukielist.storage import SQLiteStorage


TIMEZONE = ZoneInfo(DEFAULT_TIMEZONE)


class OAuthConfigurationTests(unittest.TestCase):
    def test_packaged_client_is_used_when_user_has_no_override(self):
        with tempfile.TemporaryDirectory() as directory:
            packaged = Path(directory) / "google_oauth_client.json"
            packaged.write_text("{}")
            with patch("dukielist.google_calendar.DEFAULT_CREDENTIALS_FILE", Path(directory) / "missing.json"):
                with patch("dukielist.google_calendar.BUNDLED_CREDENTIALS_FILE", packaged):
                    with patch.dict("os.environ", {"DUKIELIST_GOOGLE_CREDENTIALS": ""}):
                        self.assertEqual(credentials_file(), packaged)


def make_event(**changes):
    event = {
        "id": "event-1",
        "status": "confirmed",
        "summary": "Consulta",
        "description": "Levar os exames",
        "updated": "2026-09-20T12:00:00.000Z",
        "htmlLink": "https://calendar.google.com/event?eid=event-1",
        "start": {"dateTime": "2026-09-20T15:30:00-03:00"},
        "end": {"dateTime": "2026-09-20T16:30:00-03:00"},
    }
    event.update(changes)
    return event


class FakeCalendarClient:
    """Agenda em memória com o mesmo contrato do cliente real."""

    def __init__(self, events=None, *, missing=(), forbidden=()):
        self.events = {event["id"]: dict(event) for event in (events or [])}
        self.missing = set(missing)
        # Eventos que o Google recusa alterar, como os organizados por outra conta.
        self.forbidden = set(forbidden)
        self.inserted = []
        self.patched = []
        self.deleted = []
        self.calendar_id = "primary"
        self._next_id = 100
        self._revision = 0

    def _stamp(self):
        self._revision += 1
        return f"2026-09-21T10:00:0{self._revision}.000Z"

    def list_events(self, start, end, timezone):
        return [dict(event) for event in self.events.values()]

    def insert_event(self, body):
        self._next_id += 1
        event_id = f"event-{self._next_id}"
        event = {**body, "id": event_id, "updated": self._stamp(), "status": "confirmed"}
        event["htmlLink"] = f"https://calendar.google.com/event?eid={event_id}"
        self.events[event_id] = event
        self.inserted.append(dict(body))
        return dict(event)

    def patch_event(self, event_id, body):
        if event_id in self.forbidden:
            raise GoogleCalendarError("o Google recusou alterar este evento")
        if event_id in self.missing or event_id not in self.events:
            raise GoogleEventUnavailableError("evento ausente")
        self.patched.append((event_id, dict(body)))
        event = {**self.events[event_id], **body, "updated": self._stamp()}
        self.events[event_id] = event
        return dict(event)

    def delete_event(self, event_id):
        if event_id in self.missing or event_id not in self.events:
            raise GoogleEventUnavailableError("evento ausente")
        self.deleted.append(event_id)
        del self.events[event_id]


class GoogleCalendarTestCase(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.storage = SQLiteStorage(Path(self._directory.name) / "test.db")
        self.storage.enable_mirror(SOURCE)
        self.service = TaskService(self.storage)

    def add_task(self, **changes):
        values = {
            "title": "Consulta",
            "description": "Levar os exames",
            "start_date": date(2026, 9, 20),
            "end_date": date(2026, 9, 20),
            "start_time": time(15, 30),
            "end_time": time(16, 30),
            "all_day": False,
            "priority": Priority.HIGH,
            "category": Category.HEALTH,
        }
        values.update(changes)
        return self.service.create(**values)


class TaskToEventTests(GoogleCalendarTestCase):
    def test_timed_task_becomes_an_hour_long_event(self):
        task = self.add_task()
        body = task_to_event(task)

        self.assertEqual(body["summary"], "Consulta")
        self.assertEqual(body["start"], {
            "dateTime": "2026-09-20T15:30:00",
            "timeZone": DEFAULT_TIMEZONE,
        })
        self.assertEqual(body["end"]["dateTime"], "2026-09-20T16:30:00")
        self.assertEqual(
            body["extendedProperties"]["private"],
            {
                "dukielistPriority": "alta",
                "dukielistCategory": "saúde",
                "dukielistStatus": "pendente",
                "dukielistViewMode": "day",
            },
        )

    def test_task_without_time_becomes_all_day_event(self):
        task = self.add_task(all_day=True, start_time=None, end_time=None)
        body = task_to_event(task)

        self.assertEqual(body["start"], {"date": "2026-09-20"})
        self.assertEqual(body["end"], {"date": "2026-09-21"})

    def test_completed_task_is_marked_in_the_summary(self):
        task = self.add_task(status=Status.COMPLETED)
        body = task_to_event(task)

        self.assertEqual(body["summary"], f"{COMPLETED_PREFIX}Consulta")
        self.assertEqual(body["extendedProperties"]["private"]["dukielistStatus"], "concluída")


class EventToTaskTests(unittest.TestCase):
    def test_timed_event_uses_the_local_timezone(self):
        task = event_to_task(make_event(), TIMEZONE)

        self.assertEqual(task.title, "Consulta")
        self.assertEqual(task.start_date, date(2026, 9, 20))
        self.assertEqual(task.start_time, time(15, 30))
        self.assertEqual(task.end_time, time(16, 30))

    def test_utc_event_is_converted(self):
        event = make_event(start={"dateTime": "2026-09-20T18:30:00Z"})
        task = event_to_task(event, TIMEZONE)

        self.assertEqual(task.start_time, time(15, 30))
        self.assertEqual(task.end_time, time(16, 30))

    def test_all_day_event_has_no_time(self):
        task = event_to_task(make_event(start={"date": "2026-09-20"}), TIMEZONE)

        self.assertIsNone(task.start_time)
        self.assertTrue(task.all_day)

    def test_event_without_start_is_ignored(self):
        self.assertIsNone(event_to_task(make_event(start={}), TIMEZONE))

    def test_defaults_are_used_for_events_created_outside_dukielist(self):
        task = event_to_task(make_event(), TIMEZONE)

        self.assertIs(task.priority, Priority.MEDIUM)
        self.assertIs(task.category, Category.PERSONAL)
        self.assertIs(task.status, Status.PENDING)

    def test_stored_properties_win_over_defaults(self):
        event = make_event(extendedProperties={"private": {
            "dukielistPriority": "alta",
            "dukielistCategory": "saúde",
            "dukielistStatus": "concluída",
            "dukielistViewMode": "week",
        }})
        task = event_to_task(event, TIMEZONE)

        self.assertIs(task.priority, Priority.HIGH)
        self.assertIs(task.category, Category.HEALTH)
        self.assertIs(task.status, Status.COMPLETED)
        self.assertIs(task.view_mode, ViewMode.WEEK)

    def test_completed_marker_survives_an_edit_in_the_calendar(self):
        task = event_to_task(make_event(summary=f"{COMPLETED_PREFIX}Consulta"), TIMEZONE)

        self.assertEqual(task.title, "Consulta")
        self.assertIs(task.status, Status.COMPLETED)


class PushTests(GoogleCalendarTestCase):
    def test_saving_a_task_creates_the_event_and_links_it(self):
        task = self.add_task()
        client = FakeCalendarClient()

        stats = push_pending(self.storage, client)

        self.assertEqual(stats.created_remote, 1)
        self.assertEqual(len(client.inserted), 1)
        self.assertEqual(client.inserted[0]["summary"], "Consulta")
        self.assertIsNotNone(self.storage.external_id_for_task(SOURCE, task.id))
        self.assertEqual(self.storage.pending_outbox(SOURCE), [])

    def test_editing_a_task_updates_the_same_event(self):
        task = self.add_task()
        client = FakeCalendarClient()
        push_pending(self.storage, client)
        external_id = self.storage.external_id_for_task(SOURCE, task.id)

        self.service.update(
            task.id,
            title="Consulta remarcada",
            description=task.description,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 21),
            start_time=time(9, 0),
            end_time=time(10, 0),
            all_day=False,
            priority=task.priority,
            category=task.category,
            status=task.status,
        )
        stats = push_pending(self.storage, client)

        self.assertEqual(stats.updated_remote, 1)
        self.assertEqual(len(client.inserted), 1)
        self.assertEqual(client.patched[0][0], external_id)
        self.assertEqual(client.patched[0][1]["summary"], "Consulta remarcada")
        self.assertEqual(client.events[external_id]["start"]["dateTime"], "2026-09-21T09:00:00")

    def test_completing_a_task_marks_the_event(self):
        task = self.add_task()
        client = FakeCalendarClient()
        push_pending(self.storage, client)

        self.service.toggle(task.id)
        push_pending(self.storage, client)

        external_id = self.storage.external_id_for_task(SOURCE, task.id)
        self.assertEqual(
            client.events[external_id]["summary"], f"{COMPLETED_PREFIX}Consulta"
        )

    def test_deleting_a_task_removes_the_event(self):
        task = self.add_task()
        client = FakeCalendarClient()
        push_pending(self.storage, client)
        external_id = self.storage.external_id_for_task(SOURCE, task.id)

        self.service.delete(task.id)
        stats = push_pending(self.storage, client)

        self.assertEqual(stats.deleted_remote, 1)
        self.assertEqual(client.deleted, [external_id])
        self.assertEqual(client.events, {})

    def test_task_created_and_deleted_before_a_sync_never_reaches_the_calendar(self):
        task = self.add_task()
        self.service.delete(task.id)
        client = FakeCalendarClient()

        push_pending(self.storage, client)

        self.assertEqual(client.inserted, [])
        self.assertEqual(client.deleted, [])

    def test_event_removed_in_the_calendar_is_recreated_instead_of_lost(self):
        task = self.add_task()
        client = FakeCalendarClient()
        push_pending(self.storage, client)
        external_id = self.storage.external_id_for_task(SOURCE, task.id)
        del client.events[external_id]

        self.service.toggle(task.id)
        stats = push_pending(self.storage, client)

        self.assertEqual(stats.created_remote, 1)
        new_id = self.storage.external_id_for_task(SOURCE, task.id)
        self.assertNotEqual(new_id, external_id)
        self.assertIn(new_id, client.events)

    def test_nothing_is_queued_while_the_integration_is_off(self):
        self.storage.disable_mirror(SOURCE)
        self.add_task()

        self.assertEqual(self.storage.pending_outbox(SOURCE), [])

    def test_a_failed_delete_does_not_block_the_other_entries(self):
        first = self.add_task(title="Primeira")
        second = self.add_task(title="Segunda")
        client = FakeCalendarClient()
        push_pending(self.storage, client)
        stale_id = self.storage.external_id_for_task(SOURCE, first.id)
        client.missing.add(stale_id)

        self.service.delete(first.id)
        self.service.update(
            second.id,
            title="Segunda revisada",
            description=second.description,
            start_date=second.start_date,
            end_date=second.end_date,
            start_time=second.start_time,
            end_time=second.end_time,
            all_day=second.all_day,
            priority=second.priority,
            category=second.category,
            status=second.status,
        )
        stats = push_pending(self.storage, client)

        # Um evento sumido é tratado como já removido: a fila segue adiante.
        self.assertEqual(stats.deleted_remote, 1)
        self.assertEqual(stats.updated_remote, 1)
        self.assertEqual(self.storage.pending_outbox(SOURCE), [])


class SyncTests(GoogleCalendarTestCase):
    def test_calendar_events_become_tasks(self):
        client = FakeCalendarClient([make_event()])

        stats = sync_google_calendar(
            self.storage,
            client,
            start=date(2026, 9, 1),
            end=date(2026, 10, 1),
        )

        self.assertEqual(stats.created_local, 1)
        tasks = self.storage.list_between(date(2026, 9, 20), date(2026, 9, 21))
        self.assertEqual([task.title for task in tasks], ["Consulta"])
        self.assertEqual(tasks[0].start_time, time(15, 30))
        self.assertEqual(tasks[0].end_time, time(16, 30))

    def test_a_second_sync_does_not_duplicate_anything(self):
        client = FakeCalendarClient([make_event()])
        window = {"start": date(2026, 9, 1), "end": date(2026, 10, 1)}

        sync_google_calendar(self.storage, client, **window)
        stats = sync_google_calendar(self.storage, client, **window)

        self.assertEqual(stats.created_local, 0)
        self.assertEqual(stats.unchanged_local, 1)
        self.assertEqual(len(self.storage.list_between(date(2026, 9, 1), date(2026, 10, 1))), 1)

    def test_a_task_sent_to_the_calendar_does_not_come_back_as_a_change(self):
        self.add_task()
        client = FakeCalendarClient()
        window = {"start": date(2026, 9, 1), "end": date(2026, 10, 1)}

        stats = sync_google_calendar(self.storage, client, **window)
        repeat = sync_google_calendar(self.storage, client, **window)

        self.assertEqual(stats.created_remote, 1)
        self.assertEqual(repeat.unchanged_local, 1)
        self.assertEqual(stats.created_local, 0)
        self.assertEqual(len(self.storage.list_between(date(2026, 9, 1), date(2026, 10, 1))), 1)

    def test_first_sync_imports_remote_events_and_sends_existing_local_tasks(self):
        self.storage.disable_mirror(SOURCE)
        local = self.add_task(title="Tarefa anterior à conexão")
        self.storage.enable_mirror(SOURCE)
        client = FakeCalendarClient([make_event(id="evento-do-google", summary="Evento do Google")])
        window = {"start": date(2026, 9, 1), "end": date(2026, 10, 1)}

        first = sync_google_calendar(self.storage, client, **window)
        second = sync_google_calendar(self.storage, client, **window)

        self.assertEqual(first.created_local, 1)
        self.assertEqual(first.created_remote, 1)
        self.assertEqual(second.created_local, 0)
        self.assertEqual(second.created_remote, 0)
        self.assertEqual(len(client.events), 2)
        self.assertIn(self.storage.external_id_for_task(SOURCE, local.id), client.events)
        self.assertEqual(
            {task.title for task in self.storage.list_between(date(2026, 9, 1), date(2026, 10, 1))},
            {"Tarefa anterior à conexão", "Evento do Google"},
        )

    def test_an_edit_made_in_the_calendar_reaches_the_task(self):
        task = self.add_task()
        client = FakeCalendarClient()
        window = {"start": date(2026, 9, 1), "end": date(2026, 10, 1)}
        sync_google_calendar(self.storage, client, **window)
        external_id = self.storage.external_id_for_task(SOURCE, task.id)

        client.events[external_id].update({
            "summary": "Consulta remarcada",
            "start": {"dateTime": "2026-09-22T09:00:00-03:00"},
            "updated": "2026-09-21T23:00:00.000Z",
        })
        stats = sync_google_calendar(self.storage, client, **window)

        self.assertEqual(stats.updated_local, 1)
        updated = self.storage.get(task.id)
        self.assertEqual(updated.title, "Consulta remarcada")
        self.assertEqual(updated.start_date, date(2026, 9, 22))
        self.assertEqual(updated.start_time, time(9, 0))
        self.assertEqual(updated.end_time, time(10, 0))
        # Prioridade e categoria não existem no Google: seguem as da tarefa.
        self.assertIs(updated.priority, Priority.HIGH)
        self.assertIs(updated.category, Category.HEALTH)

    def test_one_unwritable_event_does_not_freeze_the_queue(self):
        # Um evento que o Google recusa alterar (403) ficava na frente da fila
        # e impedia o envio de todos os outros, parando a sincronização.
        blocked = self.add_task(title="Evento travado")
        other = self.add_task(title="Segue normal", start_date=date(2026, 9, 25),
                              end_date=date(2026, 9, 25))
        client = FakeCalendarClient()
        window = {"start": date(2026, 9, 1), "end": date(2026, 10, 1)}
        sync_google_calendar(self.storage, client, **window)

        external_id = self.storage.external_id_for_task(SOURCE, blocked.id)
        client.forbidden.add(external_id)
        self.service.toggle(blocked.id)
        self.service.toggle(other.id)
        stats = sync_google_calendar(self.storage, client, **window)

        # O item recusado conta como falha, mas o outro chega à agenda.
        self.assertEqual(stats.failed_remote, 1)
        self.assertEqual(stats.updated_remote, 1)
        other_id = self.storage.external_id_for_task(SOURCE, other.id)
        self.assertTrue(client.events[other_id]["summary"].startswith(COMPLETED_PREFIX))
        # Continua pendente para a próxima rodada, em vez de ser descartado.
        pending = [e["task_id"] for e in self.storage.pending_outbox(SOURCE)]
        self.assertEqual(pending, [blocked.id])

    def test_zero_duration_event_is_imported_instead_of_dropped(self):
        # O Google aceita eventos que começam e terminam no mesmo instante.
        # Eles precisam virar tarefa, não desaparecer na importação.
        client = FakeCalendarClient([
            make_event(
                id="instant-1",
                start={"dateTime": "2026-09-22T09:00:00-03:00"},
                end={"dateTime": "2026-09-22T09:00:00-03:00"},
            )
        ])
        stats = sync_google_calendar(
            self.storage, client, start=date(2026, 9, 1), end=date(2026, 10, 1)
        )

        self.assertEqual(stats.created_local, 1)
        self.assertEqual(stats.skipped, 0)
        imported = self.storage.list_between(date(2026, 9, 22), date(2026, 9, 23))
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0].start_time, time(9, 0))
        self.assertEqual(imported[0].end_time, time(10, 0))

    def test_google_edits_still_arrive_after_local_completion(self):
        task = self.add_task()
        client = FakeCalendarClient()
        window = {"start": date(2026, 9, 1), "end": date(2026, 10, 1)}
        sync_google_calendar(self.storage, client, **window)
        external_id = self.storage.external_id_for_task(SOURCE, task.id)

        self.service.toggle(task.id)
        sync_google_calendar(self.storage, client, **window)
        client.events[external_id].update({
            "summary": f"{COMPLETED_PREFIX}Consulta remarcada",
            "updated": "2026-09-22T12:00:00.000Z",
        })
        stats = sync_google_calendar(self.storage, client, **window)

        self.assertEqual(stats.updated_local, 1)
        self.assertEqual(self.storage.get(task.id).title, "Consulta remarcada")

    def test_an_event_deleted_in_the_calendar_removes_the_task(self):
        client = FakeCalendarClient([make_event()])
        window = {"start": date(2026, 9, 1), "end": date(2026, 10, 1)}
        sync_google_calendar(self.storage, client, **window)

        client.events["event-1"]["status"] = "cancelled"
        stats = sync_google_calendar(self.storage, client, **window)

        self.assertEqual(stats.deleted_local, 1)
        self.assertEqual(self.storage.list_between(date(2026, 9, 1), date(2026, 10, 1)), [])
        # A exclusão veio da agenda; nada a devolver para lá.
        self.assertEqual(self.storage.pending_outbox(SOURCE), [])

    def test_push_only_skips_reading_the_calendar(self):
        self.add_task()
        client = FakeCalendarClient([make_event()])

        stats = sync_google_calendar(self.storage, client, outbound_only=True)

        self.assertEqual(stats.created_remote, 1)
        self.assertEqual(stats.fetched, 0)
        # O evento que já estava na agenda não foi importado nesta rodada.
        titles = [
            task.title
            for task in self.storage.list_between(date(2026, 9, 1), date(2026, 10, 1))
        ]
        self.assertEqual(titles, ["Consulta"])


if __name__ == "__main__":
    unittest.main()


class HttpStatusMappingTests(unittest.TestCase):
    """Como cada resposta do Google é traduzida para os erros do aplicativo."""

    def _client(self):
        from googleapiclient.errors import HttpError

        client = GoogleCalendarClient.__new__(GoogleCalendarClient)
        client._http_error = HttpError
        client._network_errors = ()
        client.calendar_id = "primary"
        return client

    def _request(self, status, reason):
        from googleapiclient.errors import HttpError

        class Response(dict):
            def __init__(self, status):
                super().__init__(status=status)
                self.status = status
                self.reason = "Forbidden"

        body = json.dumps(
            {"error": {"code": status, "message": "Forbidden",
                       "errors": [{"domain": "global", "reason": reason,
                                   "message": "Forbidden"}]}}
        ).encode()

        class Request:
            def execute(self):
                raise HttpError(Response(status), body)

        return Request()

    def test_403_on_an_event_marks_it_unavailable_instead_of_demanding_auth(self):
        # Era o que travava a fila: o 403 de um evento virava erro de
        # autorização e derrubava a sincronização inteira.
        client = self._client()
        with self.assertRaises(GoogleEventUnavailableError):
            client._call(self._request(403, "forbidden"), event_scoped=True)

    def test_403_outside_an_event_still_asks_for_authorization(self):
        client = self._client()
        with self.assertRaises(GoogleAuthRequiredError):
            client._call(self._request(403, "forbidden"))

    def test_rate_limit_is_temporary_not_an_authorization_problem(self):
        client = self._client()
        with self.assertRaises(GoogleCalendarError) as caught:
            client._call(self._request(403, "rateLimitExceeded"), event_scoped=True)
        self.assertNotIsInstance(caught.exception, GoogleAuthRequiredError)
        self.assertNotIsInstance(caught.exception, GoogleEventUnavailableError)
