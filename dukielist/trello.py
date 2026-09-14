from __future__ import annotations

import json
import os
import unicodedata
import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import Category, Priority, Status, Task, ViewMode
from .storage import SQLiteStorage


DEFAULT_ENV_FILE = Path.home() / "Projetos" / "codex-trello-env" / ".env"
DEFAULT_TIMEZONE = "America/Sao_Paulo"
TRELLO_API_BASE = "https://api.trello.com/1"


class TrelloError(RuntimeError):
    """Erro de configuração, rede ou resposta da API do Trello."""


class TrelloCardUnavailableError(TrelloError):
    """A linked card no longer exists or is no longer accessible."""


@dataclass(frozen=True, slots=True)
class TrelloCredentials:
    key: str
    token: str


@dataclass(frozen=True, slots=True)
class SyncStats:
    fetched: int = 0
    eligible: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_undated: int = 0
    skipped_before_start: int = 0
    skipped_completed: int = 0
    pushed_completions: int = 0
    failed_completions: int = 0


@dataclass(frozen=True, slots=True)
class CompletionSyncStats:
    pushed: int = 0
    pulled: int = 0
    unchanged: int = 0
    failed: int = 0


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TrelloError(f"Não foi possível ler as credenciais em {path}.") from exc

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_credentials(env_file: Path | None = None) -> TrelloCredentials:
    """Carrega credenciais sem copiá-las para o projeto ou para o banco."""
    values: dict[str, str] = {}
    configured_path = os.environ.get("DUKIELIST_TRELLO_ENV")
    candidate = env_file or (Path(configured_path).expanduser() if configured_path else None)

    if candidate is not None:
        values.update(_parse_env_file(candidate.expanduser()))
    elif DEFAULT_ENV_FILE.exists():
        values.update(_parse_env_file(DEFAULT_ENV_FILE))

    key = os.environ.get("TRELLO_API_KEY") or values.get("TRELLO_API_KEY", "")
    token = os.environ.get("TRELLO_TOKEN") or values.get("TRELLO_TOKEN", "")
    if not key or not token:
        raise TrelloError(
            "Credenciais ausentes. Defina TRELLO_API_KEY e TRELLO_TOKEN ou use --env-file."
        )
    return TrelloCredentials(key=key, token=token)


