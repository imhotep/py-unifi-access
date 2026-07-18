"""UniFi Access API client."""

from __future__ import annotations

import logging
import ssl
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from inspect import isawaitable
from types import MappingProxyType
from typing import Any, TypeVar
from urllib.parse import urlparse

import aiohttp
from pydantic import BaseModel

from .const import (
    DEVICE_NOTIFICATIONS_URL,
    DEVICE_SETTINGS_URL,
    DEVICES_URL,
    DOOR_LOCK_RULE_URL,
    DOOR_UNLOCK_URL,
    DOORS_EMERGENCY_URL,
    DOORS_URL,
    PROTECT_META_INFO_URL,
    STATIC_URL,
    UNIFI_ACCESS_API_PORT,
    USER_PIN_CODES_URL,
    USER_URL,
    USERS_URL,
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
from .models.device_settings import DeviceSettings
from .models.door import (
    Device,
    Door,
    DoorLockRule,
    DoorLockRuleStatus,
    EmergencyStatus,
)
from .models.user import User
from .models.websocket import WebsocketMessage
from .websocket import UnifiAccessWebsocket, WsMessageHandler, WsRawMessageHandler

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
        self._url_host = f"[{hostname}]" if ":" in hostname else hostname
        port = parsed.port or UNIFI_ACCESS_API_PORT

        self._host = f"https://{self._url_host}:{port}"
        self._ws_host = f"wss://{self._url_host}:{port}"
        self._session = session
        self._request_timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._api_token = api_token
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
            _LOGGER.warning(
                "SSL certificate verification disabled for %s",
                self._host,
            )

        self._websocket: UnifiAccessWebsocket | None = None
        self._device_door_map: dict[str, str] | None = None

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
        self._device_door_map = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def is_protect_api_key(self) -> bool:
        """
        Check whether the API token belongs to UniFi Protect.

        Makes a best-effort GET request to the Protect meta-info endpoint
        on the same host (port 443). Returns True if the endpoint responds
        with HTTP 200, meaning the key is valid for Protect and was likely
        created in the wrong application.

        Any network or request error is silently caught and returns False.
        """
        url = f"https://{self._url_host}{PROTECT_META_INFO_URL}"
        headers = {
            "X-API-KEY": self._api_token,
            "Accept": "application/json",
        }
        _LOGGER.debug("Checking if API token belongs to UniFi Protect at %s", url)
        try:
            async with self._session.request(
                "GET",
                url,
                headers=headers,
                ssl=self._ssl_context,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
        except (TimeoutError, aiohttp.ClientError, OSError):
            return False

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

    async def get_devices(self) -> list[Device]:
        """
        Fetch all devices.

        The API returns devices grouped by location (door).  This method
        flattens the groups into a single list.
        """
        raw = await self._request(self._url(DEVICES_URL))
        return [Device.model_validate(dev) for group in raw for dev in group]

    async def get_device_door_map(self, *, refresh: bool = False) -> Mapping[str, str]:
        """
        Return a cached device-MAC to door-UUID mapping.

        Fetches the devices list on the first call and caches the result.
        Pass ``refresh=True`` to force a re-fetch (e.g. after a
        ``access.data.v2.device.update`` event).
        """
        if self._device_door_map is None or refresh:
            devices = await self.get_devices()
            self._device_door_map = {
                dev.id: dev.location_id for dev in devices if dev.location_id
            }
        return MappingProxyType(self._device_door_map)

    def resolve_door_id(self, device_mac: str) -> str | None:
        """
        Look up a door UUID by device MAC from the cached device map.

        Returns *None* if the map has not been populated yet (call
        :meth:`get_device_door_map` first) or the MAC is unknown.
        """
        if self._device_door_map is None:
            return None
        return self._device_door_map.get(device_mac)

    def _enrich_ws_message(self, msg: WebsocketMessage) -> WebsocketMessage:
        """Attach ``door_id`` to a websocket message from the cached map."""
        door_id = self.resolve_door_id(msg.event_object_id)
        if door_id:
            return msg.model_copy(update={"door_id": door_id})
        return msg

    async def unlock_door(
        self,
        door_id: str,
        *,
        control_cmd: str | None = None,
        reader_id: str | None = None,
        entry_method: str | None = None,
        actor_id: str | None = None,
        actor_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """
        Remotely unlock a door.

        Args:
            door_id: Identity ID of the door.
            control_cmd: Motor command — "open", "close", or "stop" (UGT gates).
            reader_id: Reader device MAC to act as (optional).
            entry_method: Entry direction hint, e.g. "in" (optional).
            actor_id: Custom actor ID for system logs and webhooks.
                Required if actor_name is provided.
            actor_name: Custom actor name. Required if actor_id is provided.
            extra: Passthrough data included as-is in webhook notifications.

        """
        if (actor_id is None) != (actor_name is None):
            raise ValueError(
                "actor_id and actor_name must both be provided or both omitted"
            )
        params: dict[str, str] = {}
        if control_cmd is not None:
            params["control_cmd"] = control_cmd
        if reader_id is not None:
            params["reader_id"] = reader_id
        if entry_method is not None:
            params["entry_method"] = entry_method
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
            params=params or None,
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
        on_raw_message: WsRawMessageHandler | None = None,
        reconnect_interval: int = 1,
        max_retries: int | None = None,
    ) -> UnifiAccessWebsocket:
        """
        Create and start a websocket connection.

        Returns the websocket instance for lifecycle management.
        """
        if self._websocket is not None and self._websocket.is_running:
            return self._websocket

        user_on_connect = on_connect

        async def _on_ws_connect() -> None:
            """
            Populate the device→door cache, then invoke the user callback.

            Errors fetching the device map are logged but do not propagate,
            so a transient API failure cannot terminate the websocket loop.
            """
            try:
                await self.get_device_door_map(refresh=True)
            except Exception:
                _LOGGER.exception(
                    "Failed to refresh device→door map on WS connect; "
                    "door_id enrichment may be stale"
                )
            if user_on_connect is not None:
                result = user_on_connect()
                if isawaitable(result):
                    await result

        self._websocket = UnifiAccessWebsocket(
            uri=f"{self._ws_host}{DEVICE_NOTIFICATIONS_URL}",
            headers=self._ws_headers,
            ssl_context=self._ssl_context,
            session=self._session,
            message_handlers=message_handlers,
            on_connect=_on_ws_connect,
            on_disconnect=on_disconnect,
            on_raw_message=on_raw_message,
            message_enricher=self._enrich_ws_message,
            reconnect_interval=reconnect_interval,
            max_retries=max_retries,
        )
        self._websocket.start()
        return self._websocket

    # ------------------------------------------------------------------
    # User operations
    # ------------------------------------------------------------------

    async def get_users(self) -> list[User]:
        """Fetch all users."""
        return await self._request_list(User, self._url(USERS_URL))

    async def update_user_status(self, user_id: str, *, enabled: bool) -> None:
        """Enable or disable a user."""
        status = "ACTIVE" if enabled else "DEACTIVATED"
        await self._request(
            self._url(USER_URL.format(user_id=user_id)),
            "PUT",
            {"status": status},
        )

    async def update_user_pin(self, user_id: str, pin: str | None) -> None:
        """Assign or remove a user's PIN (pass None or empty string to remove)."""
        url = self._url(USER_PIN_CODES_URL.format(user_id=user_id))
        if pin:
            await self._request(url, "PUT", {"pin_code": pin})
        else:
            await self._request(url, "DELETE")

    # ------------------------------------------------------------------
    # Device settings operations
    # ------------------------------------------------------------------

    async def get_device_settings(self, device_id: str) -> DeviceSettings:
        """Fetch access method settings for a device."""
        return await self._request_obj(
            DeviceSettings,
            self._url(DEVICE_SETTINGS_URL.format(device_id=device_id)),
        )

    async def put_device_settings(
        self, device_id: str, access_methods: dict[str, Any]
    ) -> None:
        """Update access method settings for a device."""
        await self._request(
            self._url(DEVICE_SETTINGS_URL.format(device_id=device_id)),
            "PUT",
            {"access_methods": access_methods},
        )

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
