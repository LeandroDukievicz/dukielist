"""Read-only Gmail integration for DukieList.

OAuth credentials and tokens deliberately live outside the repository and the
SQLite task database.  The only requested permission is ``gmail.readonly``.
"""

from __future__ import annotations

import base64
import fcntl
import html
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SCOPES = (GMAIL_READONLY_SCOPE,)
DEFAULT_GOOGLE_CONFIG_DIR = Path.home() / ".config" / "dukielist" / "google"
DEFAULT_CREDENTIALS_PATH = DEFAULT_GOOGLE_CONFIG_DIR / "credentials.json"
DEFAULT_TOKEN_PATH = DEFAULT_GOOGLE_CONFIG_DIR / "gmail-token.json"
MAX_BODY_CHARS = 200_000


class GmailError(RuntimeError):
    """A Gmail configuration, authentication or API error safe to show."""


@dataclass(frozen=True, slots=True)
class GmailMessage:
    id: str
    thread_id: str
    sender: str
    sender_address: str
    subject: str
    received_at: datetime | None
    snippet: str
    unread: bool
    recipient: str = ""
    body: str = ""

    @property
    def received_label(self) -> str:
        if self.received_at is None:
            return "—"
        return self.received_at.astimezone().strftime("%d/%m %H:%M")


def _configured_path(variable: str, default: Path) -> Path:
    value = os.environ.get(variable)
    return Path(value).expanduser() if value else default


def credentials_path() -> Path:
    return _configured_path("DUKIELIST_GOOGLE_CREDENTIALS", DEFAULT_CREDENTIALS_PATH)


def token_path() -> Path:
    return _configured_path("DUKIELIST_GMAIL_TOKEN", DEFAULT_TOKEN_PATH)


def _header(payload: dict[str, Any], name: str) -> str:
    expected = name.casefold()
    for item in payload.get("headers", []):
        if str(item.get("name", "")).casefold() == expected:
            value = str(item.get("value", "")).strip()
            try:
                return str(make_header(decode_header(value)))
            except (LookupError, UnicodeError):
                return value
    return ""


def _display_sender(raw_sender: str) -> tuple[str, str]:
    name, address = parseaddr(raw_sender)
    address = address.strip()
    return (name.strip() or address or "Remetente desconhecido", address)


def _received_at(message: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(message["internalDate"]) / 1000).astimezone()
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        return None


def _decode_body(data: str) -> str:
    try:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


