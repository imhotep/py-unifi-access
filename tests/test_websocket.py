"""Tests for unifi_access_api.websocket."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from unifi_access_api.websocket import (
    _BACKOFF_FACTOR,
    _BACKOFF_MAX,
    _BACKOFF_MIN,
    _WS_HEARTBEAT,
    UnifiAccessWebsocket,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ws(
    *,
    handlers: dict[str, Any] | None = None,
    on_connect: Any | None = None,
    on_disconnect: Any | None = None,
    reconnect_interval: int = _BACKOFF_MIN,
    max_retries: int | None = None,
) -> UnifiAccessWebsocket:
    return UnifiAccessWebsocket(
        uri="wss://192.168.1.1:12445/api/v1/developer/devices/notifications",
        headers={"Authorization": "Bearer test"},
        ssl_context=False,
        session=AsyncMock(spec=aiohttp.ClientSession),
        message_handlers=handlers or {},
        on_connect=on_connect,
        on_disconnect=on_disconnect,
        reconnect_interval=reconnect_interval,
        max_retries=max_retries,
    )


def _ws_msg(
    *, text: str | None = None, msg_type: aiohttp.WSMsgType | None = None
) -> MagicMock:
    """Build a single mock WS message."""
    m = MagicMock()
    if text is not None:
        m.type = aiohttp.WSMsgType.TEXT
        m.data = text
    elif msg_type is not None:
        m.type = msg_type
    else:
        m.type = aiohttp.WSMsgType.CLOSE
    return m


def _patch_ws_connect(ws: UnifiAccessWebsocket, messages: list[MagicMock]) -> MagicMock:
    """Wire session.ws_connect to yield *messages* via a proper async iterator."""

    async def _aiter() -> Any:
        for m in messages:
            yield m

    mock_conn = AsyncMock()
    mock_conn.__aiter__ = lambda self: _aiter()
    mock_conn.exception = MagicMock(return_value=None)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    ws._session.ws_connect = MagicMock(return_value=ctx)
    return mock_conn


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_initial_state(self) -> None:
        ws = _make_ws()
        assert ws.is_running is False
        assert ws.is_connected is False


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


class TestStartStop:
    async def test_start_creates_task(self) -> None:
        ws = _make_ws()
        _patch_ws_connect(ws, [_ws_msg()])  # immediate CLOSE
        ws.start()
        assert ws.is_running is True
        await ws.stop()

    async def test_start_twice_does_not_crash(self) -> None:
        ws = _make_ws()
        _patch_ws_connect(ws, [_ws_msg()])
        ws.start()
        ws.start()  # second call: warning logged, no crash
        await ws.stop()

    async def test_stop_without_start(self) -> None:
        ws = _make_ws()
        await ws.stop()  # no error


# ---------------------------------------------------------------------------
# _invoke helper
# ---------------------------------------------------------------------------


class TestInvoke:
    async def test_sync_callback(self) -> None:
        called: list[bool] = []
        await _make_ws()._invoke(lambda: called.append(True))
        assert called

    async def test_async_callback(self) -> None:
        called: list[bool] = []

        async def cb() -> None:
            called.append(True)

        await _make_ws()._invoke(cb)
        assert called

    async def test_none_is_noop(self) -> None:
        await _make_ws()._invoke(None)

    async def test_passes_args(self) -> None:
        captured: list[Any] = []
        await _make_ws()._invoke(lambda x, y: captured.extend([x, y]), 1, 2)
        assert captured == [1, 2]


# ---------------------------------------------------------------------------
# _handle_message
# ---------------------------------------------------------------------------


class TestHandleMessage:
    async def test_hello_ignored(self) -> None:
        handler = AsyncMock()
        ws = _make_ws(handlers={"test": handler})
        await ws._handle_message("Hello from UniFi Access")
        handler.assert_not_called()

    async def test_invalid_json(self) -> None:
        await _make_ws()._handle_message("not json{{{")

    async def test_non_dict_ignored(self) -> None:
        await _make_ws()._handle_message(json.dumps([1, 2, 3]))

    async def test_known_event_dispatched(self) -> None:
        handler = AsyncMock()
        ws = _make_ws(handlers={"access.data.device.update": handler})
        await ws._handle_message(
            json.dumps(
                {
                    "event": "access.data.device.update",
                    "data": {"unique_id": "x", "device_type": "y"},
                }
            )
        )
        handler.assert_awaited_once()
        assert handler.call_args[0][0].event == "access.data.device.update"

    async def test_unknown_event_no_crash(self) -> None:
        handler = AsyncMock()
        ws = _make_ws(handlers={"other": handler})
        await ws._handle_message(json.dumps({"event": "access.unknown"}))
        handler.assert_not_called()

    async def test_wildcard_handler_catches_unhandled(self) -> None:
        wildcard = AsyncMock()
        ws = _make_ws(handlers={"*": wildcard})
        await ws._handle_message(json.dumps({"event": "access.unknown", "data": {}}))
        wildcard.assert_awaited_once()
        assert wildcard.call_args[0][0].event == "access.unknown"

    async def test_specific_handler_preferred_over_wildcard(self) -> None:
        specific = AsyncMock()
        wildcard = AsyncMock()
        ws = _make_ws(handlers={"access.data.device.update": specific, "*": wildcard})
        await ws._handle_message(
            json.dumps({"event": "access.data.device.update", "data": {}})
        )
        specific.assert_awaited_once()
        wildcard.assert_not_called()

    async def test_sync_handler(self) -> None:
        captured: list[str] = []
        ws = _make_ws(
            handlers={
                "access.hw.door_bell": lambda msg: captured.append(msg.event),
            }
        )
        await ws._handle_message(
            json.dumps(
                {
                    "event": "access.hw.door_bell",
                    "data": {"door_id": "d1"},
                }
            )
        )
        assert captured == ["access.hw.door_bell"]


# ---------------------------------------------------------------------------
# _loop (integration-style)
# ---------------------------------------------------------------------------


class TestLoop:
    async def test_lifecycle_connect_message_disconnect(self) -> None:
        connect_ev = asyncio.Event()
        disconnect_ev = asyncio.Event()
        handler = AsyncMock()

        ws = _make_ws(
            handlers={"access.hw.door_bell": handler},
            on_connect=lambda: connect_ev.set(),
            on_disconnect=lambda: disconnect_ev.set(),
        )
        _patch_ws_connect(
            ws,
            [
                _ws_msg(
                    text=json.dumps(
                        {
                            "event": "access.hw.door_bell",
                            "data": {"door_id": "d1"},
                        }
                    )
                ),
                _ws_msg(),  # CLOSE
            ],
        )

        task = asyncio.create_task(ws._loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert connect_ev.is_set()
        assert disconnect_ev.is_set()
        handler.assert_awaited_once()

    async def test_connection_error_reconnects(self) -> None:
        ws = _make_ws(reconnect_interval=1)
        call_count = 0

        def side_effect(*_a: Any, **_kw: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise aiohttp.ClientError("fail")
            raise asyncio.CancelledError

        ws._session.ws_connect = MagicMock(side_effect=side_effect)

        with pytest.raises(asyncio.CancelledError):
            await ws._loop()

        assert call_count == 3

    async def test_handler_error_does_not_crash(self) -> None:
        async def bad(msg: Any) -> None:
            raise RuntimeError("boom")

        ws = _make_ws(handlers={"access.hw.door_bell": bad})
        _patch_ws_connect(
            ws,
            [
                _ws_msg(
                    text=json.dumps(
                        {
                            "event": "access.hw.door_bell",
                            "data": {"door_id": "d1"},
                        }
                    )
                ),
                _ws_msg(),  # CLOSE
            ],
        )

        task = asyncio.create_task(ws._loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_cancel_fires_disconnect(self) -> None:
        disconnect_ev = asyncio.Event()
        ws = _make_ws(on_disconnect=lambda: disconnect_ev.set())

        async def hang() -> Any:
            await asyncio.sleep(100)
            yield  # never reached  # pragma: no cover

        mock_conn = AsyncMock()
        mock_conn.__aiter__ = lambda self: hang()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        ws._session.ws_connect = MagicMock(return_value=ctx)

        task = asyncio.create_task(ws._loop())
        await asyncio.sleep(0.05)
        assert ws.is_connected
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert disconnect_ev.is_set()
        assert not ws.is_connected

    async def test_disconnect_not_fired_when_never_connected(self) -> None:
        count = 0

        def on_disc() -> None:
            nonlocal count
            count += 1

        ws = _make_ws(on_disconnect=on_disc, reconnect_interval=1)
        call_n = 0

        def side_effect(*_a: Any, **_kw: Any) -> Any:
            nonlocal call_n
            call_n += 1
            if call_n <= 2:
                raise aiohttp.ClientError("fail")
            raise asyncio.CancelledError

        ws._session.ws_connect = MagicMock(side_effect=side_effect)
        with pytest.raises(asyncio.CancelledError):
            await ws._loop()
        assert count == 0

    async def test_ws_error_breaks_inner_loop(self) -> None:
        ws = _make_ws()
        mock_conn = _patch_ws_connect(
            ws,
            [
                _ws_msg(msg_type=aiohttp.WSMsgType.ERROR),
            ],
        )
        mock_conn.exception = MagicMock(return_value=Exception("ws err"))

        task = asyncio.create_task(ws._loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_hello_skipped(self) -> None:
        handler = AsyncMock()
        ws = _make_ws(handlers={"access.hw.door_bell": handler})
        _patch_ws_connect(
            ws,
            [
                _ws_msg(text="Hello from UniFi Access"),
                _ws_msg(),
            ],
        )

        task = asyncio.create_task(ws._loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        handler.assert_not_called()


# ---------------------------------------------------------------------------
# Backoff constants
# ---------------------------------------------------------------------------


class TestBackoffConstants:
    def test_backoff_min(self) -> None:
        assert _BACKOFF_MIN == 1

    def test_backoff_max(self) -> None:
        assert _BACKOFF_MAX == 60

    def test_backoff_factor(self) -> None:
        assert _BACKOFF_FACTOR == 2

    def test_heartbeat(self) -> None:
        assert _WS_HEARTBEAT == 30

    def test_backoff_scales_correctly(self) -> None:
        """1 -> 2 -> 4 -> 8 -> 16 -> 32 -> 60 (capped)."""
        b = _BACKOFF_MIN
        expected = [1, 2, 4, 8, 16, 32, 60]
        actual = []
        for _ in expected:
            actual.append(b)
            b = min(b * _BACKOFF_FACTOR, _BACKOFF_MAX)
        assert actual == expected


# ---------------------------------------------------------------------------
# max_retries
# ---------------------------------------------------------------------------


class TestMaxRetries:
    async def test_max_retries_stops_loop(self) -> None:
        """Loop exits after max_retries reconnection attempts."""
        ws = _make_ws(reconnect_interval=0, max_retries=2)
        call_count = 0

        def side_effect(*_a: Any, **_kw: Any) -> Any:
            nonlocal call_count
            call_count += 1
            raise aiohttp.ClientError("fail")

        ws._session.ws_connect = MagicMock(side_effect=side_effect)
        await ws._loop()  # should exit, not hang
        # 1 initial + 2 retries = 3 total attempts
        assert call_count == 3

    async def test_unlimited_retries_default(self) -> None:
        """Default max_retries=None means unlimited (test by checking attribute)."""
        ws = _make_ws()
        assert ws._max_retries is None

    async def test_close_stops_reconnect(self) -> None:
        """close() cancels the task, preventing further reconnection."""
        ws = _make_ws(reconnect_interval=0)
        call_count = 0

        def side_effect(*_a: Any, **_kw: Any) -> Any:
            nonlocal call_count
            call_count += 1
            raise aiohttp.ClientError("fail")

        ws._session.ws_connect = MagicMock(side_effect=side_effect)
        ws.start()
        await asyncio.sleep(0.05)
        await ws.stop()
        assert not ws.is_running
        final_count = call_count
        await asyncio.sleep(0.05)
        assert call_count == final_count  # no more retries after stop

    async def test_handlers_work_after_reconnect(self) -> None:
        """Message handlers are re-used after a reconnection."""
        handler = AsyncMock()
        ws = _make_ws(handlers={"access.hw.door_bell": handler}, reconnect_interval=0)
        msg_payload = json.dumps(
            {"event": "access.hw.door_bell", "data": {"door_id": "d1"}}
        )
        connection_count = 0

        def ws_connect_side_effect(*_a: Any, **_kw: Any) -> Any:
            nonlocal connection_count
            connection_count += 1
            if connection_count <= 2:
                return _make_ws_ctx([_ws_msg(text=msg_payload), _ws_msg()])
            raise asyncio.CancelledError

        def _make_ws_ctx(messages: list[MagicMock]) -> MagicMock:
            async def _aiter() -> Any:
                for m in messages:
                    yield m

            mock_conn = AsyncMock()
            mock_conn.__aiter__ = lambda self: _aiter()
            mock_conn.exception = MagicMock(return_value=None)
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=mock_conn)
            ctx.__aexit__ = AsyncMock(return_value=False)
            return ctx

        ws._session.ws_connect = MagicMock(side_effect=ws_connect_side_effect)
        with pytest.raises(asyncio.CancelledError):
            await ws._loop()

        assert handler.await_count == 2  # called once per connection

    async def test_callbacks_on_reconnect(self) -> None:
        """on_connect and on_disconnect are called on each connection cycle."""
        connect_count = 0
        disconnect_count = 0

        def on_connect() -> None:
            nonlocal connect_count
            connect_count += 1

        def on_disconnect() -> None:
            nonlocal disconnect_count
            disconnect_count += 1

        ws = _make_ws(
            on_connect=on_connect,
            on_disconnect=on_disconnect,
            reconnect_interval=0,
            max_retries=1,
        )
        connection_n = 0

        def ws_connect_side_effect(*_a: Any, **_kw: Any) -> Any:
            nonlocal connection_n
            connection_n += 1
            if connection_n <= 2:
                # Each connection produces a CLOSE immediately
                async def _aiter() -> Any:
                    yield _ws_msg()

                mock_conn = AsyncMock()
                mock_conn.__aiter__ = lambda self: _aiter()
                mock_conn.exception = MagicMock(return_value=None)
                ctx = AsyncMock()
                ctx.__aenter__ = AsyncMock(return_value=mock_conn)
                ctx.__aexit__ = AsyncMock(return_value=False)
                return ctx
            raise aiohttp.ClientError("fail")

        ws._session.ws_connect = MagicMock(side_effect=ws_connect_side_effect)
        await ws._loop()

        assert connect_count == 2
        assert disconnect_count == 2
