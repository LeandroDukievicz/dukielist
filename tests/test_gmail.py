import base64
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from dukielist.gmail import GmailClient, GmailError, _html_to_text, parse_message


def encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


class _Call:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _Messages:
    def __init__(self, messages):
        self.messages = messages

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return _Call({"messages": [{"id": item} for item in self.messages]})

    def get(self, **kwargs):
        self.get_kwargs = kwargs
        return _Call(self.messages[kwargs["id"]])


class _Users:
    def __init__(self, messages):
        self.resource = _Messages(messages)

    def messages(self):
        return self.resource


class _Service:
    def __init__(self, messages):
        self.resource = _Users(messages)

    def users(self):
        return self.resource


class _Batch:
    def __init__(self, callback):
        self.callback = callback
        self.requests = []

    def add(self, request, request_id):
        self.requests.append((request_id, request))

    def execute(self):
        for request_id, request in self.requests:
            self.callback(request_id, request.execute(), None)


class _BatchService(_Service):
    def new_batch_http_request(self, callback):
        self.batch = _Batch(callback)
        return self.batch


def raw_message(message_id="m1", *, body=None, mime_type="text/plain"):
    payload = {
        "mimeType": mime_type,
        "headers": [
            {"name": "From", "value": "Dukie Bot <bot@example.com>"},
            {"name": "To", "value": "leandro@example.com"},
            {"name": "Subject", "value": "Teste Gmail"},
        ],
        "body": {},
    }
    if body is not None:
        payload["body"]["data"] = encoded(body)
    return {
        "id": message_id,
        "threadId": "thread-1",
        "internalDate": "1767225600000",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "Prévia &amp; segura",
        "payload": payload,
    }


class GmailParsingTest(unittest.TestCase):
    def test_parses_metadata_without_downloading_body(self):
        message = parse_message(raw_message(body="conteúdo"))
        self.assertEqual(message.sender, "Dukie Bot")
        self.assertEqual(message.sender_address, "bot@example.com")
        self.assertEqual(message.subject, "Teste Gmail")
        self.assertEqual(message.snippet, "Prévia & segura")
        self.assertTrue(message.unread)
        self.assertEqual(message.body, "")
        self.assertIsInstance(message.received_at, datetime)

    def test_reads_plain_text_and_falls_back_to_sanitized_html(self):
        plain = parse_message(raw_message(body="Olá, Leandro!"), include_body=True)
        self.assertEqual(plain.body, "Olá, Leandro!")

        html_message = raw_message(body="<p>Olá <b>mundo</b></p><script>ruim()</script>", mime_type="text/html")
        parsed_html = parse_message(html_message, include_body=True)
        self.assertEqual(parsed_html.body, "Olá mundo")
        self.assertEqual(_html_to_text("<div>A</div><div>B&nbsp;C</div>"), "A\nB C")

    def test_prefers_plain_text_in_multipart_message(self):
        message = raw_message()
        message["payload"] = {
            "mimeType": "multipart/alternative",
            "headers": message["payload"]["headers"],
            "parts": [
                {"mimeType": "text/html", "body": {"data": encoded("<b>HTML</b>")}},
                {"mimeType": "text/plain", "body": {"data": encoded("Texto puro")}},
            ],
        }
        self.assertEqual(parse_message(message, include_body=True).body, "Texto puro")


class GmailClientTest(unittest.TestCase):
    def test_lists_only_inbox_and_fetches_message_on_demand(self):
        service = _Service({"m1": raw_message(body="Corpo")})
        client = GmailClient(service=service)

        inbox = client.list_inbox(limit=30)

        self.assertEqual([item.id for item in inbox], ["m1"])
        messages_resource = service.resource.resource
        self.assertEqual(messages_resource.list_kwargs["labelIds"], ["INBOX"])
        self.assertEqual(messages_resource.get_kwargs["format"], "metadata")

        opened = client.get_message("m1")
        self.assertEqual(opened.body, "Corpo")
        self.assertEqual(messages_resource.get_kwargs["format"], "full")

    def test_uses_one_batch_for_inbox_metadata(self):
        service = _BatchService({
            "m1": raw_message("m1"),
            "m2": raw_message("m2"),
        })

        inbox = GmailClient(service=service).list_inbox(limit=30)

        self.assertEqual([message.id for message in inbox], ["m1", "m2"])
        self.assertEqual(len(service.batch.requests), 2)

    def test_missing_oauth_file_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "credentials.json"
            client = GmailClient(
                credentials_file=expected,
                token_file=root / "gmail-token.json",
            )
            with self.assertRaisesRegex(GmailError, str(expected)):
                client._credentials()


if __name__ == "__main__":
    unittest.main()
