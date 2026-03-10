"""WebSocket client for UniFi Access real-time updates."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import ssl
from collections.abc import Callable, Coroutine
from typing import Any

import aiohttp

from .models.websocket import WebsocketMessage, create_from_unifi_dict

_LOGGER = logging.getLogger(__name__)

_BACKOFF_MIN = 1
_BACKOFF_MAX = 60
_BACKOFF_FACTOR = 2
_WS_HEARTBEAT = 30

WsMessageHandler = Callable[[WebsocketMessage], Coroutine[Any, Any, None] | None]


class UnifiAccessWebsocket:
    """Manages the websocket connection to UniFi Access."""

    def __init__(
        self,
        uri: str,
        headers: dict[str, str],
        ssl_context: ssl.SSLContext | bool,
        session: aiohttp.ClientSession,
        message_handlers: dict[str, WsMessageHandler],
        *,
        on_connect: Callable[[], Any] | None = None,
        on_disconnect: Callable[[], Any] | None = None,
        reconnect_interval: int = _BACKOFF_MIN,
        max_retries: int | None = None,
    ) -> None:
        # message_handlers: use ``"*"`` as a wildcard key to receive events
        # that have no specific handler registered.
        self._uri = uri
        self._headers = headers
        self._ssl_context = ssl_context
        self._session = session
        self._message_handlers = message_handlers
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._reconnect_interval = reconnect_interval
        self._max_retries = max_retries
        self._task: asyncio.Task[None] | None = None
        self._connected = False

    @property
    def is_running(self) -> bool:
        """Check if the websocket task is alive (connected or reconnecting)."""
        return self._task is not None and not self._task.done()

    @property
    def is_connected(self) -> bool:
        """Check if the websocket has an active connection."""
        return self._connected

    def start(self) -> None:
        """Start the websocket connection loop."""
        if self.is_running:
            _LOGGER.warning("Websocket already running")
            return
        self._task = asyncio.create_task(self._loop())
        _LOGGER.info("Started websocket connection task")

    async def stop(self) -> None:
        """Stop the websocket connection."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            _LOGGER.info("Websocket connection stopped")

    async def _invoke(self, callback: Callable[..., Any] | None, *args: Any) -> None:
        """Invoke a callback, awaiting it if it returns an awaitable."""
        if callback is None:
            return
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

    async def _handle_message(self, message: str) -> None:
        """Process a single websocket message."""
        if message.startswith("Hello"):
            return

        _LOGGER.debug("Websocket message received: %s", message)
        try:
            raw = json.loads(message)
        except json.JSONDecodeError:
            _LOGGER.warning("Ignoring non-JSON websocket message: %s", message)
            return

        if not isinstance(raw, dict):
            _LOGGER.debug("Ignoring non-dict websocket payload: %s", type(raw).__name__)
            return

        parsed = create_from_unifi_dict(raw)
        event = parsed.event or ""
        handler = self._message_handlers.get(event)
        if handler is None:
            handler = self._message_handlers.get("*")
        if handler is not None:
            await self._invoke(handler, parsed)
        else:
            _LOGGER.debug("Unhandled websocket message type: %s", event)

    async def _loop(self) -> None:
        """Run websocket connection with automatic reconnection and backoff."""
        backoff = self._reconnect_interval
        retries = 0
        try:
            while True:
                try:
                    _LOGGER.debug("Connecting to websocket %s", self._uri)
                    async with self._session.ws_connect(
                        self._uri,
                        headers=self._headers,
                        ssl=self._ssl_context,
                        heartbeat=_WS_HEARTBEAT,
                    ) as ws:
                        _LOGGER.info("Websocket connection established")
                        backoff = self._reconnect_interval
                        retries = 0
                        self._connected = True
                        await self._invoke(self._on_connect)
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    await self._handle_message(msg.data)
                                except Exception:
                                    _LOGGER.exception(
                                        "Error handling websocket message"
                                    )
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                _LOGGER.error("Websocket error: %s", ws.exception())
                                break
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.CLOSED,
                            ):
                                _LOGGER.warning("Websocket closed")
                                break
                except (aiohttp.ClientError, OSError, TimeoutError) as err:
                    _LOGGER.error("Websocket connection error: %s", err)

                if self._connected:
                    self._connected = False
                    await self._invoke(self._on_disconnect)

                retries += 1
                if self._max_retries is not None and retries > self._max_retries:
                    _LOGGER.warning(
                        "Max retries (%s) reached, giving up", self._max_retries
                    )
                    break

                _LOGGER.debug("Reconnecting websocket in %s seconds...", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)
        except asyncio.CancelledError:
            _LOGGER.info("Websocket task cancelled")
            if self._connected:
                self._connected = False
                await self._invoke(self._on_disconnect)
            raise
