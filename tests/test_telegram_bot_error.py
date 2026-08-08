import logging
import asyncio
from app.telegram_bot import handle_error

class MockContext:
    def __init__(self, error=None):
        self.error = error

def test_handle_error_logs_exception_type_and_message(caplog):
    caplog.set_level(logging.ERROR)

    class SafeError(Exception):
        pass

    test_error = SafeError("Database connection failed for user id 1")
    context = MockContext(error=test_error)

    asyncio.run(handle_error(update={"user_id": 1}, context=context))

    assert "Unhandled Telegram bot error" in caplog.text
    assert "SafeError" in caplog.text
    assert "Database connection failed for user id 1" in caplog.text

def test_handle_error_redacts_sensitive_content(caplog):
    caplog.set_level(logging.ERROR)

    class SecretLeakError(Exception):
        pass

    test_error = SecretLeakError("Connection failed but here is my secret token: XYZ")
    context = MockContext(error=test_error)

    asyncio.run(handle_error(update={"secret_update_payload": True}, context=context))

    assert "Unhandled Telegram bot error (SecretLeakError): [REDACTED SENSITIVE CONTENT]" in caplog.text
    assert "XYZ" not in caplog.text

def test_handle_error_handles_missing_context(caplog):
    caplog.set_level(logging.ERROR)

    asyncio.run(handle_error(update=None, context=None))

    assert "Unhandled Telegram bot error." in caplog.text

def test_handle_error_handles_missing_error_in_context(caplog):
    caplog.set_level(logging.ERROR)

    context = MockContext(error=None)

    asyncio.run(handle_error(update=None, context=context))

    assert "Unhandled Telegram bot error." in caplog.text


def test_handle_error_handles_malformed_context_without_error_attribute(caplog):
    """Regression: a context-like object with no `.error` attribute at all
    (not just None) must never raise AttributeError out of the handler --
    this is the bot's last-resort error handler."""
    caplog.set_level(logging.ERROR)

    class MalformedContext:
        pass

    asyncio.run(handle_error(update=None, context=MalformedContext()))

    assert "Unhandled Telegram bot error." in caplog.text


def test_handle_error_handles_wrong_type_context(caplog):
    """A context that isn't even object-shaped (e.g. a bare string) must
    still fail safe rather than raise."""
    caplog.set_level(logging.ERROR)

    asyncio.run(handle_error(update=None, context="not-a-context"))

    assert "Unhandled Telegram bot error." in caplog.text


def test_handle_error_survives_exception_whose_str_raises(caplog):
    """Regression: an exception whose own __str__ raises must not crash the
    handler or leak a traceback of the secondary failure."""
    caplog.set_level(logging.ERROR)

    class UnprintableError(Exception):
        def __str__(self):
            raise RuntimeError("cannot stringify me")

    context = MockContext(error=UnprintableError())

    asyncio.run(handle_error(update=None, context=context))

    assert "Unhandled Telegram bot error (UnprintableError)" in caplog.text
    assert "cannot stringify me" not in caplog.text


def test_handle_error_redacts_bare_token_without_secret(caplog):
    caplog.set_level(logging.ERROR)

    class TokenError(Exception):
        pass

    context = MockContext(error=TokenError("expired token abc123"))

    asyncio.run(handle_error(update=None, context=context))

    assert "[REDACTED SENSITIVE CONTENT]" in caplog.text
    assert "abc123" not in caplog.text


def test_handle_error_redacts_bare_secret_without_token(caplog):
    caplog.set_level(logging.ERROR)

    class SecretError(Exception):
        pass

    context = MockContext(error=SecretError("could not read secret value XYZ"))

    asyncio.run(handle_error(update=None, context=context))

    assert "[REDACTED SENSITIVE CONTENT]" in caplog.text
    assert "XYZ" not in caplog.text


def test_handle_error_redacts_api_key(caplog):
    caplog.set_level(logging.ERROR)

    class ApiKeyError(Exception):
        pass

    context = MockContext(error=ApiKeyError("request failed, api_key=sk-should-not-appear"))

    asyncio.run(handle_error(update=None, context=context))

    assert "[REDACTED SENSITIVE CONTENT]" in caplog.text
    assert "sk-should-not-appear" not in caplog.text


def test_handle_error_redacts_bearer_and_pem_shapes(caplog):
    """SENSITIVE_CONTENT_PATTERN (shared, codebase-wide) catches shapes a
    bare 'token'/'secret'/'key' keyword check would miss entirely."""
    caplog.set_level(logging.ERROR)

    class AuthError(Exception):
        pass

    context = MockContext(error=AuthError("failed with Authorization: Bearer abc.def.ghi"))
    asyncio.run(handle_error(update=None, context=context))
    assert "[REDACTED SENSITIVE CONTENT]" in caplog.text
    assert "abc.def.ghi" not in caplog.text


def test_handle_error_redaction_is_case_insensitive(caplog):
    caplog.set_level(logging.ERROR)

    class MixedCaseError(Exception):
        pass

    context = MockContext(error=MixedCaseError("Leaked SECRET value QWERTY"))

    asyncio.run(handle_error(update=None, context=context))

    assert "[REDACTED SENSITIVE CONTENT]" in caplog.text
    assert "QWERTY" not in caplog.text


def test_handle_error_does_not_over_redact_bare_key(caplog):
    """A bare 'key' mention (e.g. a routine SQL constraint error) has too
    many legitimate non-secret uses to redact on its own -- 'api key' /
    'private key' shapes are still caught via SENSITIVE_CONTENT_PATTERN."""
    caplog.set_level(logging.ERROR)

    class IntegrityError(Exception):
        pass

    context = MockContext(error=IntegrityError("duplicate primary key value violates constraint"))

    asyncio.run(handle_error(update=None, context=context))

    assert "[REDACTED SENSITIVE CONTENT]" not in caplog.text
    assert "duplicate primary key value violates constraint" in caplog.text


def test_handle_error_truncates_long_messages(caplog):
    caplog.set_level(logging.ERROR)

    class LongError(Exception):
        pass

    long_message = "x" * 1000
    context = MockContext(error=LongError(long_message))

    asyncio.run(handle_error(update=None, context=context))

    assert ("x" * 500) in caplog.text
    assert ("x" * 501) not in caplog.text
    assert "..." in caplog.text


def test_handle_error_neutralizes_control_characters(caplog):
    """A newline/control-character payload must never be able to forge a
    second, fake-looking log line."""
    caplog.set_level(logging.ERROR)

    class InjectionError(Exception):
        pass

    payload = "safe prefix\nERROR fake injected line\r\ntail\x07bell"
    context = MockContext(error=InjectionError(payload))

    asyncio.run(handle_error(update=None, context=context))

    # Inspect the single emitted record's own message text -- caplog.text
    # joins multiple records with its own newlines, which is not what is
    # being tested here (that a single record can't be split into two by
    # attacker-controlled content).
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "\n" not in message
    assert "\r" not in message
    assert "\x07" not in message
    assert "safe prefix" in message
    assert "fake injected line" in message  # content preserved, just neutralized


def test_handle_error_never_logs_update_payload(caplog):
    caplog.set_level(logging.ERROR)

    class SafeError(Exception):
        pass

    sensitive_update = {
        "chat_id": 918273645,
        "user": {"id": 5551234, "username": "prof_marker_ZZTOP"},
        "message": {"text": "UPDATE_PAYLOAD_MARKER_DO_NOT_LOG"},
    }
    context = MockContext(error=SafeError("safe message"))

    asyncio.run(handle_error(update=sensitive_update, context=context))

    assert "UPDATE_PAYLOAD_MARKER_DO_NOT_LOG" not in caplog.text
    assert "prof_marker_ZZTOP" not in caplog.text
    assert "918273645" not in caplog.text
