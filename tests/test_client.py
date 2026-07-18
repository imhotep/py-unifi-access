"""Tests for unifi_access_api.client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from unifi_access_api.client import UnifiAccessApiClient, _map_exceptions
from unifi_access_api.const import (
    DEVICE_NOTIFICATIONS_URL,
    DEVICE_SETTINGS_URL,
    DEVICES_URL,
    DOORS_URL,
    STATIC_URL,
    UNIFI_ACCESS_API_PORT,
    USER_PIN_CODES_URL,
    USER_URL,
    USERS_URL,
)
from unifi_access_api.exceptions import (
    ApiAuthError,
    ApiConnectionError,
    ApiError,
    ApiForbiddenError,
    ApiNotFoundError,
    ApiRateLimitError,
    ApiSSLError,
)
from unifi_access_api.models.device_settings import DeviceSettings
from unifi_access_api.models.door import (
    Device,
    Door,
    DoorLockRule,
    DoorLockRuleStatus,
    DoorLockRuleType,
    EmergencyStatus,
)
from unifi_access_api.models.user import User, UserStatus

from .conftest import (
    SAMPLE_DOOR_LOCKED,
    SAMPLE_DOOR_RAW,
    SAMPLE_EMERGENCY_STATUS_RAW,
    SAMPLE_LOCK_RULE_STATUS_RAW,
    _make_success_response,
    make_mock_response,
)

SAMPLE_DEVICE_GROUPS = [
    [
        {
            "id": "aabbccddeeff",
            "type": "UA-Hub-Door-Mini",
            "location_id": "door-uuid-1",
        },
        {"id": "ffeeddccbbaa", "type": "UA-G3-Flex", "location_id": "door-uuid-1"},
    ],
    [
        {"id": "112233445566", "type": "UAH-DOOR", "location_id": "door-uuid-2"},
    ],
]

# ---------------------------------------------------------------------------
# Constructor / host parsing
# ---------------------------------------------------------------------------


class TestClientInit:
    def test_plain_host(self, mock_session: AsyncMock) -> None:
        client = UnifiAccessApiClient("192.168.1.1", "tok", mock_session)
        assert client._host == f"https://192.168.1.1:{UNIFI_ACCESS_API_PORT}"
        assert client._ws_host == f"wss://192.168.1.1:{UNIFI_ACCESS_API_PORT}"

    def test_ipv6_host_brackets_urls(self, mock_session: AsyncMock) -> None:
        client = UnifiAccessApiClient("[::1]", "tok", mock_session)
        assert client._host == f"https://[::1]:{UNIFI_ACCESS_API_PORT}"
        assert client._ws_host == f"wss://[::1]:{UNIFI_ACCESS_API_PORT}"

    def test_host_with_scheme(self, mock_session: AsyncMock) -> None:
        client = UnifiAccessApiClient("https://192.168.1.1", "tok", mock_session)
        assert client._host == f"https://192.168.1.1:{UNIFI_ACCESS_API_PORT}"

    def test_host_with_port(self, mock_session: AsyncMock) -> None:
        client = UnifiAccessApiClient("https://192.168.1.1:7443", "tok", mock_session)
        assert client._host == "https://192.168.1.1:7443"
        assert client._ws_host == "wss://192.168.1.1:7443"

    def test_hostname(self, mock_session: AsyncMock) -> None:
        client = UnifiAccessApiClient("unifi.local", "tok", mock_session)
        assert client._host == f"https://unifi.local:{UNIFI_ACCESS_API_PORT}"

    def test_invalid_host_raises(self, mock_session: AsyncMock) -> None:
        with pytest.raises(ValueError, match="Invalid host"):
            UnifiAccessApiClient("://invalid", "tok", mock_session)

    def test_verify_ssl_true(self, mock_session: AsyncMock) -> None:
        client = UnifiAccessApiClient(
            "192.168.1.1", "tok", mock_session, verify_ssl=True
        )
        assert client._ssl_context is True

    def test_verify_ssl_false_creates_context(self, mock_session: AsyncMock) -> None:
        client = UnifiAccessApiClient(
            "192.168.1.1", "tok", mock_session, verify_ssl=False
        )
        import ssl

        assert isinstance(client._ssl_context, ssl.SSLContext)

    def test_auth_header(self, mock_session: AsyncMock) -> None:
        client = UnifiAccessApiClient("x.local", "my-token", mock_session)
        assert client._http_headers["Authorization"] == "Bearer my-token"
        assert client._ws_headers["Authorization"] == "Bearer my-token"

    def test_custom_ssl_context(self, mock_session: AsyncMock) -> None:
        import ssl

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client = UnifiAccessApiClient(
            "192.168.1.1", "tok", mock_session, ssl_context=ctx
        )
        assert client._ssl_context is ctx


# ---------------------------------------------------------------------------
# _map_exceptions
# ---------------------------------------------------------------------------


class TestMapExceptions:
    def test_passes_api_auth_error(self) -> None:
        with pytest.raises(ApiAuthError):
            with _map_exceptions("http://x"):
                raise ApiAuthError

    def test_passes_api_error(self) -> None:
        with pytest.raises(ApiError):
            with _map_exceptions("http://x"):
                raise ApiError("bad")

    def test_timeout_becomes_connection_error(self) -> None:
        with pytest.raises(ApiConnectionError, match="Timeout"):
            with _map_exceptions("http://x"):
                raise TimeoutError

    def test_client_ssl_error_becomes_api_ssl_error(self) -> None:
        conn_key = MagicMock()
        conn_key.ssl = True
        with pytest.raises(ApiSSLError):
            with _map_exceptions("http://x"):
                raise aiohttp.ClientSSLError(conn_key, OSError("ssl fail"))

    def test_client_error_becomes_connection_error(self) -> None:
        with pytest.raises(ApiConnectionError):
            with _map_exceptions("http://x"):
                raise aiohttp.ClientError("conn fail")

    def test_os_error_becomes_connection_error(self) -> None:
        with pytest.raises(ApiConnectionError):
            with _map_exceptions("http://x"):
                raise OSError("network down")


# ---------------------------------------------------------------------------
# _check_status
# ---------------------------------------------------------------------------


class TestCheckStatus:
    async def test_200_ok(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 200
        await UnifiAccessApiClient._check_status(resp)

    async def test_401_raises_auth_error(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 401
        with pytest.raises(ApiAuthError):
            await UnifiAccessApiClient._check_status(resp)

    async def test_500_raises_api_error(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 500
        resp.text = AsyncMock(return_value="Internal Server Error")
        with pytest.raises(ApiError, match="500") as exc_info:
            await UnifiAccessApiClient._check_status(resp)
        assert exc_info.value.status_code == 500

    async def test_error_with_context(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 404
        resp.text = AsyncMock(return_value="")
        with pytest.raises(ApiError, match="Not found"):
            await UnifiAccessApiClient._check_status(resp, "Not found")

    async def test_error_body_truncated(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 500
        resp.text = AsyncMock(return_value="x" * 300)
        with pytest.raises(ApiError) as exc_info:
            await UnifiAccessApiClient._check_status(resp)
        # Body is truncated to 200 chars
        assert len(str(exc_info.value)) <= 250

    async def test_error_body_read_fails(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 502
        resp.text = AsyncMock(side_effect=Exception("read fail"))
        with pytest.raises(ApiError, match="502"):
            await UnifiAccessApiClient._check_status(resp)


# ---------------------------------------------------------------------------
# _request base method
# ---------------------------------------------------------------------------


class TestRequest:
    async def test_success(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response([SAMPLE_DOOR_RAW])
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        result = await api_client._request(api_client._url(DOORS_URL))
        assert result == [SAMPLE_DOOR_RAW]

    async def test_non_success_code_raises(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = {"code": "FAIL", "msg": "something wrong"}
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        with pytest.raises(ApiError, match="something wrong"):
            await api_client._request(api_client._url(DOORS_URL))

    async def test_missing_data_key_raises(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = {"code": "SUCCESS"}
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        with pytest.raises(ApiError, match="Missing 'data'"):
            await api_client._request(api_client._url(DOORS_URL))

    async def test_invalid_json_raises(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(
            return_value=make_mock_response(raise_on_json=ValueError("bad json"))
        )
        with pytest.raises(ApiError, match="Invalid JSON"):
            await api_client._request(api_client._url(DOORS_URL))

    async def test_content_type_error_raises(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(
            return_value=make_mock_response(
                raise_on_json=aiohttp.ContentTypeError(MagicMock(), MagicMock())
            )
        )
        with pytest.raises(ApiError, match="Invalid JSON"):
            await api_client._request(api_client._url(DOORS_URL))

    async def test_401_raises_auth(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(return_value=make_mock_response(status=401))
        with pytest.raises(ApiAuthError):
            await api_client._request(api_client._url(DOORS_URL))

    async def test_timeout_raises_connection_error(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(side_effect=TimeoutError)
        with pytest.raises(ApiConnectionError, match="Timeout"):
            await api_client._request(api_client._url(DOORS_URL))

    async def test_os_error_raises_connection_error(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(side_effect=OSError("down"))
        with pytest.raises(ApiConnectionError):
            await api_client._request(api_client._url(DOORS_URL))

    async def test_request_passes_params(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response("ok")
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client._request(api_client._url(DOORS_URL), params={"foo": "bar"})
        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs["params"] == {"foo": "bar"}

    async def test_request_passes_json_body(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response("ok")
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client._request(api_client._url(DOORS_URL), "PUT", {"key": "val"})
        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs["json"] == {"key": "val"}


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


class TestAuthenticate:
    async def test_calls_doors_url(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response([])
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.authenticate()
        call_args = mock_session.request.call_args
        assert DOORS_URL in call_args[0][1]

    async def test_auth_failure(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(return_value=make_mock_response(status=401))
        with pytest.raises(ApiAuthError):
            await api_client.authenticate()


# ---------------------------------------------------------------------------
# is_protect_api_key
# ---------------------------------------------------------------------------


class TestIsProtectApiKey:
    async def test_returns_true_on_200(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(
            return_value=make_mock_response(status=200, json_data={"version": "1.0"})
        )
        assert await api_client.is_protect_api_key() is True

    async def test_returns_false_on_401(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(return_value=make_mock_response(status=401))
        assert await api_client.is_protect_api_key() is False

    async def test_returns_false_on_connection_error(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=aiohttp.ClientConnectionError())
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.request = MagicMock(return_value=ctx)
        assert await api_client.is_protect_api_key() is False

    async def test_returns_false_on_timeout(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=TimeoutError())
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.request = MagicMock(return_value=ctx)
        assert await api_client.is_protect_api_key() is False

    async def test_uses_port_443_and_protect_headers(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(
            return_value=make_mock_response(status=200, json_data={})
        )
        await api_client.is_protect_api_key()
        call_args = mock_session.request.call_args
        url = call_args[1]["url"] if "url" in call_args[1] else call_args[0][1]
        assert "192.168.1.1" in url
        assert ":12445" not in url
        assert "/proxy/protect/integration/v1/meta/info" in url
        headers = (
            call_args[1]["headers"] if "headers" in call_args[1] else call_args[0][2]
        )
        assert "X-API-KEY" in headers
        assert headers["X-API-KEY"] == "test-api-token"

    async def test_ipv6_host_brackets(self, mock_session: AsyncMock) -> None:
        client = UnifiAccessApiClient("[::1]", "tok", mock_session, verify_ssl=False)
        mock_session.request = MagicMock(
            return_value=make_mock_response(status=200, json_data={})
        )
        await client.is_protect_api_key()
        call_args = mock_session.request.call_args
        url = call_args[1]["url"] if "url" in call_args[1] else call_args[0][1]
        assert "https://[::1]/" in url


# ---------------------------------------------------------------------------
# _check_status — specific exception subclasses
# ---------------------------------------------------------------------------


class TestCheckStatusSpecificExceptions:
    async def test_403_raises_forbidden(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 403
        resp.text = AsyncMock(return_value="Forbidden")
        with pytest.raises(ApiForbiddenError) as exc_info:
            await UnifiAccessApiClient._check_status(resp)
        assert exc_info.value.status_code == 403

    async def test_404_raises_not_found(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 404
        resp.text = AsyncMock(return_value="Not Found")
        with pytest.raises(ApiNotFoundError) as exc_info:
            await UnifiAccessApiClient._check_status(resp)
        assert exc_info.value.status_code == 404

    async def test_429_raises_rate_limit(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 429
        resp.text = AsyncMock(return_value="Too Many Requests")
        with pytest.raises(ApiRateLimitError) as exc_info:
            await UnifiAccessApiClient._check_status(resp)
        assert exc_info.value.status_code == 429

    async def test_403_is_api_error_subclass(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 403
        resp.text = AsyncMock(return_value="")
        with pytest.raises(ApiError):
            await UnifiAccessApiClient._check_status(resp)

    async def test_404_is_api_error_subclass(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 404
        resp.text = AsyncMock(return_value="")
        with pytest.raises(ApiError):
            await UnifiAccessApiClient._check_status(resp)

    async def test_429_is_api_error_subclass(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 429
        resp.text = AsyncMock(return_value="")
        with pytest.raises(ApiError):
            await UnifiAccessApiClient._check_status(resp)

    async def test_500_still_raises_generic_api_error(self) -> None:
        resp = AsyncMock(spec=aiohttp.ClientResponse)
        resp.status = 500
        resp.text = AsyncMock(return_value="Internal Server Error")
        with pytest.raises(ApiError) as exc_info:
            await UnifiAccessApiClient._check_status(resp)
        assert type(exc_info.value) is ApiError
        assert exc_info.value.status_code == 500

    async def test_403_via_client_request(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(
            return_value=make_mock_response(status=403, text_data="Forbidden")
        )
        with pytest.raises(ApiForbiddenError):
            await api_client.get_doors()

    async def test_404_via_client_request(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(
            return_value=make_mock_response(status=404, text_data="Not Found")
        )
        with pytest.raises(ApiNotFoundError):
            await api_client.get_doors()

    async def test_429_via_client_request(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(
            return_value=make_mock_response(status=429, text_data="Rate limit")
        )
        with pytest.raises(ApiRateLimitError):
            await api_client.get_doors()


# ---------------------------------------------------------------------------
# get_doors
# ---------------------------------------------------------------------------


class TestGetDoors:
    async def test_returns_door_list(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response([SAMPLE_DOOR_RAW, SAMPLE_DOOR_LOCKED])
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        doors = await api_client.get_doors()
        assert len(doors) == 2
        assert all(isinstance(d, Door) for d in doors)
        assert doors[0].id == "door-001"
        assert doors[1].id == "door-002"

    async def test_empty_list(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response([])
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        doors = await api_client.get_doors()
        assert doors == []


# ---------------------------------------------------------------------------
# unlock_door
# ---------------------------------------------------------------------------


class TestUnlockDoor:
    async def test_simple_unlock(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.unlock_door("door-001")
        call_args = mock_session.request.call_args
        assert "door-001/unlock" in call_args[0][1]
        assert call_args[0][0] == "PUT"

    async def test_unlock_with_actor(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.unlock_door("d1", actor_id="a1", actor_name="Admin")
        call_kwargs = mock_session.request.call_args[1]
        body = call_kwargs["json"]
        assert body["actor_id"] == "a1"
        assert body["actor_name"] == "Admin"

    async def test_unlock_with_extra(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.unlock_door("d1", extra={"ha": True})
        call_kwargs = mock_session.request.call_args[1]
        body = call_kwargs["json"]
        assert body["extra"] == {"ha": True}

    async def test_actor_id_only_raises(self, api_client: UnifiAccessApiClient) -> None:
        with pytest.raises(ValueError, match="both be provided"):
            await api_client.unlock_door("d1", actor_id="a1")

    async def test_actor_name_only_raises(
        self, api_client: UnifiAccessApiClient
    ) -> None:
        with pytest.raises(ValueError, match="both be provided"):
            await api_client.unlock_door("d1", actor_name="Admin")

    async def test_no_body_when_no_params(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.unlock_door("d1")
        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs["json"] is None

    async def test_unlock_with_control_cmd_open(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.unlock_door("d1", control_cmd="open")
        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs["params"] == {"control_cmd": "open"}
        assert call_kwargs["json"] is None

    async def test_unlock_with_control_cmd_close(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.unlock_door("d1", control_cmd="close")
        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs["params"] == {"control_cmd": "close"}

    async def test_unlock_with_control_cmd_stop(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.unlock_door("d1", control_cmd="stop")
        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs["params"] == {"control_cmd": "stop"}

    async def test_unlock_with_reader_id(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.unlock_door("d1", control_cmd="open", reader_id="aa:bb:cc")
        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs["params"]["reader_id"] == "aa:bb:cc"
        assert call_kwargs["params"]["control_cmd"] == "open"

    async def test_unlock_no_control_cmd_sends_no_params(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.unlock_door("d1")
        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs.get("params") is None


# ---------------------------------------------------------------------------
# get/set door lock rule
# ---------------------------------------------------------------------------


class TestDoorLockRuleOps:
    async def test_get_door_lock_rule(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(SAMPLE_LOCK_RULE_STATUS_RAW)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        result = await api_client.get_door_lock_rule("d1")
        assert isinstance(result, DoorLockRuleStatus)
        assert result.type == DoorLockRuleType.KEEP_LOCK
        assert result.ended_time == 1700000000

    async def test_set_door_lock_rule(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        rule = DoorLockRule(type=DoorLockRuleType.KEEP_LOCK)
        await api_client.set_door_lock_rule("d1", rule)
        call_kwargs = mock_session.request.call_args[1]
        body = call_kwargs["json"]
        assert body == {"type": "keep_lock"}
        assert "interval" not in body

    async def test_set_door_lock_rule_with_interval(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        rule = DoorLockRule(type=DoorLockRuleType.CUSTOM, interval=600)
        await api_client.set_door_lock_rule("d1", rule)
        call_kwargs = mock_session.request.call_args[1]
        body = call_kwargs["json"]
        assert body == {"type": "custom", "interval": 600}


# ---------------------------------------------------------------------------
# Emergency status
# ---------------------------------------------------------------------------


class TestEmergencyOps:
    async def test_get_emergency_status(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(SAMPLE_EMERGENCY_STATUS_RAW)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        result = await api_client.get_emergency_status()
        assert isinstance(result, EmergencyStatus)
        assert result.evacuation is True
        assert result.lockdown is False

    async def test_set_emergency_status(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        status = EmergencyStatus(evacuation=False, lockdown=True)
        await api_client.set_emergency_status(status)
        call_kwargs = mock_session.request.call_args[1]
        body = call_kwargs["json"]
        assert body == {"evacuation": False, "lockdown": True}


# ---------------------------------------------------------------------------
# Thumbnail
# ---------------------------------------------------------------------------


class TestGetThumbnail:
    async def test_returns_bytes(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        img_bytes = b"\x89PNG\r\n\x1a\n"
        mock_session.request = MagicMock(
            return_value=make_mock_response(read_data=img_bytes)
        )
        result = await api_client.get_thumbnail("/img/thumb.jpg")
        assert result == img_bytes
        call_args = mock_session.request.call_args
        url = call_args[0][1]
        assert STATIC_URL in url
        assert "/img/thumb.jpg" in url

    async def test_auth_error(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(return_value=make_mock_response(status=401))
        with pytest.raises(ApiAuthError):
            await api_client.get_thumbnail("/img/x.jpg")

    async def test_connection_error(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(side_effect=OSError("fail"))
        with pytest.raises(ApiConnectionError):
            await api_client.get_thumbnail("/img/x.jpg")


# ---------------------------------------------------------------------------
# start_websocket
# ---------------------------------------------------------------------------


class TestStartWebsocket:
    def test_creates_websocket(self, api_client: UnifiAccessApiClient) -> None:
        with patch("unifi_access_api.client.UnifiAccessWebsocket") as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws.is_running = False
            mock_ws_cls.return_value = mock_ws
            ws = api_client.start_websocket({"test": AsyncMock()})
            assert ws is mock_ws
            mock_ws.start.assert_called_once()

    def test_returns_existing_running_websocket(
        self, api_client: UnifiAccessApiClient
    ) -> None:
        with patch("unifi_access_api.client.UnifiAccessWebsocket") as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws.is_running = False
            mock_ws_cls.return_value = mock_ws

            ws1 = api_client.start_websocket({})
            mock_ws.is_running = True
            ws2 = api_client.start_websocket({})
            assert ws1 is ws2
            assert mock_ws.start.call_count == 1

    def test_passes_correct_uri(self, api_client: UnifiAccessApiClient) -> None:
        with patch("unifi_access_api.client.UnifiAccessWebsocket") as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws.is_running = False
            mock_ws_cls.return_value = mock_ws
            api_client.start_websocket({})
            call_kwargs = mock_ws_cls.call_args[1]
            assert DEVICE_NOTIFICATIONS_URL in call_kwargs["uri"]
            assert call_kwargs["uri"].startswith("wss://")

    def test_passes_message_enricher(self, api_client: UnifiAccessApiClient) -> None:
        with patch("unifi_access_api.client.UnifiAccessWebsocket") as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws.is_running = False
            mock_ws_cls.return_value = mock_ws
            api_client.start_websocket({})
            call_kwargs = mock_ws_cls.call_args[1]
            enricher = call_kwargs["message_enricher"]
            assert enricher.__func__ is UnifiAccessApiClient._enrich_ws_message
            assert enricher.__self__ is api_client

    async def test_on_connect_populates_device_map(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        """The on_connect wrapper should refresh the device→door cache."""
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(SAMPLE_DEVICE_GROUPS)
        )
        with patch("unifi_access_api.client.UnifiAccessWebsocket") as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws.is_running = False
            mock_ws_cls.return_value = mock_ws
            api_client.start_websocket({})
            on_connect = mock_ws_cls.call_args[1]["on_connect"]
            await on_connect()

        assert api_client._device_door_map == {
            "aabbccddeeff": "door-uuid-1",
            "ffeeddccbbaa": "door-uuid-1",
            "112233445566": "door-uuid-2",
        }

    async def test_on_connect_invokes_user_callback(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        """User-supplied on_connect must still be invoked (async)."""
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response([])
        )
        user_cb = AsyncMock()
        with patch("unifi_access_api.client.UnifiAccessWebsocket") as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws.is_running = False
            mock_ws_cls.return_value = mock_ws
            api_client.start_websocket({}, on_connect=user_cb)
            on_connect = mock_ws_cls.call_args[1]["on_connect"]
            await on_connect()

        user_cb.assert_awaited_once()

    async def test_on_connect_invokes_sync_user_callback(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        """User-supplied on_connect may be a plain (sync) callable."""
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response([])
        )
        calls: list[int] = []
        with patch("unifi_access_api.client.UnifiAccessWebsocket") as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws.is_running = False
            mock_ws_cls.return_value = mock_ws
            api_client.start_websocket({}, on_connect=lambda: calls.append(1))
            on_connect = mock_ws_cls.call_args[1]["on_connect"]
            await on_connect()

        assert calls == [1]

    async def test_on_connect_swallows_device_map_errors(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        """A failing device map refresh must not kill the WS loop."""
        mock_session.request.side_effect = RuntimeError("API down")
        user_cb = AsyncMock()
        with patch("unifi_access_api.client.UnifiAccessWebsocket") as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws.is_running = False
            mock_ws_cls.return_value = mock_ws
            api_client.start_websocket({}, on_connect=user_cb)
            on_connect = mock_ws_cls.call_args[1]["on_connect"]
            # Must not raise
            await on_connect()

        # User callback must still be invoked
        user_cb.assert_awaited_once()


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    async def test_enter_returns_self(self, api_client: UnifiAccessApiClient) -> None:
        async with api_client as c:
            assert c is api_client

    async def test_exit_stops_websocket(self, api_client: UnifiAccessApiClient) -> None:
        mock_ws = AsyncMock()
        api_client._websocket = mock_ws
        await api_client.close()
        mock_ws.stop.assert_awaited_once()
        assert api_client._websocket is None

    async def test_close_without_websocket(
        self, api_client: UnifiAccessApiClient
    ) -> None:
        await api_client.close()  # should not raise

    async def test_close_clears_device_door_map(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(SAMPLE_DEVICE_GROUPS)
        )
        await api_client.get_device_door_map()
        assert api_client._device_door_map is not None
        await api_client.close()
        assert api_client._device_door_map is None


# ---------------------------------------------------------------------------
# get_devices
# ---------------------------------------------------------------------------


class TestGetDevices:
    async def test_flattens_device_groups(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        """Devices API returns nested arrays grouped by location; get_devices flattens."""
        raw_groups = [
            [
                {
                    "id": "aabbccddeeff",
                    "type": "UA-Hub-Door-Mini",
                    "location_id": "door-uuid-1",
                },
                {
                    "id": "ffeeddccbbaa",
                    "type": "UA-G3-Flex",
                    "location_id": "door-uuid-1",
                },
            ],
            [
                {
                    "id": "112233445566",
                    "type": "UAH-DOOR",
                    "location_id": "door-uuid-2",
                },
            ],
        ]
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(raw_groups)
        )
        devices = await api_client.get_devices()
        assert len(devices) == 3
        assert all(isinstance(d, Device) for d in devices)
        assert devices[0].id == "aabbccddeeff"
        assert devices[0].location_id == "door-uuid-1"
        assert devices[2].id == "112233445566"
        assert devices[2].location_id == "door-uuid-2"

    async def test_uses_devices_url(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response([])
        )
        await api_client.get_devices()
        url = mock_session.request.call_args[0][1]
        assert DEVICES_URL in url

    async def test_empty_groups(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response([])
        )
        devices = await api_client.get_devices()
        assert devices == []


# ---------------------------------------------------------------------------
# get_device_door_map / resolve_door_id
# ---------------------------------------------------------------------------


class TestDeviceDoorMap:
    async def test_builds_map_on_first_call(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(SAMPLE_DEVICE_GROUPS)
        )
        result = await api_client.get_device_door_map()
        assert result == {
            "aabbccddeeff": "door-uuid-1",
            "ffeeddccbbaa": "door-uuid-1",
            "112233445566": "door-uuid-2",
        }

    async def test_caches_result(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(SAMPLE_DEVICE_GROUPS)
        )
        await api_client.get_device_door_map()
        await api_client.get_device_door_map()
        # Only one HTTP call despite two get_device_door_map calls
        assert mock_session.request.call_count == 1

    async def test_refresh_refetches(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(SAMPLE_DEVICE_GROUPS)
        )
        await api_client.get_device_door_map()
        await api_client.get_device_door_map(refresh=True)
        assert mock_session.request.call_count == 2

    async def test_skips_devices_without_location(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        groups = [
            [
                {"id": "aabb", "type": "X", "location_id": ""},
                {"id": "ccdd", "type": "Y", "location_id": "door-1"},
            ]
        ]
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(groups)
        )
        result = await api_client.get_device_door_map()
        assert result == {"ccdd": "door-1"}

    async def test_returns_copy_not_internal_cache(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        """Callers must not be able to mutate the internal cache."""
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(SAMPLE_DEVICE_GROUPS)
        )
        result = await api_client.get_device_door_map()
        with pytest.raises(TypeError):
            result["evil"] = "hacked"  # type: ignore[index]
        # Cache untouched
        second = await api_client.get_device_door_map()
        assert "evil" not in second
        assert second["aabbccddeeff"] == "door-uuid-1"


class TestResolveDoorId:
    async def test_returns_door_uuid(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(SAMPLE_DEVICE_GROUPS)
        )
        await api_client.get_device_door_map()
        assert api_client.resolve_door_id("aabbccddeeff") == "door-uuid-1"
        assert api_client.resolve_door_id("112233445566") == "door-uuid-2"

    def test_returns_none_before_map_loaded(
        self, api_client: UnifiAccessApiClient
    ) -> None:
        assert api_client.resolve_door_id("aabbccddeeff") is None

    async def test_returns_none_for_unknown_mac(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(SAMPLE_DEVICE_GROUPS)
        )
        await api_client.get_device_door_map()
        assert api_client.resolve_door_id("000000000000") is None


# ---------------------------------------------------------------------------
# _enrich_ws_message
# ---------------------------------------------------------------------------


class TestEnrichWsMessage:
    async def test_attaches_door_id(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        from unifi_access_api.models.websocket import WebsocketMessage

        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(SAMPLE_DEVICE_GROUPS)
        )
        await api_client.get_device_door_map()
        msg = WebsocketMessage(event="access.logs.add", event_object_id="aabbccddeeff")
        enriched = api_client._enrich_ws_message(msg)
        assert enriched.door_id == "door-uuid-1"
        assert enriched.event_object_id == "aabbccddeeff"

    def test_returns_original_when_map_not_loaded(
        self, api_client: UnifiAccessApiClient
    ) -> None:
        from unifi_access_api.models.websocket import WebsocketMessage

        msg = WebsocketMessage(event="access.logs.add", event_object_id="aabbccddeeff")
        result = api_client._enrich_ws_message(msg)
        assert result is msg
        assert result.door_id == ""

    async def test_returns_original_for_unknown_mac(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        from unifi_access_api.models.websocket import WebsocketMessage

        mock_session.request.return_value = make_mock_response(
            json_data=_make_success_response(SAMPLE_DEVICE_GROUPS)
        )
        await api_client.get_device_door_map()
        msg = WebsocketMessage(event="access.logs.add", event_object_id="000000000000")
        result = api_client._enrich_ws_message(msg)
        assert result is msg


# ---------------------------------------------------------------------------
# User operations
# ---------------------------------------------------------------------------

SAMPLE_USER_RAW = {
    "id": "user-001",
    "name": "John Doe",
    "first_name": "John",
    "last_name": "Doe",
    "email": "john@example.com",
    "employee_number": "EMP001",
    "status": "ACTIVE",
}

SAMPLE_USER_INACTIVE_RAW = {
    "id": "user-002",
    "name": "Jane Smith",
    "first_name": "Jane",
    "last_name": "Smith",
    "email": "jane@example.com",
    "status": "DEACTIVATED",
}


class TestGetUsers:
    async def test_success_list(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response([SAMPLE_USER_RAW, SAMPLE_USER_INACTIVE_RAW])
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        users = await api_client.get_users()
        assert len(users) == 2
        assert isinstance(users[0], User)
        assert users[0].id == "user-001"
        assert users[0].status == UserStatus.ACTIVE
        assert users[1].id == "user-002"
        assert users[1].status == UserStatus.DEACTIVATED

    async def test_empty_list(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response([])
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        users = await api_client.get_users()
        assert users == []

    async def test_calls_users_url(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response([])
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.get_users()
        call_args = mock_session.request.call_args
        url = call_args[0][1] if call_args[0] else call_args[1]["url"]
        assert USERS_URL in url

    async def test_auth_error(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(return_value=make_mock_response(status=401))
        with pytest.raises(ApiAuthError):
            await api_client.get_users()

    async def test_connection_error(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(side_effect=OSError("down"))
        with pytest.raises(ApiConnectionError):
            await api_client.get_users()


class TestUpdateUserStatus:
    async def test_enable_success(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.update_user_status("user-001", enabled=True)
        call_args = mock_session.request.call_args
        method = call_args[0][0]
        url = call_args[0][1]
        body = call_args[1]["json"]
        assert method == "PUT"
        assert USER_URL.format(user_id="user-001") in url
        assert body == {"status": "ACTIVE"}

    async def test_disable_success(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.update_user_status("user-002", enabled=False)
        call_args = mock_session.request.call_args
        body = call_args[1]["json"]
        assert body == {"status": "DEACTIVATED"}

    async def test_user_not_found(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(
            return_value=make_mock_response(status=404, text_data="Not found")
        )
        with pytest.raises(ApiNotFoundError):
            await api_client.update_user_status("bad-id", enabled=True)

    async def test_auth_error(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(return_value=make_mock_response(status=401))
        with pytest.raises(ApiAuthError):
            await api_client.update_user_status("user-001", enabled=True)


class TestUpdateUserPin:
    async def test_set_pin(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.update_user_pin("user-001", "1234")
        call_args = mock_session.request.call_args
        method = call_args[0][0]
        url = call_args[0][1]
        body = call_args[1]["json"]
        assert method == "PUT"
        assert USER_PIN_CODES_URL.format(user_id="user-001") in url
        assert body == {"pin_code": "1234"}

    async def test_clear_pin_none(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.update_user_pin("user-001", None)
        call_args = mock_session.request.call_args
        method = call_args[0][0]
        url = call_args[0][1]
        assert method == "DELETE"
        assert USER_PIN_CODES_URL.format(user_id="user-001") in url

    async def test_clear_pin_empty_string(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.update_user_pin("user-001", "")
        call_args = mock_session.request.call_args
        method = call_args[0][0]
        assert method == "DELETE"

    async def test_auth_error(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(return_value=make_mock_response(status=401))
        with pytest.raises(ApiAuthError):
            await api_client.update_user_pin("user-001", "1234")


# ---------------------------------------------------------------------------
# get/put device settings
# ---------------------------------------------------------------------------

SAMPLE_DEVICE_SETTINGS_RAW = {
    "device_id": "dev-001",
    "access_methods": {
        "face": {
            "enabled": "no",
            "anti_spoofing_level": "high",
            "detect_distance": "near",
        },
        "nfc": {"enabled": "yes"},
        "pin_code": {"enabled": "yes", "pin_code_shuffle": "no"},
        "bt_tap": {"enabled": "yes"},
        "bt_button": {"enabled": "yes"},
        "qr_code": {"enabled": "no"},
        "touch_pass": {"enabled": "no"},
    },
}


class TestDeviceSettingsOps:
    async def test_get_device_settings(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(SAMPLE_DEVICE_SETTINGS_RAW)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        result = await api_client.get_device_settings("dev-001")
        assert isinstance(result, DeviceSettings)
        assert result.device_id == "dev-001"
        assert result.access_methods.face.enabled == "no"
        assert result.access_methods.face.anti_spoofing_level == "high"
        assert result.access_methods.nfc.enabled == "yes"
        call_args = mock_session.request.call_args
        assert "dev-001/settings" in call_args[0][1]
        assert call_args[0][0] == "GET"

    async def test_get_device_settings_uses_correct_url(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(SAMPLE_DEVICE_SETTINGS_RAW)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.get_device_settings("abc-123")
        call_args = mock_session.request.call_args
        assert DEVICE_SETTINGS_URL.format(device_id="abc-123") in call_args[0][1]

    async def test_put_device_settings_enable_face(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.put_device_settings(
            "dev-001", {"face": {"enabled": "yes", "anti_spoofing_level": "high"}}
        )
        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs["json"] == {
            "access_methods": {
                "face": {"enabled": "yes", "anti_spoofing_level": "high"}
            }
        }
        assert mock_session.request.call_args[0][0] == "PUT"

    async def test_put_device_settings_disable_face(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        data = _make_success_response(None)
        mock_session.request = MagicMock(
            return_value=make_mock_response(json_data=data)
        )
        await api_client.put_device_settings("dev-001", {"face": {"enabled": "no"}})
        call_kwargs = mock_session.request.call_args[1]
        assert call_kwargs["json"] == {"access_methods": {"face": {"enabled": "no"}}}

    async def test_get_device_settings_auth_error(
        self, api_client: UnifiAccessApiClient, mock_session: AsyncMock
    ) -> None:
        mock_session.request = MagicMock(return_value=make_mock_response(status=401))
        with pytest.raises(ApiAuthError):
            await api_client.get_device_settings("dev-001")
