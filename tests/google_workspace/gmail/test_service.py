"""Tests for bounded, read-only Gmail seams."""

from __future__ import annotations

import pytest

from app.google_workspace.gmail.exceptions import GmailInvalidRequestError, GmailNotFoundError
from app.google_workspace.gmail.service import GmailService


class FakeRequest:
    def __init__(self, value: object) -> None:
        self.value = value

    def execute(self) -> object:
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeMessages:
    def list(self, **kwargs: object) -> FakeRequest:
        return FakeRequest({"messages": [{"id": "m1"}], "nextPageToken": "more"})

    def get(self, **kwargs: object) -> FakeRequest:
        return FakeRequest({
            "id": "m1", "threadId": "t1", "snippet": "x" * 250, "labelIds": ["INBOX"],
            "payload": {"headers": [
                {"name": "Subject", "value": "Quarterly update"},
                {"name": "From", "value": "person@example.com"},
                {"name": "Date", "value": "Tue, 01 Jan 2024 00:00:00 +0000"},
            ], "parts": [{"filename": "report.pdf"}]},
        })


class FakeUsers:
    def messages(self) -> FakeMessages:
        return FakeMessages()


class FakeFactory:
    def get_service(self, name: str, version: str) -> object:
        assert (name, version) == ("gmail", "v1")
        return type("Service", (), {"users": lambda self: FakeUsers()})()


def test_search_messages_returns_bounded_safe_metadata() -> None:
    result = GmailService(FakeFactory()).search_messages("from:person@example.com", max_results=5)
    assert result.truncated is True
    assert result.messages[0].sender_alias.startswith("sender_")
    assert "person@example.com" not in result.messages[0].sender_alias
    assert len(result.messages[0].snippet) == 200
    assert result.messages[0].has_attachments is True


@pytest.mark.parametrize("query", ["", "x" * 121, "has:attachment; rm -rf /"])
def test_search_messages_rejects_unsafe_queries_before_provider(query: str) -> None:
    with pytest.raises(GmailInvalidRequestError):
        GmailService(FakeFactory()).search_messages(query)


def test_provider_not_found_is_normalized() -> None:
    class MissingMessages(FakeMessages):
        def get(self, **kwargs: object) -> FakeRequest:
            error = RuntimeError("raw provider detail")
            error.resp = type("Response", (), {"status": 404})()
            return FakeRequest(error)

    class MissingUsers(FakeUsers):
        def messages(self) -> MissingMessages:
            return MissingMessages()

    class MissingFactory(FakeFactory):
        def get_service(self, name: str, version: str) -> object:
            return type("Service", (), {"users": lambda self: MissingUsers()})()

    with pytest.raises(GmailNotFoundError) as error:
        GmailService(MissingFactory()).get_message_metadata("m1")
    assert "raw provider detail" not in str(error.value)


class FakeThreads:
    def get(self, **kwargs: object) -> FakeRequest:
        return FakeRequest({
            "id": "t1",
            "messages": [
                {
                    "id": "m1", "threadId": "t1", "snippet": "hello", "labelIds": ["INBOX"],
                    "payload": {"headers": [
                        {"name": "Subject", "value": "Re: hi"},
                        {"name": "From", "value": "a@example.com"},
                        {"name": "Date", "value": "Tue, 01 Jan 2024 00:00:00 +0000"},
                    ], "parts": []},
                },
                {
                    "id": "m2", "threadId": "t1", "snippet": "reply", "labelIds": ["INBOX"],
                    "payload": {"headers": [
                        {"name": "Subject", "value": "Re: hi"},
                        {"name": "From", "value": "b@example.com"},
                        {"name": "Date", "value": "Tue, 01 Jan 2024 01:00:00 +0000"},
                    ], "parts": []},
                },
            ],
        })


class ThreadUsers(FakeUsers):
    def threads(self) -> FakeThreads:
        return FakeThreads()


class ThreadFactory(FakeFactory):
    def get_service(self, name: str, version: str) -> object:
        assert (name, version) == ("gmail", "v1")
        return type("Service", (), {"users": lambda self: ThreadUsers()})()


def test_list_thread_returns_bounded_message_summaries() -> None:
    result = GmailService(ThreadFactory()).list_thread("t1")
    assert result.thread_id == "t1"
    assert result.message_count == 2
    assert [message.message_id for message in result.messages] == ["m1", "m2"]
    assert "a@example.com" not in result.messages[0].sender_alias


@pytest.mark.parametrize("thread_id", ["", "../unsafe", "x" * 201])
def test_list_thread_rejects_unsafe_identifiers(thread_id: str) -> None:
    with pytest.raises(GmailInvalidRequestError):
        GmailService(FakeFactory()).list_thread(thread_id)
