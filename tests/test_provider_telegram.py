"""Tests for the provider Telegram commands (/ask, /providerstatus)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from telegram import Update, Message, User, Chat
from telegram.ext import ContextTypes

from app.telegram_bot import ask_command, providerstatus_command
from app.providers.service import ProviderGatewayService
from app.providers.errors import ProviderError
from app.security import is_authorized_user

AUTHORIZED_USER = 111
UNAUTHORIZED_USER = 999

@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def mock_context():
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.application.bot_data = {
        "settings": MagicMock(telegram_allowed_user_id=AUTHORIZED_USER),
        "provider": None
    }
    context.args = []
    return context

@pytest.fixture
def mock_update():
    update = MagicMock(spec=Update)
    message = AsyncMock(spec=Message)
    user = MagicMock(spec=User)
    user.id = AUTHORIZED_USER

    update.effective_message = message
    update.effective_user = user
    return update

@pytest.mark.anyio
async def test_ask_requires_authorization(mock_update, mock_context):
    mock_update.effective_user.id = UNAUTHORIZED_USER
    await ask_command(mock_update, mock_context)
    mock_update.effective_message.reply_text.assert_called_once()
    args = mock_update.effective_message.reply_text.call_args[0]
    assert "Akses ditolak" in args[0]

@pytest.mark.anyio
async def test_ask_usage(mock_update, mock_context):
    mock_context.args = []
    await ask_command(mock_update, mock_context)
    mock_update.effective_message.reply_text.assert_called_once()
    assert "Usage: /ask" in mock_update.effective_message.reply_text.call_args[0][0]

@pytest.mark.anyio
async def test_ask_no_provider_configured(mock_update, mock_context):
    mock_context.args = ["hello"]
    mock_context.application.bot_data["provider"] = None
    await ask_command(mock_update, mock_context)
    assert "belum dikonfigurasi" in mock_update.effective_message.reply_text.call_args[0][0]

@pytest.mark.anyio
async def test_ask_success(mock_update, mock_context):
    mock_context.args = ["hello"]
    provider = AsyncMock(spec=ProviderGatewayService)
    provider.generate_text.return_value = "World"
    mock_context.application.bot_data["provider"] = provider

    await ask_command(mock_update, mock_context)

    provider.generate_text.assert_called_once_with("hello", AUTHORIZED_USER)
    mock_update.effective_message.reply_text.assert_called_with("World")

@pytest.mark.anyio
async def test_ask_provider_error(mock_update, mock_context):
    mock_context.args = ["hello"]
    provider = AsyncMock(spec=ProviderGatewayService)
    provider.generate_text.side_effect = ProviderError("Something failed")
    mock_context.application.bot_data["provider"] = provider

    await ask_command(mock_update, mock_context)

    assert "Gagal: Something failed" in mock_update.effective_message.reply_text.call_args[0][0]

@pytest.mark.anyio
async def test_providerstatus_requires_authorization(mock_update, mock_context):
    mock_update.effective_user.id = UNAUTHORIZED_USER
    await providerstatus_command(mock_update, mock_context)
    assert "Akses ditolak" in mock_update.effective_message.reply_text.call_args[0][0]

@pytest.mark.anyio
async def test_providerstatus_no_provider(mock_update, mock_context):
    await providerstatus_command(mock_update, mock_context)
    assert "Not Configured" in mock_update.effective_message.reply_text.call_args[0][0]

@pytest.mark.anyio
async def test_providerstatus_success_and_redaction(mock_update, mock_context):
    provider = MagicMock(spec=ProviderGatewayService)
    provider.base_url = "https://secret.com/api"
    mock_cb = MagicMock()
    mock_cb.get_state.return_value = "closed"
    provider.circuit_breaker = mock_cb
    provider.model_priority = ["nova-v1", "nova-v2-preview", "nova-v1-fallback"]
    provider.allowed_models = ["nova-v1", "nova-v2-preview", "nova-v1-fallback"]
    provider.combo_priorities = {"generic": ["nova-v1", "nova-v2-preview", "nova-v1-fallback"]}
    provider.last_successful_model = "nova-v1"
    provider.last_fallback_reason = "timeout_error"

    ninerouter_adapter = MagicMock()
    ninerouter_adapter.is_available.return_value = True

    def _get_adapter(provider_id):
        return ninerouter_adapter if provider_id == "9Router" else None

    provider._get_adapter = MagicMock(side_effect=_get_adapter)
    mock_context.application.bot_data["provider"] = provider

    await providerstatus_command(mock_update, mock_context)

    response = mock_update.effective_message.reply_text.call_args[0][0]
    assert "https://***" in response
    assert "secret.com" not in response
    assert "CLOSED" in response
    assert "Most Recent Successful Model: nova-v1" in response
    assert "Last Fallback Reason: timeout_error" in response
    assert "nova-v1, nova-v2-preview, nova-v1-fallback" in response
    assert "nova-v2-preview: disabled" in response
    assert "Codex: not configured (stub inactive)" in response
    assert "Claude: not configured (stub inactive)" in response