class _HTMLTextExtractor(HTMLParser):
    BREAK_TAGS = {
        "br", "div", "p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "section", "article", "header", "footer",
    }
    IGNORED_TAGS = {"script", "style", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.IGNORED_TAGS:
            self.ignored_depth += 1
        elif tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.IGNORED_TAGS and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = html.unescape("".join(self.parts)).replace("\xa0", " ")
        raw = re.sub(r"\n[ \t]*\n+", "\n", raw)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        compact: list[str] = []
        for line in lines:
            if line or (compact and compact[-1]):
                compact.append(line)
        return "\n".join(compact).strip()


def _html_to_text(value: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        return re.sub(r"<[^>]+>", " ", value).strip()
    return parser.text()


def _mime_bodies(part: dict[str, Any]) -> Iterable[tuple[str, str]]:
    mime_type = str(part.get("mimeType", "")).lower()
    data = part.get("body", {}).get("data")
    if data and mime_type in {"text/plain", "text/html"}:
        yield mime_type, _decode_body(str(data))
    for child in part.get("parts", []) or []:
        yield from _mime_bodies(child)


def _message_body(payload: dict[str, Any]) -> str:
    bodies = list(_mime_bodies(payload))
    plain = next((body for mime, body in bodies if mime == "text/plain" and body.strip()), "")
    if plain:
        result = plain.strip()
    else:
        html_body = next((body for mime, body in bodies if mime == "text/html" and body.strip()), "")
        result = _html_to_text(html_body) if html_body else ""
    if len(result) > MAX_BODY_CHARS:
        return result[:MAX_BODY_CHARS].rstrip() + "\n\n[Mensagem truncada para proteger o terminal.]"
    return result


def parse_message(message: dict[str, Any], *, include_body: bool = False) -> GmailMessage:
    payload = message.get("payload", {})
    sender, sender_address = _display_sender(_header(payload, "From"))
    subject = _header(payload, "Subject") or "(sem assunto)"
    body = _message_body(payload) if include_body else ""
    if include_body and not body:
        body = "[Esta mensagem não possui conteúdo de texto exibível. Anexos não são baixados.]"
    return GmailMessage(
        id=str(message.get("id", "")),
        thread_id=str(message.get("threadId", "")),
        sender=sender,
        sender_address=sender_address,
        subject=subject,
        received_at=_received_at(message),
        snippet=html.unescape(str(message.get("snippet", ""))).strip(),
        unread="UNREAD" in (message.get("labelIds") or []),
        recipient=_header(payload, "To"),
        body=body,
    )


class GmailClient:
    """Small synchronous client; callers should run it outside the UI thread."""

    def __init__(
        self,
        *,
        credentials_file: Path | None = None,
        token_file: Path | None = None,
        service: Any | None = None,
    ) -> None:
        self.credentials_file = credentials_file or credentials_path()
        self.token_file = token_file or token_path()
        self._injected_service = service

    @contextmanager
    def _token_lock(self):
        """Serialize authorization/refresh when multiple DukieList instances run."""
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock_path = self.token_file.with_suffix(self.token_file.suffix + ".lock")
            with lock_path.open("a+", encoding="utf-8") as lock:
                lock_path.chmod(0o600)
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                yield
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            raise GmailError(f"Não foi possível proteger o token do Gmail: {exc}") from exc

    def _credentials(self) -> Credentials:
        with self._token_lock():
            return self._credentials_locked()

    def _credentials_locked(self) -> Credentials:
        credentials: Credentials | None = None
        if self.token_file.exists():
            try:
                credentials = Credentials.from_authorized_user_file(
                    str(self.token_file), GMAIL_SCOPES
                )
            except (ValueError, OSError, GoogleAuthError):
                credentials = None

        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except Exception:
                # A revoked/expired refresh token should not permanently brick
                # the integration. Remove it and start a clean OAuth flow.
                credentials = None
                try:
                    self.token_file.unlink(missing_ok=True)
                except OSError:
                    pass

        if not credentials or not credentials.valid:
            if not self.credentials_file.is_file():
                raise GmailError(
                    "Configuração do Gmail ausente. Baixe a credencial OAuth do tipo "
                    f"'App para computador' e salve em {self.credentials_file}"
                )
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_file), GMAIL_SCOPES
                )
                credentials = flow.run_local_server(
                    port=0,
                    open_browser=True,
                    authorization_prompt_message=(
                        "Autorize a leitura do Gmail na janela do navegador: {url}"
                    ),
                    success_message="Gmail autorizado. Você pode fechar esta janela.",
                )
            except Exception as exc:
                raise GmailError(f"Não foi possível autorizar o Gmail: {exc}") from exc

        self._save_token(credentials)
        return credentials

    def _save_token(self, credentials: Credentials) -> None:
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".gmail-token-", suffix=".tmp", dir=self.token_file.parent
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                    temporary.write(credentials.to_json())
                    temporary.flush()
                    os.fsync(temporary.fileno())
                temporary_path.chmod(0o600)
                temporary_path.replace(self.token_file)
            finally:
                temporary_path.unlink(missing_ok=True)
        except OSError as exc:
            raise GmailError(f"Não foi possível salvar o token do Gmail: {exc}") from exc

    def _service(self) -> Any:
        if self._injected_service is not None:
            return self._injected_service
        try:
            return build("gmail", "v1", credentials=self._credentials(), cache_discovery=False)
        except Exception as exc:
            raise GmailError(f"Não foi possível conectar ao Gmail: {exc}") from exc

    @staticmethod
    def _api_error(exc: HttpError) -> GmailError:
        status = getattr(exc.resp, "status", None)
        if status in {401, 403}:
            return GmailError(
                "O Google recusou o acesso. Verifique se a API Gmail está habilitada, "
                "se sua conta é usuária de teste e autorize novamente."
            )
        return GmailError(f"Falha ao consultar o Gmail: {exc}")

    def list_inbox(self, *, limit: int = 30) -> list[GmailMessage]:
        limit = max(1, min(limit, 100))
        service = self._service()
        try:
            response = service.users().messages().list(
                userId="me", labelIds=["INBOX"], maxResults=limit
            ).execute()
            references = response.get("messages", [])
            raw_messages: dict[str, dict[str, Any]] = {}
            resource = service.users().messages()

            # One HTTP batch is substantially faster than fetching 30 rows in
            # sequence. The sequential fallback keeps injected test clients and
            # compatible third-party transports working.
            if references and hasattr(service, "new_batch_http_request"):
                batch_errors: list[Exception] = []

                def collect(request_id, response, exception):
                    if exception is not None:
                        batch_errors.append(exception)
                    elif response is not None:
                        raw_messages[str(request_id)] = response

                batch = service.new_batch_http_request(callback=collect)
                for reference in references:
                    message_id = str(reference["id"])
                    batch.add(
                        resource.get(
                            userId="me",
                            id=message_id,
                            format="metadata",
                            metadataHeaders=["From", "Subject", "Date"],
                        ),
                        request_id=message_id,
                    )
                batch.execute()
                if batch_errors and not raw_messages:
                    raise batch_errors[0]
            else:
                for reference in references:
                    message_id = str(reference["id"])
                    raw_messages[message_id] = resource.get(
                        userId="me",
                        id=message_id,
                        format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    ).execute()

            return [
                parse_message(raw_messages[str(reference["id"])])
                for reference in references
                if str(reference["id"]) in raw_messages
            ]
        except HttpError as exc:
            raise self._api_error(exc) from exc
        except Exception as exc:
            raise GmailError(f"Falha de rede ao consultar o Gmail: {exc}") from exc

    def get_message(self, message_id: str) -> GmailMessage:
        service = self._service()
        try:
            raw = service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
            return parse_message(raw, include_body=True)
        except HttpError as exc:
            raise self._api_error(exc) from exc
        except Exception as exc:
            raise GmailError(f"Falha de rede ao abrir a mensagem: {exc}") from exc
