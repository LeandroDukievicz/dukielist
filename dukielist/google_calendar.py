from __future__ import annotations

import fcntl
import json
import os
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import Category, Priority, Status, Task, ViewMode
from .storage import SQLiteStorage


SOURCE = "google_calendar"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
DEFAULT_CREDENTIALS_FILE = Path.home() / ".config" / "dukielist" / "google_credentials.json"
BUNDLED_CREDENTIALS_FILE = Path(__file__).with_name("google_oauth_client.json")
DEFAULT_TOKEN_FILE = Path.home() / ".local" / "share" / "dukielist" / "google-token.json"
DEFAULT_CALENDAR_ID = "primary"
DEFAULT_TIMEZONE = "America/Sao_Paulo"
DEFAULT_DURATION_MINUTES = 60
PULL_DAYS_BACK = 7
PULL_DAYS_AHEAD = 180
CALENDAR_SETTING_KEY = "google_calendar_id"
INITIAL_SYNC_SETTING_KEY = "google_calendar_initial_sync"
COMPLETED_PREFIX = "✔ "


class GoogleCalendarError(RuntimeError):
    """Erro de configuração, rede ou resposta da API do Google Calendar."""


class GoogleAuthRequiredError(GoogleCalendarError):
    """Ainda não há uma autorização válida para a conta do Google."""


class GoogleEventUnavailableError(GoogleCalendarError):
    """O evento vinculado não existe mais ou não está acessível."""


@dataclass(frozen=True, slots=True)
class SyncStats:
    created_remote: int = 0
    updated_remote: int = 0
    deleted_remote: int = 0
    failed_remote: int = 0
    fetched: int = 0
    created_local: int = 0
    updated_local: int = 0
    unchanged_local: int = 0
    deleted_local: int = 0
    skipped: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.created_local or self.updated_local or self.deleted_local)


def credentials_file(override: Path | None = None) -> Path:
    configured = os.environ.get("DUKIELIST_GOOGLE_CREDENTIALS")
    if override is not None:
        return override.expanduser()
    if configured:
        return Path(configured).expanduser()
    if DEFAULT_CREDENTIALS_FILE.exists():
        return DEFAULT_CREDENTIALS_FILE
    if BUNDLED_CREDENTIALS_FILE.exists():
        return BUNDLED_CREDENTIALS_FILE
    return DEFAULT_CREDENTIALS_FILE


def token_file(override: Path | None = None) -> Path:
    configured = os.environ.get("DUKIELIST_GOOGLE_TOKEN")
    if override is not None:
        return override.expanduser()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_TOKEN_FILE


def is_authorized(token_path: Path | None = None) -> bool:
    return token_file(token_path).exists()


