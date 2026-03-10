"""Shared fixtures for the py-unifi-access test suite."""

from __future__ import annotations

import ssl
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from unifi_access_api.client import UnifiAccessApiClient

# ---------------------------------------------------------------------------
# Sample API response payloads
# ---------------------------------------------------------------------------

SAMPLE_DOOR_RAW: dict[str, Any] = {
    "id": "door-001",
    "name": "Front Door",
    "full_name": "Building A / Front Door",
    "floor_id": "floor-1",
    "type": "door",
    "is_bind_hub": False,
    "door_position_status": "open",
    "door_lock_relay_status": "lock",
}

SAMPLE_DOOR_LOCKED: dict[str, Any] = {
    "id": "door-002",
    "name": "Back Door",
    "full_name": "Building A / Back Door",
    "floor_id": "floor-2",
    "type": "door",
    "is_bind_hub": True,
    "door_position_status": "close",
    "door_lock_relay_status": "unlock",
}

SAMPLE_DOOR_NULL_POSITION: dict[str, Any] = {
    "id": "door-003",
    "name": "Side Door",
    "full_name": "Side Door",
    "floor_id": "",
    "type": "door",
    "is_bind_hub": False,
    "door_position_status": None,
    "door_lock_relay_status": "lock",
}

SAMPLE_LOCK_RULE_STATUS_RAW: dict[str, Any] = {
    "type": "keep_lock",
    "ended_time": 1700000000,
}

SAMPLE_EMERGENCY_STATUS_RAW: dict[str, Any] = {
    "evacuation": True,
    "lockdown": False,
}


def _make_success_response(data: Any) -> dict[str, Any]:
    """Wrap data in a standard API success envelope."""
    return {"code": "SUCCESS", "data": data}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@pytest.fixture
def mock_session() -> AsyncMock:
    """Return a mocked aiohttp.ClientSession."""
    return AsyncMock(spec=aiohttp.ClientSession)


@pytest.fixture
def api_client(mock_session: AsyncMock) -> UnifiAccessApiClient:
    """Return a UnifiAccessApiClient wired to a mock session."""
    return UnifiAccessApiClient(
        host="192.168.1.1",
        api_token="test-api-token",
        session=mock_session,
        verify_ssl=False,
    )


def make_mock_response(
    *,
    status: int = 200,
    json_data: Any = None,
    read_data: bytes = b"",
    text_data: str = "",
    raise_on_json: Exception | None = None,
) -> AsyncMock:
    """Build a mock aiohttp response as an async context manager."""
    resp = AsyncMock(spec=aiohttp.ClientResponse)
    resp.status = status
    if raise_on_json:
        resp.json = AsyncMock(side_effect=raise_on_json)
    else:
        resp.json = AsyncMock(return_value=json_data)
    resp.read = AsyncMock(return_value=read_data)
    resp.text = AsyncMock(return_value=text_data)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx
