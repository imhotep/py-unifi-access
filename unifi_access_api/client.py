"""UniFi Access API client."""

from __future__ import annotations

import logging
import ssl
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any, TypeVar
from urllib.parse import urlparse

import aiohttp
from pydantic import BaseModel

from .const import (
    DEVICE_NOTIFICATIONS_URL,
    DOOR_LOCK_RULE_URL,
    DOOR_UNLOCK_URL,
    DOORS_EMERGENCY_URL,
    DOORS_URL,
    STATIC_URL,
    UNIFI_ACCESS_API_PORT,
)
from .exceptions import (
    ApiAuthError,
    ApiConnectionError,
    ApiError,
    ApiForbiddenError,
    ApiNotFoundError,
    ApiRateLimitError,
    ApiSSLError,
)
from .models.door import (
    Door,
    DoorLockRule,
    DoorLockRuleStatus,
    EmergencyStatus,
)
from .websocket import UnifiAccessWebsocket, WsMessageHandler

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T", bound=BaseModel)


@contextmanager
def _map_exceptions(url: str) -> Generator[None]:
    """Map aiohttp and stdlib exceptions to library exceptions."""
    try:
        yield
    except (ApiAuthError, ApiError):
        raise
    except TimeoutError as err:
        raise ApiConnectionError(f"Timeout connecting to {url}") from err
    except aiohttp.ClientSSLError as err:
        raise ApiSSLError(str(err)) from err
    except (aiohttp.ClientError, OSError) as err:
        raise ApiConnectionError(str(err)) from err