def _google_modules():
    """Importa as bibliotecas do Google só quando a integração é usada."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise GoogleCalendarError(
            "Bibliotecas do Google ausentes. Instale com: "
            "pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        ) from exc
    return Request, Credentials, InstalledAppFlow, build, HttpError


def _network_error_types() -> tuple[type[BaseException], ...]:
    """Falhas de conexão das camadas usadas pelo cliente do Google."""
    import httplib2
    from google.auth.exceptions import TransportError

    return (OSError, TimeoutError, httplib2.HttpLib2Error, TransportError)


def _write_token(path: Path, credentials: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The refresh token is a long-lived secret: never leave it group-readable.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(credentials.to_json())


def authorize(
    *,
    credentials_path: Path | None = None,
    token_path: Path | None = None,
    calendar_id: str = DEFAULT_CALENDAR_ID,
    quiet: bool = False,
) -> str:
    """Abre o consentimento no navegador e guarda o token renovável."""
    _Request, _Credentials, InstalledAppFlow, _build, _HttpError = _google_modules()
    secrets = credentials_file(credentials_path)
    if not secrets.exists():
        raise GoogleCalendarError(
            f"Cliente OAuth do DukieList não encontrado em {secrets}. "
            "A instalação precisa incluir o JSON do aplicativo para conectar a agenda."
        )
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    except (ValueError, json.JSONDecodeError) as exc:
        raise GoogleCalendarError(
            f"O arquivo {secrets} não é uma credencial OAuth válida."
        ) from exc

    from google_auth_oauthlib.flow import WSGITimeoutError

    try:
        webbrowser.get()
    except webbrowser.Error:
        if quiet:
            raise GoogleCalendarError(
                "Nenhum navegador foi encontrado. Rode 'dukielist auth google' "
                "no terminal para abrir o link manualmente."
            ) from None
        open_browser = False
    else:
        open_browser = True

    try:
        credentials = flow.run_local_server(
            port=0,
            prompt="consent",
            authorization_prompt_message=None if quiet else (
                "Abrindo o navegador para autorizar o DukieList. "
                "Se nada abrir, acesse:\n{url}"
            ),
            success_message="DukieList autorizado. Pode fechar esta aba.",
            timeout_seconds=300,
            open_browser=open_browser,
        )
    except (*_network_error_types(), WSGITimeoutError, webbrowser.Error) as exc:
        raise GoogleCalendarError(
            "Não foi possível concluir a autorização no navegador."
        ) from exc

    _write_token(token_file(token_path), credentials)
    client = GoogleCalendarClient(credentials, calendar_id=calendar_id)
    return client.calendar_name()


def load_user_credentials(token_path: Path | None = None) -> Any:
    """Carrega o token salvo, renovando-o quando o acesso expira."""
    Request, Credentials, _flow, _build, _HttpError = _google_modules()
    path = token_file(token_path)
    if not path.exists():
        raise GoogleAuthRequiredError(
            "Conta do Google não autorizada. Rode: dukielist auth google"
        )
    try:
        credentials = Credentials.from_authorized_user_file(str(path), SCOPES)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        raise GoogleAuthRequiredError(
            f"Token inválido em {path}. Rode novamente: dukielist auth google"
        ) from exc

    if credentials.valid:
        return credentials
    if not (credentials.expired and credentials.refresh_token):
        raise GoogleAuthRequiredError(
            "A autorização do Google expirou. Rode: dukielist auth google"
        )
    try:
        credentials.refresh(Request())
    except Exception as exc:  # google.auth.exceptions.RefreshError e derivados
        raise GoogleAuthRequiredError(
            "Não foi possível renovar o acesso ao Google. "
            "Rode novamente: dukielist auth google"
        ) from exc
    _write_token(path, credentials)
    return credentials


def calendar_id_for(storage: SQLiteStorage | None = None) -> str:
    configured = os.environ.get("DUKIELIST_GOOGLE_CALENDAR")
    if configured:
        return configured
    if storage is not None:
        stored = storage.get_setting(CALENDAR_SETTING_KEY)
        if stored:
            return stored
    return DEFAULT_CALENDAR_ID


class GoogleCalendarClient:
    def __init__(self, credentials: Any, *, calendar_id: str = DEFAULT_CALENDAR_ID) -> None:
        _Request, _Credentials, _flow, build, self._http_error = _google_modules()
        self._network_errors = _network_error_types()
        self.calendar_id = calendar_id
        self._service = build(
            "calendar", "v3", credentials=credentials, cache_discovery=False
        )

    def _call(self, request: Any) -> Any:
        try:
            return request.execute()
        except self._http_error as exc:
            status = getattr(exc.resp, "status", 0)
            if status in {404, 410}:
                raise GoogleEventUnavailableError(
                    "Um evento vinculado não existe mais na agenda."
                ) from exc
            if status in {401, 403}:
                raise GoogleAuthRequiredError(
                    "O Google recusou o acesso à agenda. "
                    "Rode novamente: dukielist auth google"
                ) from exc
            if status == 429 or status >= 500:
                raise GoogleCalendarError(
                    "O Google Calendar está indisponível no momento."
                ) from exc
            raise GoogleCalendarError(
                f"O Google Calendar respondeu com erro HTTP {status}."
            ) from exc
        except self._network_errors as exc:
            raise GoogleCalendarError("Não foi possível conectar ao Google Calendar.") from exc

    def calendar_name(self) -> str:
        calendar = self._call(
            self._service.events().list(calendarId=self.calendar_id, maxResults=1)
        )
        return str(calendar.get("summary") or self.calendar_id)

    def list_events(self, start: date, end: date, timezone: ZoneInfo) -> list[dict[str, Any]]:
        time_min = datetime.combine(start, time.min, tzinfo=timezone).isoformat()
        time_max = datetime.combine(end, time.min, tzinfo=timezone).isoformat()
        events: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            response = self._call(
                self._service.events().list(
                    calendarId=self.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    showDeleted=True,
                    maxResults=2500,
                    pageToken=page_token,
                )
            )
            events.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return events

    def insert_event(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._call(
            self._service.events().insert(calendarId=self.calendar_id, body=body)
        )

    def patch_event(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        # PATCH keeps fields DukieList does not manage (convidados, lembretes,
        # anexos) intactos em eventos que nasceram na própria agenda.
        return self._call(
            self._service.events().patch(
                calendarId=self.calendar_id, eventId=event_id, body=body
            )
        )

    def delete_event(self, event_id: str) -> None:
        self._call(
            self._service.events().delete(calendarId=self.calendar_id, eventId=event_id)
        )


def build_client(
    storage: SQLiteStorage | None = None,
    *,
    calendar_id: str | None = None,
    token_path: Path | None = None,
) -> GoogleCalendarClient:
    return GoogleCalendarClient(
        load_user_credentials(token_path),
        calendar_id=calendar_id or calendar_id_for(storage),
    )


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise GoogleCalendarError(f"Fuso horário desconhecido: {name}") from exc


def _private_properties(task: Task) -> dict[str, str]:
    """Campos que o Google Calendar não tem, guardados junto do evento."""
    return {
        "dukielistPriority": task.priority.value,
        "dukielistCategory": task.category.value,
        "dukielistStatus": task.status.value,
        "dukielistViewMode": task.view_mode.value,
    }


def task_to_event(task: Task, timezone_name: str = DEFAULT_TIMEZONE) -> dict[str, Any]:
    summary = f"{COMPLETED_PREFIX}{task.title}" if task.completed else task.title
    body: dict[str, Any] = {
        "summary": summary,
        "description": task.description,
        "extendedProperties": {"private": _private_properties(task)},
    }
    if task.all_day:
        body["start"] = {"date": task.start_date.isoformat()}
        body["end"] = {"date": (task.end_date + timedelta(days=1)).isoformat()}
    else:
        start = datetime.combine(task.start_date, task.start_time)
        end = datetime.combine(task.end_date, task.end_time)
        body["start"] = {"dateTime": start.isoformat(), "timeZone": timezone_name}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": timezone_name}
    return body


def _event_schedule(
    event: dict[str, Any], timezone: ZoneInfo
) -> tuple[date, date, time | None, time | None, bool] | None:
    start = event.get("start") or {}
    end = event.get("end") or {}
    if start.get("date"):
        try:
            start_date = date.fromisoformat(str(start["date"]))
            end_date = date.fromisoformat(str(end["date"])) - timedelta(days=1) if end.get("date") else start_date
        except ValueError:
            return None
        if end_date < start_date:
            return None
        return start_date, end_date, None, None, True

    if start.get("dateTime"):
        try:
            start_moment = datetime.fromisoformat(str(start["dateTime"]).replace("Z", "+00:00"))
        except ValueError:
            return None
        if start_moment.tzinfo is None:
            start_moment = start_moment.replace(tzinfo=timezone)
        start_local = start_moment.astimezone(timezone)
        if end.get("dateTime"):
            try:
                end_moment = datetime.fromisoformat(str(end["dateTime"]).replace("Z", "+00:00"))
            except ValueError:
                return None
            if end_moment.tzinfo is None:
                end_moment = end_moment.replace(tzinfo=timezone)
            end_local = end_moment.astimezone(timezone)
        else:
            end_local = start_local + timedelta(minutes=DEFAULT_DURATION_MINUTES)
        if end_local <= start_local:
            return None
        return (
            start_local.date(),
            end_local.date(),
            start_local.time().replace(second=0, microsecond=0, tzinfo=None),
            end_local.time().replace(second=0, microsecond=0, tzinfo=None),
            False,
        )
    return None


def _enum_or(value: Any, enum_type, fallback):
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return fallback


def event_to_task(
    event: dict[str, Any],
    timezone: ZoneInfo,
    existing: Task | None = None,
) -> Task | None:
    """Converte um evento em tarefa, preservando o que o Google não guarda."""
    schedule = _event_schedule(event, timezone)
    if schedule is None:
        return None
    start_date, end_date, start_time, end_time, all_day = schedule

    summary = str(event.get("summary") or "").strip() or "Evento da agenda"
    completed_marker = summary.startswith(COMPLETED_PREFIX)
    if completed_marker:
        summary = summary[len(COMPLETED_PREFIX):].strip()

    properties = (event.get("extendedProperties") or {}).get("private") or {}
    default_status = Status.COMPLETED if completed_marker else Status.PENDING
    now = datetime.now()
    return Task(
        id=existing.id if existing else None,
        title=summary,
        description=str(event.get("description") or "").strip(),
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        all_day=all_day,
        priority=_enum_or(
            properties.get("dukielistPriority"),
            Priority,
            existing.priority if existing else Priority.MEDIUM,
        ),
        category=_enum_or(
            properties.get("dukielistCategory"),
            Category,
            existing.category if existing else Category.PERSONAL,
        ),
        status=_enum_or(properties.get("dukielistStatus"), Status, default_status),
        view_mode=_enum_or(
            properties.get("dukielistViewMode"),
            ViewMode,
            existing.view_mode if existing else ViewMode.DAY,
        ),
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )


@contextmanager
def _sync_guard(storage: SQLiteStorage) -> Iterator[None]:
    """Serializa o tráfego de várias instâncias que compartilham o banco."""
    lock_path = storage.db_path.with_name(f"{storage.db_path.name}.google-sync.lock")
    try:
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise GoogleCalendarError(
            "Não foi possível coordenar a sincronização com o Google Calendar."
        ) from exc


def _push_upsert(
    storage: SQLiteStorage,
    client: GoogleCalendarClient,
    entry: dict[str, Any],
    timezone_name: str,
) -> str:
    task_id = int(entry["task_id"])
    task = storage.get(task_id)
    if task is None:
        return "skipped"

    body = task_to_event(task, timezone_name)
    external_id = storage.external_id_for_task(SOURCE, task_id)
    outcome = "updated"
    if external_id:
        try:
            event = client.patch_event(external_id, body)
        except GoogleEventUnavailableError:
            # Apagado direto na agenda: recria em vez de perder a tarefa.
            storage.unlink_external(SOURCE, external_id)
            event = client.insert_event(body)
            outcome = "created"
    else:
        event = client.insert_event(body)
        outcome = "created"

    storage.link_external(
        source=SOURCE,
        external_id=str(event["id"]),
        task_id=task_id,
        external_updated_at=str(event.get("updated") or ""),
        external_url=str(event.get("htmlLink") or "") or None,
    )
    return outcome


def _push_delete(
    storage: SQLiteStorage,
    client: GoogleCalendarClient,
    entry: dict[str, Any],
) -> str:
    external_id = entry.get("external_id")
    if not external_id:
        return "skipped"
    try:
        client.delete_event(str(external_id))
    except GoogleEventUnavailableError:
        # Já não está lá: o objetivo da fila foi atingido do mesmo jeito.
        return "deleted"
    return "deleted"


def push_pending(
    storage: SQLiteStorage,
    client: GoogleCalendarClient,
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> SyncStats:
    """Envia as alterações locais enfileiradas, na ordem em que aconteceram."""
    counts = {"created": 0, "updated": 0, "deleted": 0, "skipped": 0, "failed": 0}
    for entry in storage.pending_outbox(SOURCE):
        try:
            if entry["operation"] == "delete":
                outcome = _push_delete(storage, client, entry)
            else:
                outcome = _push_upsert(storage, client, entry, timezone_name)
        except GoogleEventUnavailableError:
            counts["failed"] += 1
            continue
        # Uma edição feita durante a chamada mantém o item na fila para a
        # próxima rodada, em vez de ser descartada aqui.
        storage.resolve_outbox(int(entry["id"]), created_at=str(entry["created_at"]))
        counts[outcome] += 1
    return SyncStats(
        created_remote=counts["created"],
        updated_remote=counts["updated"],
        deleted_remote=counts["deleted"],
        failed_remote=counts["failed"],
        skipped=counts["skipped"],
    )


def _pull_events(
    storage: SQLiteStorage,
    client: GoogleCalendarClient,
    *,
    start: date,
    end: date,
    timezone: ZoneInfo,
) -> dict[str, int]:
    counts = {"fetched": 0, "created": 0, "updated": 0, "unchanged": 0, "deleted": 0, "skipped": 0}
    events = client.list_events(start, end, timezone)
    counts["fetched"] = len(events)

    for event in events:
        external_id = str(event.get("id") or "")
        if not external_id:
            counts["skipped"] += 1
            continue

        linked_task_id = storage.external_task_id(SOURCE, external_id)
        if event.get("status") == "cancelled":
            if linked_task_id is not None:
                # Desfaz o vínculo antes de apagar para não enfileirar de volta
                # uma exclusão de evento que o Google já removeu.
                storage.unlink_external(SOURCE, external_id)
                storage.delete(linked_task_id)
                counts["deleted"] += 1
            continue

        existing = storage.get(linked_task_id) if linked_task_id is not None else None
        task = event_to_task(event, timezone, existing)
        if task is None:
            counts["skipped"] += 1
            continue

        result = storage.sync_external(
            task,
            source=SOURCE,
            external_id=external_id,
            external_updated_at=str(event.get("updated") or ""),
            external_url=str(event.get("htmlLink") or "") or None,
        )
        counts[result] += 1
    return counts


def _sync(
    storage: SQLiteStorage,
    client: GoogleCalendarClient,
    *,
    start: date | None,
    end: date | None,
    timezone_name: str,
    outbound_only: bool,
) -> SyncStats:
    timezone = _timezone(timezone_name)
    if outbound_only:
        return push_pending(storage, client, timezone_name=timezone_name)

    today = datetime.now(timezone).date()
    start = start or today - timedelta(days=PULL_DAYS_BACK)
    end = end or today + timedelta(days=PULL_DAYS_AHEAD)
    if end <= start:
        raise GoogleCalendarError("A data final precisa ser posterior à inicial.")

    if storage.get_setting(INITIAL_SYNC_SETTING_KEY) != client.calendar_id:
        # Primeiro vincula os eventos existentes. Assim, os eventos importados
        # não voltam ao Google como novas tarefas durante o envio inicial.
        pulled = _pull_events(storage, client, start=start, end=end, timezone=timezone)
        storage.enqueue_unlinked_tasks(SOURCE)
        pushed = push_pending(storage, client, timezone_name=timezone_name)
        storage.set_setting(INITIAL_SYNC_SETTING_KEY, client.calendar_id)
    else:
        pushed = push_pending(storage, client, timezone_name=timezone_name)
        pulled = _pull_events(storage, client, start=start, end=end, timezone=timezone)
    return replace(
        pushed,
        fetched=pulled["fetched"],
        created_local=pulled["created"],
        updated_local=pulled["updated"],
        unchanged_local=pulled["unchanged"],
        deleted_local=pulled["deleted"],
        skipped=pushed.skipped + pulled["skipped"],
    )


def sync_google_calendar(
    storage: SQLiteStorage,
    client: GoogleCalendarClient,
    *,
    start: date | None = None,
    end: date | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    outbound_only: bool = False,
) -> SyncStats:
    """Envia o que mudou na DukieList e traz o que mudou na agenda."""
    with _sync_guard(storage):
        return _sync(
            storage,
            client,
            start=start,
            end=end,
            timezone_name=timezone_name,
            outbound_only=outbound_only,
        )
