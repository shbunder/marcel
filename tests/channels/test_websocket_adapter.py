"""Tests for WebSocket channel adapter."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from marcel_core.channels.websocket import WebSocketAdapter


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket connection."""
    ws = MagicMock()
    ws.send_text = AsyncMock()
    return ws


@pytest.fixture
def adapter(mock_websocket):
    """Create a WebSocket adapter with mock connection."""
    return WebSocketAdapter(mock_websocket)


def test_capabilities(adapter):
    """Test that WebSocket declares correct capabilities."""
    caps = adapter.capabilities
    assert caps.markdown is True
    assert caps.streaming is True
    assert caps.rich_ui is True
    assert caps.progress_updates is True
    assert caps.attachments is False


@pytest.mark.asyncio
async def test_send_text_delta(adapter, mock_websocket):
    """Test sending text delta."""
    await adapter.send_text_delta('Hello ')

    mock_websocket.send_text.assert_called_once()
    sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
    assert sent_data['type'] == 'token'
    assert sent_data['text'] == 'Hello '


@pytest.mark.asyncio
async def test_send_tool_call_started(adapter, mock_websocket):
    """Test sending tool call start event."""
    await adapter.send_tool_call_started('tc-123', 'bash')

    mock_websocket.send_text.assert_called_once()
    sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
    assert sent_data['type'] == 'tool_call_start'
    assert sent_data['tool_call_id'] == 'tc-123'
    assert sent_data['tool_name'] == 'bash'


@pytest.mark.asyncio
async def test_send_tool_call_completed(adapter, mock_websocket):
    """Test sending tool call completion events."""
    await adapter.send_tool_call_completed('tc-123', 'bash', 'Command output', False)

    # Should send two messages: end and result
    assert mock_websocket.send_text.call_count == 2

    # Check end event
    end_data = json.loads(mock_websocket.send_text.call_args_list[0][0][0])
    assert end_data['type'] == 'tool_call_end'
    assert end_data['tool_call_id'] == 'tc-123'

    # Check result event
    result_data = json.loads(mock_websocket.send_text.call_args_list[1][0][0])
    assert result_data['type'] == 'tool_call_result'
    assert result_data['tool_call_id'] == 'tc-123'
    assert result_data['tool_name'] == 'bash'
    assert result_data['is_error'] is False


@pytest.mark.asyncio
async def test_send_tool_call_completed_with_error(adapter, mock_websocket):
    """Test sending tool call with error flag."""
    await adapter.send_tool_call_completed('tc-456', 'git_push', 'Error: permission denied', True)

    result_data = json.loads(mock_websocket.send_text.call_args_list[1][0][0])
    assert result_data['is_error'] is True


@pytest.mark.asyncio
async def test_send_run_finished(adapter, mock_websocket):
    """Test sending run finished event."""
    await adapter.send_run_finished(0.025, False)

    mock_websocket.send_text.assert_called_once()
    sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
    assert sent_data['type'] == 'done'
    assert sent_data['cost_usd'] == 0.025
    assert 'is_error' not in sent_data


@pytest.mark.asyncio
async def test_send_run_finished_with_error(adapter, mock_websocket):
    """Test sending run finished with error flag."""
    await adapter.send_run_finished(None, True)

    sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
    assert sent_data['type'] == 'done'
    assert sent_data['is_error'] is True
    assert 'cost_usd' not in sent_data


@pytest.mark.asyncio
async def test_send_error(adapter, mock_websocket):
    """Test sending error message."""
    await adapter.send_error('Something went wrong')

    sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
    assert sent_data['type'] == 'error'
    assert sent_data['message'] == 'Something went wrong'


@pytest.mark.asyncio
async def test_send_conversation_started(adapter, mock_websocket):
    """Test sending conversation started event."""
    await adapter.send_conversation_started('conv-123')

    sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
    assert sent_data['type'] == 'started'
    assert sent_data['conversation'] == 'conv-123'


@pytest.mark.asyncio
async def test_send_text_message_boundaries(adapter, mock_websocket):
    """Test sending text message start/end events."""
    await adapter.send_text_message_start()
    start_data = json.loads(mock_websocket.send_text.call_args[0][0])
    assert start_data['type'] == 'text_message_start'

    await adapter.send_text_message_end()
    end_data = json.loads(mock_websocket.send_text.call_args[0][0])
    assert end_data['type'] == 'text_message_end'


def test_format_text(adapter):
    """Test text formatting (passthrough for WebSocket)."""
    text = 'Hello **world**'
    formatted = adapter.format_text(text)
    assert formatted == text  # No transformation for WebSocket