class UnifiAccessApiClient:
    """
    Stateless UniFi Access API client.

    Designed for Home Assistant's DataUpdateCoordinator pattern.
    The aiohttp session is provided externally (e.g. via async_get_clientsession).
    """

    def __init__(
        self,
        host: str,
        api_token: str,
        session: aiohttp.ClientSession,
        *,
        verify_ssl: bool = False,
        ssl_context: ssl.SSLContext | None = None,
        request_timeout: int = 10,
    ) -> None:
        if "://" not in host:
            host = f"https://{host}"
        parsed = urlparse(host)
        hostname = parsed.hostname
        if not hostname:
            raise ValueError(f"Invalid host: {host!r}")
        port = parsed.port or UNIFI_ACCESS_API_PORT

        self._host = f"https://{hostname}:{port}"
        self._ws_host = f"wss://{hostname}:{port}"
        self._session = session
        self._request_timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._auth_header = f"Bearer {api_token}"

        self._http_headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": self._auth_header,
        }
        self._ws_headers: dict[str, str] = {
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Authorization": self._auth_header,
        }

        if ssl_context is not None:
            self._ssl_context: ssl.SSLContext | bool = ssl_context
        elif verify_ssl:
            self._ssl_context = True
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._ssl_context = ctx

        self._websocket: UnifiAccessWebsocket | None = None

    def _url(self, path: str) -> str:
        """Build a full API URL from a path."""
        return f"{self._host}{path}"

    _STATUS_EXCEPTIONS: dict[int, type[ApiError]] = {
        403: ApiForbiddenError,
        404: ApiNotFoundError,
        429: ApiRateLimitError,
    }

    @staticmethod
    async def _check_status(resp: aiohttp.ClientResponse, context: str = "") -> None:
        """Raise on non-200 status codes."""
        if resp.status == 401:
            raise ApiAuthError
        if resp.status != 200:
            msg = (
                f"{context} ({resp.status})"
                if context
                else f"Unexpected status {resp.status}"
            )
            try:
                body = await resp.text()
            except Exception:
                body = ""
            if body:
                msg = f"{msg}: {body[:200]}"
            exc_cls = UnifiAccessApiClient._STATUS_EXCEPTIONS.get(resp.status)
            if exc_cls is not None:
                raise exc_cls(msg)
            raise ApiError(msg, status_code=resp.status)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> UnifiAccessApiClient:
        """Enter async context manager."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit async context manager."""
        await self.close()

    async def close(self) -> None:
        """Stop the websocket. Does not close the session (owned externally)."""
        if self._websocket is not None:
            await self._websocket.stop()
            self._websocket = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(self) -> None:
        """
        Verify the API token by making a test request.

        Raises ApiAuthError, ApiConnectionError, ApiSSLError, or ApiError
        on failure.
        """
        await self._request(self._url(DOORS_URL))

    # ------------------------------------------------------------------
    # Door operations
    # ------------------------------------------------------------------

    async def get_doors(self) -> list[Door]:
        """Fetch all doors."""
        return await self._request_list(Door, self._url(DOORS_URL))

    async def unlock_door(
        self,
        door_id: str,
        *,
        actor_id: str | None = None,
        actor_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """
        Remotely unlock a door.

        Args:
            door_id: Identity ID of the door.
            actor_id: Custom actor ID for system logs and webhooks.
                Required if actor_name is provided.
            actor_name: Custom actor name. Required if actor_id is provided.
            extra: Passthrough data included as-is in webhook notifications.

        """
        if (actor_id is None) != (actor_name is None):
            raise ValueError(
                "actor_id and actor_name must both be provided or both omitted"
            )
        body: dict[str, Any] | None = None
        if actor_id is not None or actor_name is not None or extra is not None:
            body = {
                k: v
                for k, v in (
                    ("actor_id", actor_id),
                    ("actor_name", actor_name),
                    ("extra", extra),
                )
                if v is not None
            }
        await self._request(
            self._url(DOOR_UNLOCK_URL.format(door_id=door_id)),
            "PUT",
            body,
        )

    async def get_door_lock_rule(self, door_id: str) -> DoorLockRuleStatus:
        """Get the current door lock rule."""
        return await self._request_obj(
            DoorLockRuleStatus,
            self._url(DOOR_LOCK_RULE_URL.format(door_id=door_id)),
        )

    async def set_door_lock_rule(self, door_id: str, rule: DoorLockRule) -> None:
        """Set a temporary door lock rule."""
        await self._request(
            self._url(DOOR_LOCK_RULE_URL.format(door_id=door_id)),
            "PUT",
            rule.model_dump(exclude_unset=True),
        )

    # ------------------------------------------------------------------
    # Emergency status
    # ------------------------------------------------------------------

    async def get_emergency_status(self) -> EmergencyStatus:
        """Get doors emergency status."""
        return await self._request_obj(EmergencyStatus, self._url(DOORS_EMERGENCY_URL))

    async def set_emergency_status(self, status: EmergencyStatus) -> None:
        """Set doors emergency status."""
        await self._request(self._url(DOORS_EMERGENCY_URL), "PUT", status.model_dump())

    # ------------------------------------------------------------------
    # Thumbnail
    # ------------------------------------------------------------------

    async def get_thumbnail(self, path: str) -> bytes:
        """
        Fetch a door thumbnail image.

        Args:
            path: Thumbnail path from a websocket ThumbnailInfo.url field.

        """
        url = self._url(f"{STATIC_URL}{path}")
        with _map_exceptions(url):
            async with self._session.request(
                "GET",
                url,
                headers={"Authorization": self._auth_header},
                ssl=self._ssl_context,
                timeout=self._request_timeout,
            ) as resp:
                await self._check_status(resp, "Thumbnail fetch failed")
                return await resp.read()

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    def start_websocket(
        self,
        message_handlers: dict[str, WsMessageHandler],
        *,
        on_connect: Callable[[], Any] | None = None,
        on_disconnect: Callable[[], Any] | None = None,
        reconnect_interval: int = 1,
        max_retries: int | None = None,
    ) -> UnifiAccessWebsocket:
        """
        Create and start a websocket connection.

        Returns the websocket instance for lifecycle management.
        """
        if self._websocket is not None and self._websocket.is_running:
            return self._websocket

        self._websocket = UnifiAccessWebsocket(
            uri=f"{self._ws_host}{DEVICE_NOTIFICATIONS_URL}",
            headers=self._ws_headers,
            ssl_context=self._ssl_context,
            session=self._session,
            message_handlers=message_handlers,
            on_connect=on_connect,
            on_disconnect=on_disconnect,
            reconnect_interval=reconnect_interval,
            max_retries=max_retries,
        )
        self._websocket.start()
        return self._websocket

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    async def _request_obj(
        self,
        response_type: type[_T],
        url: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _T:
        """Make an HTTP request and parse the response into a model."""
        raw = await self._request(url, method, data, params=params)
        return response_type.model_validate(raw)

    async def _request_list(
        self,
        response_type: type[_T],
        url: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[_T]:
        """Make an HTTP request and parse the response into a list of models."""
        raw = await self._request(url, method, data, params=params)
        return [response_type.model_validate(item) for item in raw]

    async def _request(
        self,
        url: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make an HTTP request to the UniFi Access API."""
        _LOGGER.debug("HTTP %s %s", method, url)
        with _map_exceptions(url):
            async with self._session.request(
                method,
                url,
                headers=self._http_headers,
                json=data,
                params=params,
                ssl=self._ssl_context,
                timeout=self._request_timeout,
            ) as resp:
                await self._check_status(resp)
                try:
                    response = await resp.json()
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise ApiError(
                        f"Invalid JSON response from {url}",
                        status_code=resp.status,
                    ) from err
                if response.get("code") != "SUCCESS":
                    raise ApiError(
                        f"API error: {response.get('msg', 'unknown')}",
                        status_code=resp.status,
                    )
                if "data" not in response:
                    raise ApiError(
                        f"Missing 'data' key in response from {url}",
                        status_code=resp.status,
                    )
                return response["data"]
