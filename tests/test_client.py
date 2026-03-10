"""Tests for unifi_access_api.client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from unifi_access_api.client import UnifiAccessApiClient, _map_exceptions
from unifi_access_api.const import (
    DEVICE_NOTIFICATIONS_URL,
    DOORS_URL,
    STATIC_URL,
    UNIFI_ACCESS_API_PORT,
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
from unifi_access_api.models.door import (
    Door,
    DoorLockRule,
    DoorLockRuleStatus,
    DoorLockRuleType,
    EmergencyStatus,
)

from .conftest import (
    SAMPLE_DOOR_LOCKED,
    SAMPLE_DOOR_RAW,
    SAMPLE_EMERGENCY_STATUS_RAW,
    SAMPLE_LOCK_RULE_STATUS_RAW,
    _make_success_response,
    make_mock_response,
)

# ---------------------------------------------------------------------------
# Constructor / host parsing
# ---------------------------------------------------------------------------


class TestClientInit:
    def test_plain_host(self, mock_session: AsyncMock) -> None:
        client = UnifiAccessApiClient("192.168.1.1", "tok", mock_session)
        assert client._host == f"https://192.168.1.1:{UNIFI_ACCESS_API_PORT}"
        assert client._ws_host == f"wss://192.168.1.1:{UNIFI_ACCESS_API_PORT}"

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
