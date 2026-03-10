"""Tests for custom model logic — validators, coercion, dispatch."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from unifi_access_api.models.door import (
    Door,
    DoorLockRelayStatus,
    DoorLockRule,
    DoorLockRuleType,
    DoorPositionStatus,
    _coerce_door_position,
)
from unifi_access_api.models.websocket import (
    _EVENT_MODELS,
    BaseInfo,
    InsightsAdd,
    LocationUpdateLegacy,
    LocationUpdateV2,
    LogAdd,
    V2DeviceUpdate,
    V2LocationUpdate,
    WebsocketMessage,
    WsDoorLockRuleStatus,
    create_from_unifi_dict,
)

# ---------------------------------------------------------------------------
# _coerce_door_position (BeforeValidator)
# ---------------------------------------------------------------------------


class TestCoerceDoorPosition:
    def test_none_coerced(self) -> None:
        assert _coerce_door_position(None) == "none"

    def test_empty_coerced(self) -> None:
        assert _coerce_door_position("") == "none"

    def test_valid_passes_through(self) -> None:
        assert _coerce_door_position("open") == "open"

    def test_coercion_in_door_model(self) -> None:
        door = Door.model_validate(
            {
                "id": "d1",
                "name": "X",
                "door_position_status": None,
            }
        )
        assert door.door_position_status == DoorPositionStatus.NONE


# ---------------------------------------------------------------------------
# Door.normalize_name (NFC + strip)
# ---------------------------------------------------------------------------


class TestNormalizeName:
    def test_strips_whitespace(self) -> None:
        door = Door.model_validate({"id": "d1", "name": "  Lobby  "})
        assert door.name == "Lobby"

    def test_nfc_normalization(self) -> None:
        nfd = "Tu\u0308r"  # NFD decomposed ü
        door = Door.model_validate({"id": "d1", "name": nfd})
        assert door.name == "Tür"  # NFC composed

    def test_empty_stays_empty(self) -> None:
        door = Door.model_validate({"id": "d1", "name": ""})
        assert door.name == ""


# ---------------------------------------------------------------------------
# DoorLockRule.model_dump(exclude_unset=True) — used by client
# ---------------------------------------------------------------------------


class TestDoorLockRuleDump:
    def test_excludes_interval_when_unset(self) -> None:
        rule = DoorLockRule(type=DoorLockRuleType.KEEP_LOCK)
        d = rule.model_dump(exclude_unset=True)
        assert d == {"type": "keep_lock"}
        assert "interval" not in d

    def test_includes_interval_when_set(self) -> None:
        rule = DoorLockRule(type=DoorLockRuleType.CUSTOM, interval=600)
        d = rule.model_dump(exclude_unset=True)
        assert d == {"type": "custom", "interval": 600}


# ---------------------------------------------------------------------------
# create_from_unifi_dict — dispatch factory
# ---------------------------------------------------------------------------


class TestCreateFromUnifiDict:
    def test_registry_has_13_events(self) -> None:
        assert len(_EVENT_MODELS) == 13

    @pytest.mark.parametrize("event", list(_EVENT_MODELS))
    def test_dispatches_to_correct_class(self, event: str) -> None:
        expected_cls = _EVENT_MODELS[event]
        # LocationUpdateData has required fields (id, location_type)
        data: dict[str, Any] = {"id": "x", "location_type": "door"}
        result = create_from_unifi_dict({"event": event, "data": data})
        assert type(result) is expected_cls

    def test_unknown_event_returns_generic(self) -> None:
        result = create_from_unifi_dict({"event": "access.unknown"})
        assert type(result) is WebsocketMessage

    def test_missing_event_returns_generic(self) -> None:
        result = create_from_unifi_dict({"some_key": 1})
        assert type(result) is WebsocketMessage
        assert result.event is None

    def test_location_update_parses_state(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.data.device.location_update_v2",
            "data": {
                "id": "loc-1",
                "location_type": "door",
                "state": {"dps": None, "lock": "locked"},
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LocationUpdateV2)
        assert msg.data.state is not None
        assert msg.data.state.dps == DoorPositionStatus.NONE

    def test_log_source_alias(self) -> None:
        """_source alias is critical for real API data."""
        raw: dict[str, Any] = {
            "event": "access.logs.add",
            "data": {
                "_source": {
                    "actor": {"display_name": "Admin"},
                    "event": {"result": "OK"},
                }
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LogAdd)
        assert msg.data.source.actor.display_name == "Admin"

    def test_log_source_by_name(self) -> None:
        """populate_by_name lets 'source' work too."""
        raw: dict[str, Any] = {
            "event": "access.logs.add",
            "data": {
                "source": {
                    "actor": {"display_name": "Jane"},
                    "event": {"result": "DENIED"},
                }
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LogAdd)
        assert msg.data.source.actor.display_name == "Jane"

    def test_event_models_inherit_websocket_message(self) -> None:
        """All event models inherit from WebsocketMessage."""
        for cls in _EVENT_MODELS.values():
            assert issubclass(cls, WebsocketMessage)

    def test_top_level_extra_allow(self) -> None:
        """Top-level event models accept extra fields."""
        raw: dict[str, Any] = {
            "event": "access.data.device.location_update_v2",
            "data": {"id": "x", "location_type": "door"},
            "new_field": "future",
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LocationUpdateV2)


# ---------------------------------------------------------------------------
# WsDoorLockRuleStatus — until alias
# ---------------------------------------------------------------------------


class TestWsDoorLockRuleStatus:
    def test_parses_ws_payload(self) -> None:
        """WS payloads use 'until' and include 'state'."""
        status = WsDoorLockRuleStatus.model_validate(
            {"type": "keep_lock", "until": 3666291264, "state": "locked"}
        )
        assert status.until == 3666291264
        assert status.state == "locked"

    def test_defaults(self) -> None:
        status = WsDoorLockRuleStatus.model_validate({})
        assert status.until == 0
        assert status.state == ""

    def test_in_location_update_state(self) -> None:
        """remain_unlock with 'until' is correctly parsed in a full WS message."""
        raw: dict[str, Any] = {
            "event": "access.data.device.location_update_v2",
            "data": {
                "id": "loc-1",
                "location_type": "door",
                "state": {
                    "lock": "locked",
                    "remain_unlock": {
                        "type": "keep_lock",
                        "until": 3666291264,
                        "state": "locked",
                    },
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LocationUpdateV2)
        assert msg.data.state is not None
        assert msg.data.state.remain_unlock is not None
        assert msg.data.state.remain_unlock.until == 3666291264


# ---------------------------------------------------------------------------
# Door.with_updates
# ---------------------------------------------------------------------------


class TestDoorWithUpdates:
    def _make_door(self) -> Door:
        return Door.model_validate(
            {
                "id": "door-001",
                "name": "Front Door",
                "door_position_status": "close",
                "door_lock_relay_status": "lock",
            }
        )

    def test_original_unchanged(self) -> None:
        door = self._make_door()
        door.with_updates(door_position_status=DoorPositionStatus.OPEN)
        assert door.door_position_status == DoorPositionStatus.CLOSE

    def test_updated_fields(self) -> None:
        door = self._make_door()
        updated = door.with_updates(
            door_position_status=DoorPositionStatus.OPEN,
            door_lock_relay_status=DoorLockRelayStatus.UNLOCK,
        )
        assert updated.door_position_status == DoorPositionStatus.OPEN
        assert updated.door_lock_relay_status == DoorLockRelayStatus.UNLOCK

    def test_non_updated_fields_preserved(self) -> None:
        door = self._make_door()
        updated = door.with_updates(door_position_status=DoorPositionStatus.OPEN)
        assert updated.id == door.id
        assert updated.name == door.name
        assert updated.door_lock_relay_status == door.door_lock_relay_status

    def test_invalid_field_raises_error(self) -> None:
        door = self._make_door()
        with pytest.raises(TypeError, match="Invalid field"):
            door.with_updates(nonexistent_field="boom")

    def test_invalid_value_raises_validation_error(self) -> None:
        door = self._make_door()
        with pytest.raises(ValidationError):
            door.with_updates(door_lock_relay_status="garbage")


# ---------------------------------------------------------------------------
# New event models — access.base.info
# ---------------------------------------------------------------------------


class TestBaseInfo:
    def test_parses_payload(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.base.info",
            "receiver_id": "",
            "event_object_id": "abc",
            "save_to_history": False,
            "data": {"top_log_count": 1},
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, BaseInfo)
        assert msg.data.top_log_count == 1


# ---------------------------------------------------------------------------
# New event models — access.data.v2.location.update
# ---------------------------------------------------------------------------


class TestV2LocationUpdate:
    def test_parses_with_state(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.data.v2.location.update",
            "data": {
                "id": "loc-1",
                "location_type": "door",
                "name": "Front Door",
                "state": {
                    "lock": "unlocked",
                    "dps": "none",
                    "dps_connected": False,
                    "emergency": {"software": "none", "hardware": "none"},
                    "is_unavailable": False,
                },
            },
            "meta": {
                "object_type": "location",
                "target_field": None,
                "all_field": False,
                "id": "loc-1",
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, V2LocationUpdate)
        assert msg.data.name == "Front Door"
        assert msg.data.state is not None
        assert msg.data.state.lock == "unlocked"
        assert msg.data.state.dps == DoorPositionStatus.NONE
        assert msg.data.state.emergency.software == "none"
        assert msg.meta is not None
        assert msg.meta.object_type == "location"

    def test_dps_coercion_null(self) -> None:
        """V2 location state coerces null DPS like LocationUpdateState."""
        raw: dict[str, Any] = {
            "event": "access.data.v2.location.update",
            "data": {
                "id": "loc-1",
                "location_type": "door",
                "state": {"dps": None, "lock": "locked"},
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, V2LocationUpdate)
        assert msg.data.state is not None
        assert msg.data.state.dps == DoorPositionStatus.NONE

    def test_parses_without_state(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.data.v2.location.update",
            "data": {
                "id": "loc-1",
                "location_type": "door",
                "device_ids": ["abc", "def"],
                "thumbnail": {
                    "url": "/preview/test.png",
                    "door_thumbnail_last_update": 123,
                },
            },
            "meta": {"object_type": "location", "target_field": ["thumbnail"]},
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, V2LocationUpdate)
        assert msg.data.state is None
        assert msg.data.device_ids == ["abc", "def"]
        assert msg.data.thumbnail is not None
        assert msg.data.thumbnail.url == "/preview/test.png"


# ---------------------------------------------------------------------------
# New event models — access.data.v2.device.update
# ---------------------------------------------------------------------------


class TestV2DeviceUpdate:
    def test_parses_payload(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.data.v2.device.update",
            "data": {
                "id": "abc123",
                "name": "UA Hub",
                "alias": "Front Hub",
                "device_type": "UA-Hub-Door-Mini",
                "online": True,
                "location_id": "loc-1",
                "firmware": "v1.4.6.0",
                "location_states": [
                    {
                        "location_id": "loc-1",
                        "lock": "unlocked",
                        "dps": "none",
                        "dps_connected": False,
                        "emergency": {"software": "none", "hardware": "none"},
                        "is_unavailable": False,
                    }
                ],
                "category": ["hub"],
            },
            "meta": {
                "object_type": "device",
                "target_field": ["location_states"],
                "id": "abc123",
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, V2DeviceUpdate)
        assert msg.data.alias == "Front Hub"
        assert msg.data.online is True
        assert len(msg.data.location_states) == 1
        assert msg.data.location_states[0].location_id == "loc-1"
        assert msg.data.location_states[0].lock == "unlocked"
        assert msg.data.location_states[0].dps == DoorPositionStatus.NONE
        assert msg.meta is not None
        assert msg.meta.target_field == ["location_states"]


# ---------------------------------------------------------------------------
# New event models — access.logs.insights.add
# ---------------------------------------------------------------------------


class TestInsightsAdd:
    def test_parses_payload(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "data": {
                "log_key": "dashboard.access.door.unlock.success",
                "event_type": "access.door.unlock",
                "message": "Access Granted (Face)",
                "published": 1773163828000,
                "result": "ACCESS",
                "metadata": {
                    "actor": {
                        "id": "user-1",
                        "type": "user",
                        "display_name": "Test User",
                    },
                    "door": {
                        "id": "door-1",
                        "type": "door",
                        "display_name": "Front Door",
                    },
                    "authentication": {
                        "id": "f4fd081c",
                        "type": "authentication",
                        "display_name": "FACE",
                    },
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert msg.data.result == "ACCESS"
        assert msg.data.event_type == "access.door.unlock"
        assert msg.data.message == "Access Granted (Face)"
        # Typed access — no dict digging needed in HA
        assert msg.data.metadata.actor.display_name == "Test User"
        assert msg.data.metadata.actor.type == "user"
        assert msg.data.metadata.door.display_name == "Front Door"
        assert msg.data.metadata.authentication.display_name == "FACE"

    def test_empty_metadata_defaults(self) -> None:
        """Missing metadata entries default to empty InsightsMetadataEntry."""
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "data": {
                "event_type": "access.door.unlock",
                "result": "ACCESS",
                "metadata": {},
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert msg.data.metadata.actor.display_name == ""
        assert msg.data.metadata.door.id == ""

    def test_extra_metadata_fields_preserved(self) -> None:
        """Unknown metadata keys are preserved via extra=allow."""
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "data": {
                "result": "ACCESS",
                "metadata": {
                    "actor": {"display_name": "Admin"},
                    "custom_new_field": {"id": "x", "type": "y"},
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert msg.data.metadata.actor.display_name == "Admin"


# ---------------------------------------------------------------------------
# New event models — access.data.location.update (legacy)
# ---------------------------------------------------------------------------


class TestLocationUpdateLegacy:
    def test_parses_payload(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.data.location.update",
            "data": {
                "unique_id": "loc-1",
                "name": "Front Door",
                "up_id": "floor-1",
                "location_type": "door",
                "full_name": "Building - 1F - Front Door",
                "extras": {"door_thumbnail": "/preview/test.png"},
                "previous_name": ["Old Name"],
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LocationUpdateLegacy)
        assert msg.data.unique_id == "loc-1"
        assert msg.data.name == "Front Door"
        assert msg.data.full_name == "Building - 1F - Front Door"
        assert msg.data.previous_name == ["Old Name"]
