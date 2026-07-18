"""Tests for custom model logic — validators, coercion, dispatch."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from unifi_access_api.models.device_settings import (
    AccessMethod,
    AccessMethods,
    DeviceSettings,
    FaceAccessMethod,
)
from unifi_access_api.models.door import (
    Device,
    Door,
    DoorLockRelayStatus,
    DoorLockRule,
    DoorLockRuleType,
    DoorPositionStatus,
    _coerce_door_position,
)
from unifi_access_api.models.user import User, UserStatus
from unifi_access_api.models.websocket import (
    _EVENT_MODELS,
    BaseInfo,
    InsightsAdd,
    InsightsMetadata,
    LocationUpdateLegacy,
    LocationUpdateV2,
    LogAdd,
    LogEvent,
    LogSource,
    LogTarget,
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
# Door extras flattening
# ---------------------------------------------------------------------------


class TestDoorExtrasFlattening:
    def test_door_with_extras_flattened(self) -> None:
        """Test that extras.door_thumbnail is flattened onto Door."""
        door = Door.model_validate(
            {
                "id": "door-001",
                "name": "Front Door",
                "extras": {
                    "door_thumbnail": "/preview/thumb.png",
                    "door_thumbnail_last_update": 1700000000,
                },
            }
        )
        assert door.door_thumbnail == "/preview/thumb.png"
        assert door.door_thumbnail_last_update == 1700000000

    def test_door_without_extras(self) -> None:
        """Test that Door without extras has None thumbnails."""
        door = Door.model_validate(
            {
                "id": "door-001",
                "name": "Front Door",
            }
        )
        assert door.door_thumbnail is None
        assert door.door_thumbnail_last_update is None

    def test_door_with_empty_extras(self) -> None:
        """Test that Door with empty extras has None thumbnails."""
        door = Door.model_validate(
            {
                "id": "door-001",
                "name": "Front Door",
                "extras": {},
            }
        )
        assert door.door_thumbnail is None
        assert door.door_thumbnail_last_update is None

    def test_extras_does_not_mutate_input(self) -> None:
        """Validator must not mutate the caller's dict."""
        raw: dict[str, Any] = {
            "id": "door-001",
            "name": "Front Door",
            "extras": {"door_thumbnail": "/preview/thumb.png"},
        }
        Door.model_validate(raw)
        assert "extras" in raw

    def test_round_trip_with_thumbnail(self) -> None:
        """model_dump → model_validate must preserve thumbnail fields."""
        door = Door.model_validate(
            {
                "id": "door-001",
                "name": "Front Door",
                "extras": {
                    "door_thumbnail": "/preview/thumb.png",
                    "door_thumbnail_last_update": 1700000000,
                },
            }
        )
        restored = Door.model_validate(door.model_dump())
        assert restored == door
        assert restored.door_thumbnail == "/preview/thumb.png"
        assert restored.door_thumbnail_last_update == 1700000000

    def test_with_updates_preserves_thumbnail(self) -> None:
        """with_updates on non-thumbnail field must keep thumbnail."""
        door = Door.model_validate(
            {
                "id": "door-001",
                "name": "Front Door",
                "extras": {
                    "door_thumbnail": "/preview/thumb.png",
                    "door_thumbnail_last_update": 1700000000,
                },
            }
        )
        updated = door.with_updates(
            door_lock_relay_status=DoorLockRelayStatus.UNLOCK,
        )
        assert updated.door_thumbnail == "/preview/thumb.png"
        assert updated.door_thumbnail_last_update == 1700000000

    def test_with_updates_can_set_thumbnail(self) -> None:
        """with_updates can set thumbnail on a door that had none."""
        door = Door.model_validate({"id": "door-001", "name": "Front Door"})
        updated = door.with_updates(door_thumbnail="/preview/new.png")
        assert updated.door_thumbnail == "/preview/new.png"

    def test_extras_none_ignored(self) -> None:
        """extras: None is safely ignored."""
        door = Door.model_validate(
            {"id": "door-001", "name": "Front Door", "extras": None}
        )
        assert door.door_thumbnail is None

    def test_extras_non_dict_ignored(self) -> None:
        """extras with a non-dict value (e.g. string) is safely ignored."""
        door = Door.model_validate(
            {"id": "door-001", "name": "Front Door", "extras": "unexpected"}
        )
        assert door.door_thumbnail is None

    def test_explicit_top_level_wins_over_extras(self) -> None:
        """Explicit top-level door_thumbnail beats extras value."""
        door = Door.model_validate(
            {
                "id": "door-001",
                "name": "Front Door",
                "door_thumbnail": "/preview/explicit.png",
                "extras": {"door_thumbnail": "/preview/extras.png"},
            }
        )
        assert door.door_thumbnail == "/preview/explicit.png"


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

    def test_log_event_direction_field_not_typed(self) -> None:
        """event.direction is not a typed field — real API always sends it empty.

        The canonical way to read direction from a logs.add event is
        ``msg.data.source.direction`` (LogSource property), not
        ``msg.data.source.event.direction``.
        """
        raw: dict[str, Any] = {
            "event": "access.logs.add",
            "data": {
                "_source": {
                    "target": [
                        {
                            "type": "device_config",
                            "id": "door_entry_method",
                            "display_name": "entry",
                        },
                    ],
                    "actor": {"display_name": "Admin"},
                    "event": {"result": "OK"},
                }
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LogAdd)
        # canonical path
        assert msg.data.source.direction == "entry"
        # event sub-object has no typed direction field
        assert (
            not hasattr(LogEvent, "direction")
            or "direction" not in LogEvent.model_fields
        )

    def test_log_source_direction_from_target(self) -> None:
        """direction property reads from device_config target (real API pattern)."""
        raw: dict[str, Any] = {
            "event": "access.logs.add",
            "data": {
                "_source": {
                    "target": [
                        {
                            "type": "device_config",
                            "id": "door_entry_method",
                            "display_name": "entry",
                        },
                        {"type": "door", "id": "door-1", "display_name": "Front Door"},
                    ],
                    "actor": {"display_name": "Raphael"},
                    "event": {"result": "ACCESS", "direction": ""},
                }
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LogAdd)
        # event.direction is empty (as sent by real API)
        assert msg.data.source.event.direction == ""
        # but the property extracts it from the target list
        assert msg.data.source.direction == "entry"

    def test_log_source_direction_missing(self) -> None:
        """direction property returns empty string when no device_config target."""
        raw: dict[str, Any] = {
            "event": "access.logs.add",
            "data": {
                "_source": {
                    "target": [
                        {"type": "door", "id": "door-1", "display_name": "Front Door"}
                    ],
                    "event": {"result": "ACCESS"},
                }
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LogAdd)
        assert msg.data.source.direction == ""

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

    def test_parses_with_lock_rules(self) -> None:
        """V2 location update with remain_lock/remain_unlock is parsed."""
        raw: dict[str, Any] = {
            "event": "access.data.v2.location.update",
            "data": {
                "id": "loc-1",
                "location_type": "door",
                "state": {
                    "lock": "locked",
                    "dps": "close",
                    "remain_lock": {
                        "type": "keep_lock",
                        "until": 1773200000,
                        "state": "locked",
                    },
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, V2LocationUpdate)
        assert msg.data.state is not None
        assert msg.data.state.remain_lock is not None
        assert msg.data.state.remain_lock.type == DoorLockRuleType.KEEP_LOCK
        assert msg.data.state.remain_lock.until == 1773200000
        assert msg.data.state.remain_unlock is None

    def test_parses_with_remain_unlock(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.data.v2.location.update",
            "data": {
                "id": "loc-1",
                "location_type": "door",
                "state": {
                    "lock": "unlocked",
                    "dps": "open",
                    "remain_unlock": {
                        "type": "keep_unlock",
                        "until": 1773300000,
                    },
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, V2LocationUpdate)
        assert msg.data.state is not None
        assert msg.data.state.remain_unlock is not None
        assert msg.data.state.remain_unlock.type == DoorLockRuleType.KEEP_UNLOCK
        assert msg.data.state.remain_unlock.until == 1773300000
        assert msg.data.state.remain_lock is None


# ---------------------------------------------------------------------------
# LogSource.device_config convenience property
# ---------------------------------------------------------------------------


class TestLogSourceDeviceConfig:
    def test_returns_device_config_target(self) -> None:
        source = LogSource.model_validate(
            {
                "target": [
                    {"type": "door", "id": "d1"},
                    {"type": "device_config", "id": "dc1", "display_name": "entry"},
                ],
            }
        )
        dc = source.device_config
        assert dc is not None
        assert dc.type == "device_config"
        assert dc.id == "dc1"
        assert dc.display_name == "entry"

    def test_returns_none_when_absent(self) -> None:
        source = LogSource.model_validate({"target": [{"type": "door", "id": "d1"}]})
        assert source.device_config is None

    def test_returns_none_for_empty_targets(self) -> None:
        source = LogSource.model_validate({})
        assert source.device_config is None


# ---------------------------------------------------------------------------
# LogTarget.display_name field
# ---------------------------------------------------------------------------


class TestLogTargetDisplayName:
    def test_display_name_parsed(self) -> None:
        target = LogTarget.model_validate(
            {"type": "device_config", "id": "x", "display_name": "entry"}
        )
        assert target.display_name == "entry"

    def test_display_name_defaults_empty(self) -> None:
        target = LogTarget.model_validate({"type": "door", "id": "d1"})
        assert target.display_name == ""


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
                "cap": ["unlock_failure_limit", "lock", "two_factor_auth"],
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
        assert msg.data.cap == ["unlock_failure_limit", "lock", "two_factor_auth"]
        assert len(msg.data.location_states) == 1
        assert msg.data.location_states[0].location_id == "loc-1"
        assert msg.data.location_states[0].lock == "unlocked"
        assert msg.data.location_states[0].dps == DoorPositionStatus.NONE
        assert msg.meta is not None
        assert msg.meta.target_field == ["location_states"]

    def test_location_state_inherits_lock_rules(self) -> None:
        """V2DeviceLocationState inherits remain_lock/unlock from V2LocationState."""
        raw: dict[str, Any] = {
            "event": "access.data.v2.device.update",
            "data": {
                "id": "hub-1",
                "device_type": "UAH",
                "location_states": [
                    {
                        "location_id": "loc-1",
                        "lock": "locked",
                        "dps": "close",
                        "remain_lock": {
                            "type": "keep_lock",
                            "until": 1773200000,
                            "state": "locked",
                        },
                    },
                ],
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, V2DeviceUpdate)
        ls = msg.data.location_states[0]
        assert ls.location_id == "loc-1"
        assert ls.remain_lock is not None
        assert ls.remain_lock.type == DoorLockRuleType.KEEP_LOCK
        assert ls.remain_lock.until == 1773200000
        assert ls.remain_unlock is None

    def test_category_none(self) -> None:
        """category=None should not raise a validation error (#167128)."""
        raw: dict[str, Any] = {
            "event": "access.data.v2.device.update",
            "data": {
                "id": "abc123",
                "device_type": "UA-G6-Pro-Entry",
                "category": None,
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, V2DeviceUpdate)
        assert msg.data.category is None


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
        # Scalar fields
        assert msg.data.metadata.actor.display_name == "Test User"
        assert msg.data.metadata.actor.type == "user"
        assert msg.data.metadata.authentication.display_name == "FACE"
        # Target fields (lists, single dict coerced to one-element list)
        assert msg.data.metadata.door[0].display_name == "Front Door"

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
        assert msg.data.metadata.door == []

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

    def test_device_as_list(self) -> None:
        """Device arriving as a list of dicts is preserved as-is."""
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "data": {
                "result": "ACCESS",
                "metadata": {
                    "device": [
                        {
                            "id": "dev-1",
                            "type": "UA-G2-Pro",
                            "display_name": "Front Hub",
                            "alternate_name": "guid",
                        },
                    ],
                    "actor": {
                        "id": "user-1",
                        "type": "user",
                        "display_name": "Jon",
                    },
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert len(msg.data.metadata.device) == 1
        assert msg.data.metadata.device[0].id == "dev-1"
        assert msg.data.metadata.device[0].display_name == "Front Hub"
        # Scalar actor remains a single entry
        assert msg.data.metadata.actor.display_name == "Jon"

    def test_door_as_single_dict_coerced_to_list(self) -> None:
        """A single-dict target field is coerced into a one-element list."""
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "data": {
                "result": "ACCESS",
                "metadata": {
                    "door": {
                        "id": "door-1",
                        "type": "door",
                        "display_name": "Front Door",
                    },
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert len(msg.data.metadata.door) == 1
        assert msg.data.metadata.door[0].display_name == "Front Door"

    def test_multiple_targets_same_type(self) -> None:
        """Multiple targets of the same type are preserved as a list."""
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "data": {
                "result": "ACCESS",
                "metadata": {
                    "device": [
                        {"id": "dev-1", "type": "UA-Hub", "display_name": "Hub A"},
                        {
                            "id": "dev-2",
                            "type": "UA-Reader",
                            "display_name": "Reader B",
                        },
                    ],
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert len(msg.data.metadata.device) == 2
        assert msg.data.metadata.device[0].display_name == "Hub A"
        assert msg.data.metadata.device[1].display_name == "Reader B"

    def test_insights_metadata_direction_property(self) -> None:
        """direction property mirrors opened_direction[0].display_name."""
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "data": {
                "result": "ACCESS",
                "metadata": {
                    "opened_direction": {
                        "id": "door_entry_method",
                        "type": "device_config",
                        "display_name": "entry",
                    },
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert msg.data.metadata.direction == "entry"

    def test_insights_metadata_direction_empty(self) -> None:
        """direction property returns empty string when opened_direction is absent."""
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "data": {"result": "ACCESS", "metadata": {}},
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert msg.data.metadata.direction == ""


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


# ---------------------------------------------------------------------------
# WebsocketMessage.event_object_id
# ---------------------------------------------------------------------------


class TestEventObjectId:
    def test_typed_on_base_message(self) -> None:
        msg = create_from_unifi_dict(
            {
                "event": "access.base.info",
                "event_object_id": "abc123",
                "data": {"top_log_count": 1},
            }
        )
        assert msg.event_object_id == "abc123"

    def test_defaults_empty_string(self) -> None:
        msg = create_from_unifi_dict(
            {"event": "access.base.info", "data": {"top_log_count": 0}}
        )
        assert msg.event_object_id == ""


# ---------------------------------------------------------------------------
# WebsocketMessage.door_id
# ---------------------------------------------------------------------------


class TestDoorId:
    def test_defaults_empty_string(self) -> None:
        msg = create_from_unifi_dict(
            {"event": "access.base.info", "data": {"top_log_count": 0}}
        )
        assert msg.door_id == ""

    def test_preserved_via_model_copy(self) -> None:
        msg = create_from_unifi_dict(
            {
                "event": "access.logs.add",
                "event_object_id": "aabb",
                "data": {
                    "_source": {
                        "actor": {},
                        "event": {},
                        "authentication": {},
                        "target": [],
                    }
                },
            }
        )
        enriched = msg.model_copy(update={"door_id": "door-uuid-1"})
        assert enriched.door_id == "door-uuid-1"
        assert enriched.event_object_id == "aabb"
        assert enriched.event == "access.logs.add"

    def test_inherited_by_subclasses(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.logs.add",
            "event_object_id": "112233",
            "data": {
                "_source": {
                    "actor": {},
                    "event": {},
                    "authentication": {},
                    "target": [],
                }
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LogAdd)
        assert msg.door_id == ""
        enriched = msg.model_copy(update={"door_id": "d1"})
        assert isinstance(enriched, LogAdd)
        assert enriched.door_id == "d1"


# ---------------------------------------------------------------------------
# WebsocketMessage.event_object_id (continued)
# ---------------------------------------------------------------------------


class TestEventObjectIdOnSubclasses:
    def test_accessible_on_log_add(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.logs.add",
            "event_object_id": "112233445566",
            "data": {
                "_source": {
                    "actor": {},
                    "event": {},
                    "authentication": {},
                    "target": [],
                }
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LogAdd)
        assert msg.event_object_id == "112233445566"

    def test_accessible_on_insights_add(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "event_object_id": "aabbccddeeff",
            "data": {
                "event_type": "access.door.unlock",
                "result": "ACCESS",
                "metadata": {},
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert msg.event_object_id == "aabbccddeeff"


# ---------------------------------------------------------------------------
# LogEvent.type
# ---------------------------------------------------------------------------


class TestLogEventType:
    def test_type_parsed(self) -> None:
        event = LogEvent.model_validate(
            {"type": "access.door.unlock", "result": "ACCESS"}
        )
        assert event.type == "access.door.unlock"
        assert event.result == "ACCESS"

    def test_type_defaults_empty(self) -> None:
        event = LogEvent.model_validate({})
        assert event.type == ""

    def test_type_via_log_add(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.logs.add",
            "event_object_id": "112233445566",
            "data": {
                "_source": {
                    "actor": {"display_name": "Test User"},
                    "event": {"type": "access.door.unlock", "result": "ACCESS"},
                    "authentication": {"credential_provider": "NFC"},
                    "target": [],
                }
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, LogAdd)
        assert msg.data.source.event.type == "access.door.unlock"


# ---------------------------------------------------------------------------
# InsightsMetadata.camera_capture
# ---------------------------------------------------------------------------


class TestInsightsMetadataCameraCapture:
    def test_camera_capture_parsed(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "event_object_id": "aabbccddeeff",
            "data": {
                "event_type": "access.door.unlock",
                "result": "ACCESS",
                "metadata": {
                    "actor": {"display_name": "Test User"},
                    "authentication": {"display_name": "NFC"},
                    "camera": {
                        "id": "cafebabe12345678abcd0000",
                        "type": "camera",
                        "display_name": "Test Door A - Entry",
                    },
                    "camera_capture": {
                        "id": "protect_699_abc",
                        "type": "camera_capture",
                        "display_name": "Test Door A - Entry",
                    },
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert len(msg.data.metadata.camera) == 1
        assert msg.data.metadata.camera[0].id == "cafebabe12345678abcd0000"
        assert len(msg.data.metadata.camera_capture) == 1
        assert msg.data.metadata.camera_capture[0].id == "protect_699_abc"

    def test_no_camera_on_uah_door(self) -> None:
        """UAH-DOOR without linked camera has empty camera and camera_capture."""
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "event_object_id": "112233445566",
            "data": {
                "event_type": "access.door.unlock",
                "result": "ACCESS",
                "metadata": {
                    "actor": {"display_name": "Test User"},
                    "authentication": {"display_name": "NFC"},
                    "device": [
                        {
                            "id": "112233445566",
                            "type": "UAH-DOOR",
                            "display_name": "UA Hub Door",
                        }
                    ],
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert msg.data.metadata.camera == []
        assert msg.data.metadata.camera_capture == []


# ---------------------------------------------------------------------------
# Device model
# ---------------------------------------------------------------------------


class TestDevice:
    def test_basic_device(self) -> None:
        dev = Device.model_validate(
            {
                "id": "aabbccddeeff",
                "type": "UA-Hub-Door-Mini",
                "name": "UA Hub Door Mini AABB",
                "alias": "Test Door A",
                "location_id": "03665403-763f-4658-a056-336794d9114d",
                "connected_uah_id": "aabbccddeeff",
                "is_online": True,
                "is_adopted": True,
                "is_managed": True,
            }
        )
        assert dev.id == "aabbccddeeff"
        assert dev.type == "UA-Hub-Door-Mini"
        assert dev.location_id == "03665403-763f-4658-a056-336794d9114d"
        assert dev.is_online is True

    def test_extra_fields_allowed(self) -> None:
        dev = Device.model_validate({"id": "abc", "capabilities": ["ssh_change"]})
        assert dev.id == "abc"
        assert dev.model_extra["capabilities"] == ["ssh_change"]

    def test_defaults(self) -> None:
        dev = Device.model_validate({"id": "abc"})
        assert dev.type == ""
        assert dev.location_id == ""
        assert dev.is_online is False


# ---------------------------------------------------------------------------
# InsightsMetadata.reader_capture
# ---------------------------------------------------------------------------


class TestInsightsMetadataReaderCapture:
    def test_reader_capture_parsed(self) -> None:
        raw: dict[str, Any] = {
            "event": "access.logs.insights.add",
            "event_object_id": "aabbccddeeff",
            "data": {
                "event_type": "access.door.unlock",
                "result": "ACCESS",
                "metadata": {
                    "actor": {"display_name": "Test User"},
                    "authentication": {"display_name": "NFC"},
                    "reader_capture": {
                        "id": "protect_699_abc",
                        "type": "reader_capture",
                        "display_name": "Test Door A",
                        "video_source": "protect",
                    },
                },
            },
        }
        msg = create_from_unifi_dict(raw)
        assert isinstance(msg, InsightsAdd)
        assert len(msg.data.metadata.reader_capture) == 1
        assert msg.data.metadata.reader_capture[0].id == "protect_699_abc"
        assert msg.data.metadata.reader_capture[0].type == "reader_capture"

    def test_no_reader_capture_on_uah_door(self) -> None:
        meta = InsightsMetadata.model_validate(
            {
                "actor": {"display_name": "Test"},
                "authentication": {"display_name": "NFC"},
                "device": [{"id": "112233445566", "type": "UAH-DOOR"}],
            }
        )
        assert meta.reader_capture == []


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------


class TestUserStatus:
    def test_active_value(self) -> None:
        assert UserStatus.ACTIVE == "ACTIVE"

    def test_deactivated_value(self) -> None:
        assert UserStatus.DEACTIVATED == "DEACTIVATED"

    def test_pending_value(self) -> None:
        assert UserStatus.PENDING == "PENDING"


class TestUserModel:
    def test_minimal_required_fields(self) -> None:
        user = User.model_validate({"id": "abc123"})
        assert user.id == "abc123"
        assert user.name == ""
        assert user.status == UserStatus.ACTIVE
        assert user.pin_code is None

    def test_all_optional_fields(self) -> None:
        user = User.model_validate(
            {
                "id": "u1",
                "name": "John Doe",
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
                "employee_number": "EMP001",
                "status": "DEACTIVATED",
                "pin_code": "1234",
            }
        )
        assert user.first_name == "John"
        assert user.last_name == "Doe"
        assert user.email == "john@example.com"
        assert user.employee_number == "EMP001"
        assert user.status == UserStatus.DEACTIVATED
        assert user.pin_code == "1234"

    def test_is_active_true_when_active(self) -> None:
        user = User.model_validate({"id": "u1", "status": "ACTIVE"})
        assert user.is_active is True

    def test_is_active_false_when_deactivated(self) -> None:
        user = User.model_validate({"id": "u1", "status": "DEACTIVATED"})
        assert user.is_active is False

    def test_is_active_false_when_pending(self) -> None:
        user = User.model_validate({"id": "u1", "status": "PENDING"})
        assert user.is_active is False

    def test_extra_fields_allowed(self) -> None:
        # extra="allow" means unknown fields don't raise
        user = User.model_validate({"id": "u1", "some_new_api_field": "value"})
        assert user.id == "u1"

    def test_frozen_raises_on_mutation(self) -> None:
        user = User.model_validate({"id": "u1"})
        with pytest.raises(ValidationError):
            user.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DeviceSettings models
# ---------------------------------------------------------------------------


class TestAccessMethod:
    def test_is_enabled_yes(self) -> None:
        m = AccessMethod(enabled="yes")
        assert m.is_enabled is True

    def test_is_enabled_no(self) -> None:
        m = AccessMethod(enabled="no")
        assert m.is_enabled is False

    def test_default_is_disabled(self) -> None:
        m = AccessMethod()
        assert m.is_enabled is False

    def test_extra_fields_allowed(self) -> None:
        m = AccessMethod.model_validate({"enabled": "yes", "future_field": "x"})
        assert m.is_enabled is True

    def test_frozen(self) -> None:
        m = AccessMethod()
        with pytest.raises(ValidationError):
            m.enabled = "yes"  # type: ignore[misc]


class TestFaceAccessMethod:
    def test_defaults(self) -> None:
        f = FaceAccessMethod()
        assert f.enabled == "no"
        assert f.anti_spoofing_level == "high"
        assert f.detect_distance == "near"

    def test_parse_from_api_response(self) -> None:
        f = FaceAccessMethod.model_validate(
            {
                "enabled": "yes",
                "anti_spoofing_level": "medium",
                "detect_distance": "far",
            }
        )
        assert f.is_enabled is True
        assert f.anti_spoofing_level == "medium"
        assert f.detect_distance == "far"


class TestAccessMethods:
    def test_defaults(self) -> None:
        a = AccessMethods()
        assert a.nfc.is_enabled is True
        assert a.pin_code.is_enabled is True
        assert a.face.is_enabled is False
        assert a.qr_code.is_enabled is False

    def test_parse_full_api_response(self) -> None:
        raw = {
            "face": {
                "enabled": "yes",
                "anti_spoofing_level": "high",
                "detect_distance": "near",
            },
            "nfc": {"enabled": "yes"},
            "pin_code": {"enabled": "yes", "pin_code_shuffle": "no"},
            "bt_tap": {"enabled": "no"},
            "bt_button": {"enabled": "yes"},
            "qr_code": {"enabled": "no"},
            "touch_pass": {"enabled": "no"},
        }
        a = AccessMethods.model_validate(raw)
        assert a.face.is_enabled is True
        assert a.bt_tap.is_enabled is False


class TestDeviceSettings:
    def test_parse_api_response(self) -> None:
        raw = {
            "device_id": "dev-abc",
            "access_methods": {
                "face": {
                    "enabled": "no",
                    "anti_spoofing_level": "high",
                    "detect_distance": "near",
                },
                "nfc": {"enabled": "yes"},
            },
        }
        s = DeviceSettings.model_validate(raw)
        assert s.device_id == "dev-abc"
        assert s.access_methods.face.is_enabled is False
        assert s.access_methods.nfc.is_enabled is True

    def test_extra_fields_allowed(self) -> None:
        s = DeviceSettings.model_validate({"device_id": "x", "unknown": "y"})
        assert s.device_id == "x"

    def test_defaults(self) -> None:
        s = DeviceSettings()
        assert s.device_id == ""
        assert s.access_methods.face.is_enabled is False