class TrelloClient:
    def __init__(self, credentials: TrelloCredentials, *, timeout: float = 20) -> None:
        self.credentials = credentials
        self.timeout = timeout

    def _request(self, method: str, path: str, **params: str) -> Any:
        query = urlencode({
            **params,
            "key": self.credentials.key,
            "token": self.credentials.token,
        })
        request = Request(
            f"{TRELLO_API_BASE}{path}?{query}",
            headers={"Accept": "application/json"},
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code in {401, 403}:
                message = "O Trello recusou as credenciais configuradas."
            elif exc.code == 404:
                raise TrelloCardUnavailableError(
                    "Um cartão vinculado não existe mais ou não está acessível."
                ) from exc
            elif exc.code == 429:
                message = "O limite temporário de requisições do Trello foi atingido."
            else:
                message = f"O Trello respondeu com erro HTTP {exc.code}."
            raise TrelloError(message) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise TrelloError("Não foi possível conectar ao Trello.") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TrelloError("O Trello devolveu uma resposta inválida.") from exc

    def _get(self, path: str, **params: str) -> Any:
        return self._request("GET", path, **params)

    def card_completion(self, card_id: str) -> dict[str, Any]:
        return self._get(
            f"/cards/{quote(card_id, safe='')}",
            fields="id,dueComplete,dateLastActivity",
        )

    def update_completion(self, card_id: str, completed: bool) -> dict[str, Any]:
        """Set Trello's due-date completion flag for one linked card."""
        return self._request(
            "PUT",
            f"/cards/{quote(card_id, safe='')}",
            dueComplete="true" if completed else "false",
        )

    def boards(self) -> list[dict[str, Any]]:
        return self._get("/members/me/boards", filter="open", fields="name,closed")

    def _resolve_boards(
        self, requested: Iterable[str], boards: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for value in requested:
            clean = value.strip()
            exact = [
                board for board in boards
                if board["id"] == clean or str(board.get("name", "")).casefold() == clean.casefold()
            ]
            matches = exact or [
                board for board in boards
                if clean.casefold() in str(board.get("name", "")).casefold()
            ]
            if not matches:
                raise TrelloError(f"Quadro do Trello não encontrado: {value}")
            if len(matches) > 1:
                names = ", ".join(str(board.get("name", board["id"])) for board in matches)
                raise TrelloError(f"Nome de quadro ambíguo ({value}): {names}")
            if matches[0] not in selected:
                selected.append(matches[0])
        return selected

    def cards(self, boards: Iterable[str] = ()) -> list[dict[str, Any]]:
        available_boards = self.boards()
        board_names = {board["id"]: str(board.get("name", "")) for board in available_boards}
        requested = tuple(boards)
        selected = self._resolve_boards(requested, available_boards) if requested else []
        fields = "name,desc,due,dueComplete,idBoard,idList,url,labels,dateLastActivity,closed"

        if selected:
            cards: list[dict[str, Any]] = []
            for board in selected:
                cards.extend(self._get(f"/boards/{board['id']}/cards", filter="open", fields=fields))
        else:
            cards = self._get("/members/me/cards", filter="open", fields=fields)

        relevant_board_ids = {str(card.get("idBoard", "")) for card in cards}
        list_names: dict[str, str] = {}
        for board_id in relevant_board_ids:
            if not board_id:
                continue
            lists = self._get(f"/boards/{board_id}/lists", filter="all", fields="name,closed")
            list_names.update({item["id"]: str(item.get("name", "")) for item in lists})

        for card in cards:
            card["boardName"] = board_names.get(str(card.get("idBoard", "")), "")
            card["listName"] = list_names.get(str(card.get("idList", "")), "")
        return cards


@contextmanager
def _trello_sync_guard(storage: SQLiteStorage):
    """Serialize Trello traffic from terminal instances sharing one database."""
    lock_path = storage.db_path.with_name(f"{storage.db_path.name}.trello-sync.lock")
    try:
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise TrelloError("Não foi possível coordenar a sincronização do Trello.") from exc


def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _category(card: dict[str, Any]) -> Category:
    labels = " ".join(str(label.get("name", "")) for label in card.get("labels", []))
    text = _normalized(f"{card.get('boardName', '')} {card.get('name', '')} {labels}")
    if any(term in text for term in ("saude", "medico", "consulta", "remedio")):
        return Category.HEALTH
    if any(term in text for term in ("estudo", "curso", "faculdade", "leitura", "coders")):
        return Category.STUDY
    if any(term in text for term in ("trabalho", "emprego", "cliente")):
        return Category.WORK
    if any(term in text for term in ("meta", "planejamento", "despesa", "projeto")):
        return Category.PLANNING
    return Category.PERSONAL


def _priority(card: dict[str, Any]) -> Priority:
    labels = _normalized(" ".join(str(label.get("name", "")) for label in card.get("labels", [])))
    if any(term in labels for term in ("urgente", "prioridade alta", "alta prioridade")):
        return Priority.HIGH
    if any(term in labels for term in ("prioridade baixa", "baixa prioridade")):
        return Priority.LOW
    return Priority.MEDIUM


def _due_datetime(value: str, timezone: ZoneInfo) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrelloError(f"Prazo inválido recebido do Trello: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def card_to_task(card: dict[str, Any], timezone: ZoneInfo) -> Task:
    due = _due_datetime(str(card["due"]), timezone)
    labels = [str(label.get("name", "")).strip() for label in card.get("labels", [])]
    labels = [label for label in labels if label]
    metadata = [
        "Origem: Trello",
        f"Quadro: {card.get('boardName') or '—'}",
        f"Lista: {card.get('listName') or '—'}",
    ]
    if labels:
        metadata.append(f"Etiquetas: {', '.join(labels)}")
    if card.get("url"):
        metadata.append(f"Link: {card['url']}")
    original_description = str(card.get("desc") or "").strip()
    description = "\n\n".join(part for part in (original_description, "\n".join(metadata)) if part)
    now = datetime.now()
    return Task(
        id=None,
        title=str(card.get("name") or "Cartão do Trello").strip(),
        description=description,
        task_date=due.date(),
        task_time=due.time().replace(second=0, microsecond=0, tzinfo=None),
        priority=_priority(card),
        category=_category(card),
        status=Status.COMPLETED if card.get("dueComplete") else Status.PENDING,
        view_mode=ViewMode.DAY,
        created_at=now,
        updated_at=now,
    )


def _push_pending_completions(
    storage: SQLiteStorage, client: TrelloClient
) -> tuple[int, int]:
    """Push durable local status changes and acknowledge only matching writes."""
    pushed = 0
    failed = 0
    for link in storage.pending_external_completions("trello"):
        external_id = str(link["external_id"])
        completed = bool(link["pending_completed"])
        try:
            card = client.update_completion(external_id, completed)
        except TrelloCardUnavailableError:
            # One removed/inaccessible card must not block every other link.
            failed += 1
            continue
        if storage.mark_external_completion_synced(
            source="trello",
            external_id=external_id,
            completed=completed,
            external_updated_at=str(card.get("dateLastActivity") or ""),
        ):
            pushed += 1
    return pushed, failed


def push_pending_completions(storage: SQLiteStorage, client: TrelloClient) -> int:
    pushed, _failed = _push_pending_completions(storage, client)
    return pushed


def _sync_linked_completions(
    storage: SQLiteStorage,
    client: TrelloClient,
    *,
    outbound_only: bool = False,
) -> CompletionSyncStats:
    """Synchronize completion both ways for every linked Trello card.

    Pending local writes are sent first. For older links created before the
    outbox existed, equal Trello timestamps mean a status mismatch originated
    locally and the local state is promoted to Trello.
    """
    pushed, failed = _push_pending_completions(storage, client)
    if outbound_only:
        return CompletionSyncStats(pushed=pushed, failed=failed)

    pulled = 0
    unchanged = 0
    for link in storage.external_completion_links("trello"):
        external_id = str(link["external_id"])
        try:
            card = client.card_completion(external_id)
        except TrelloCardUnavailableError:
            failed += 1
            continue
        remote_completed = bool(card.get("dueComplete"))
        remote_updated_at = str(card.get("dateLastActivity") or "")
        local_completed = bool(link["completed"])

        if (
            link["pending_completed"] is None
            and link["external_updated_at"] == remote_updated_at
            and local_completed != remote_completed
        ):
            updated = client.update_completion(external_id, local_completed)
            storage.mark_external_completion_synced(
                source="trello",
                external_id=external_id,
                completed=local_completed,
                external_updated_at=str(updated.get("dateLastActivity") or ""),
                require_pending=False,
            )
            pushed += 1
            continue

        changed = storage.apply_external_completion(
            source="trello",
            external_id=external_id,
            completed=remote_completed,
            external_updated_at=remote_updated_at,
        )
        if changed:
            pulled += 1
        else:
            unchanged += 1
    return CompletionSyncStats(
        pushed=pushed,
        pulled=pulled,
        unchanged=unchanged,
        failed=failed,
    )


def sync_linked_completions(
    storage: SQLiteStorage,
    client: TrelloClient,
    *,
    outbound_only: bool = False,
) -> CompletionSyncStats:
    with _trello_sync_guard(storage):
        return _sync_linked_completions(
            storage,
            client,
            outbound_only=outbound_only,
        )


def _sync_trello(
    storage: SQLiteStorage,
    client: TrelloClient,
    *,
    boards: Iterable[str] = (),
    start_date: date | None = None,
    include_completed: bool = False,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> SyncStats:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise TrelloError(f"Fuso horário desconhecido: {timezone_name}") from exc

    pushed_completions, failed_completions = _push_pending_completions(storage, client)
    cards = client.cards(boards)
    start_date = start_date or datetime.now(timezone).date()
    counts = {
        "eligible": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped_undated": 0,
        "skipped_before_start": 0,
        "skipped_completed": 0,
    }

    for card in cards:
        external_id = str(card.get("id") or "")
        if not external_id or not card.get("due"):
            counts["skipped_undated"] += 1
            continue

        linked = storage.external_task_id("trello", external_id) is not None
        task = card_to_task(card, timezone)
        if not linked and task.task_date < start_date:
            counts["skipped_before_start"] += 1
            continue
        if not linked and task.completed and not include_completed:
            counts["skipped_completed"] += 1
            continue

        counts["eligible"] += 1
        result = storage.sync_external(
            task,
            source="trello",
            external_id=external_id,
            external_updated_at=str(card.get("dateLastActivity") or ""),
            external_url=str(card.get("url") or "") or None,
        )
        counts[result] += 1

    return SyncStats(
        fetched=len(cards),
        pushed_completions=pushed_completions,
        failed_completions=failed_completions,
        **counts,
    )


def sync_trello(
    storage: SQLiteStorage,
    client: TrelloClient,
    *,
    boards: Iterable[str] = (),
    start_date: date | None = None,
    include_completed: bool = False,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> SyncStats:
    with _trello_sync_guard(storage):
        return _sync_trello(
            storage,
            client,
            boards=boards,
            start_date=start_date,
            include_completed=include_completed,
            timezone_name=timezone_name,
        )
