import tempfile
import unittest
import json
from io import BytesIO
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from dukielist.models import Category, Priority, Status
from dukielist.storage import SQLiteStorage
from dukielist.trello import (
    TrelloClient,
    TrelloCardUnavailableError,
    TrelloCredentials,
    TrelloError,
    card_to_task,
    sync_linked_completions,
    sync_trello,
)


def make_card(**changes):
    card = {
        "id": "card-1",
        "name": "Consulta urgente",
        "desc": "Levar os exames",
        "due": "2026-09-20T15:30:00.000Z",
        "dueComplete": False,
        "boardName": "Compromissos",
        "listName": "Agendados",
        "url": "https://trello.com/c/card-1",
        "labels": [{"name": "Urgente"}],
        "dateLastActivity": "2026-09-13T12:00:00.000Z",
    }
    card.update(changes)
    return card


class FakeClient:
    def __init__(self, cards, completions=None):
        self.returned_cards = cards
        self.requested_boards = None
        self.completions = completions or {}
        self.completion_updates = []

    def cards(self, boards=()):
        self.requested_boards = tuple(boards)
        return self.returned_cards

    def card_completion(self, card_id):
        return dict(self.completions[card_id])

    def update_completion(self, card_id, completed):
        self.completion_updates.append((card_id, completed))
        current = self.completions.setdefault(card_id, {})
        current.update({
            "id": card_id,
            "dueComplete": completed,
            "dateLastActivity": f"write-{len(self.completion_updates)}",
        })
        return dict(current)


class TrelloMappingTest(unittest.TestCase):
    @staticmethod
    def _linked_storage(directory):
        storage = SQLiteStorage(Path(directory) / "tasks.db")
        sync_trello(
            storage,
            FakeClient([make_card()]),
            start_date=date(2026, 9, 13),
        )
        return storage

    def test_card_mapping_uses_local_timezone_and_labels(self):
        task = card_to_task(make_card(), ZoneInfo("America/Sao_Paulo"))
        self.assertEqual(task.task_date, date(2026, 9, 20))
        self.assertEqual(task.task_time.isoformat(timespec="minutes"), "12:30")
        self.assertEqual(task.category, Category.HEALTH)
        self.assertEqual(task.priority, Priority.HIGH)
        self.assertEqual(task.status, Status.PENDING)
        self.assertIn("Quadro: Compromissos", task.description)
        self.assertIn("https://trello.com/c/card-1", task.description)

    def test_sync_filters_new_cards_and_updates_linked_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(Path(directory) / "tasks.db")
            client = FakeClient([
                make_card(),
                make_card(id="old", due="2026-08-01T12:00:00Z"),
                make_card(id="done", dueComplete=True),
                make_card(id="undated", due=None),
            ])
            stats = sync_trello(
                storage,
                client,
                boards=["Compromissos"],
                start_date=date(2026, 9, 13),
            )
            self.assertEqual(client.requested_boards, ("Compromissos",))
            self.assertEqual(stats.created, 1)
            self.assertEqual(stats.skipped_before_start, 1)
            self.assertEqual(stats.skipped_completed, 1)
            self.assertEqual(stats.skipped_undated, 1)

            changed = make_card(
                name="Consulta confirmada",
                dueComplete=True,
                dateLastActivity="2026-09-14T12:00:00.000Z",
            )
            second = sync_trello(
                storage,
                FakeClient([changed]),
                start_date=date(2026, 9, 21),
            )
            self.assertEqual(second.updated, 1)
            task_id = storage.external_task_id("trello", "card-1")
            task = storage.get(task_id)
            self.assertEqual(task.title, "Consulta confirmada")
            self.assertEqual(task.status, Status.COMPLETED)

    def test_completion_is_bidirectional_and_reopening_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = self._linked_storage(directory)
            task_id = storage.external_task_id("trello", "card-1")
            local = storage.get(task_id)
            local.status = Status.COMPLETED
            storage.update(local)
            client = FakeClient([], {
                "card-1": make_card(dueComplete=False),
            })

            outbound = sync_linked_completions(storage, client)
            self.assertEqual(outbound.pushed, 1)
            self.assertEqual(client.completion_updates, [("card-1", True)])
            self.assertEqual(storage.pending_external_completions("trello"), [])

            client.completions["card-1"].update({
                "dueComplete": False,
                "dateLastActivity": "remote-reopen",
            })
            inbound = sync_linked_completions(storage, client)
            self.assertEqual(inbound.pulled, 1)
            self.assertFalse(storage.get(task_id).completed)

    def test_failed_upload_remains_pending_for_retry(self):
        class FailingClient(FakeClient):
            def update_completion(self, card_id, completed):
                raise TrelloError("falha simulada")

        with tempfile.TemporaryDirectory() as directory:
            storage = self._linked_storage(directory)
            task_id = storage.external_task_id("trello", "card-1")
            local = storage.get(task_id)
            local.status = Status.COMPLETED
            storage.update(local)

            with self.assertRaises(TrelloError):
                sync_linked_completions(
                    storage,
                    FailingClient([], {"card-1": make_card()}),
                    outbound_only=True,
                )
            pending = storage.pending_external_completions("trello")
            self.assertEqual(pending[0]["pending_completed"], True)

    def test_one_unavailable_card_does_not_block_other_pending_cards(self):
        class PartiallyUnavailableClient(FakeClient):
            def update_completion(self, card_id, completed):
                if card_id == "card-1":
                    raise TrelloCardUnavailableError("removido")
                return super().update_completion(card_id, completed)

        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(Path(directory) / "tasks.db")
            sync_trello(
                storage,
                FakeClient([make_card(), make_card(id="card-2")]),
                start_date=date(2026, 9, 13),
            )
            for card_id in ("card-1", "card-2"):
                task = storage.get(storage.external_task_id("trello", card_id))
                task.status = Status.COMPLETED
                storage.update(task)

            client = PartiallyUnavailableClient([], {
                "card-1": make_card(),
                "card-2": make_card(id="card-2"),
            })
            stats = sync_linked_completions(storage, client, outbound_only=True)

            self.assertEqual(stats.pushed, 1)
            self.assertEqual(stats.failed, 1)
            remaining = storage.pending_external_completions("trello")
            self.assertEqual([link["external_id"] for link in remaining], ["card-1"])

    def test_update_completion_uses_put_and_boolean_due_complete(self):
        response = BytesIO(json.dumps({
            "id": "card/with space",
            "dueComplete": True,
            "dateLastActivity": "v2",
        }).encode())
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return response

        client = TrelloClient(TrelloCredentials("api-key", "api-token"), timeout=7)
        with patch("dukielist.trello.urlopen", side_effect=fake_urlopen):
            result = client.update_completion("card/with space", True)

        request = captured["request"]
        parsed = urlparse(request.full_url)
        params = parse_qs(parsed.query)
        self.assertEqual(request.method, "PUT")
        self.assertEqual(parsed.path, "/1/cards/card%2Fwith%20space")
        self.assertEqual(params["dueComplete"], ["true"])
        self.assertEqual(params["key"], ["api-key"])
        self.assertEqual(params["token"], ["api-token"])
        self.assertEqual(captured["timeout"], 7)
        self.assertTrue(result["dueComplete"])


if __name__ == "__main__":
    unittest.main()
