"""WebSocket event models and dispatch for the UniFi Access API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .door import (
    CoercedDoorPosition,
    DoorLockRuleType,
    DoorPositionStatus,
)

# -- Generic fallback (base for all event models) -------------------------


class WebsocketMessage(BaseModel, frozen=True):
    """Generic websocket message."""

    event: str | None = None

    model_config = {"extra": "allow"}


# -- WS-specific lock rule status -----------------------------------------


class WsDoorLockRuleStatus(BaseModel, frozen=True):
    """
    Door lock rule status as sent in WS location_update_v2 events.

    The WS payload uses ``until`` (unix timestamp) and includes a ``state``
    field, whereas the REST API uses ``ended_time`` without ``state``.
    """

    type: DoorLockRuleType = DoorLockRuleType.NONE
    until: int = 0
    state: str = ""

    model_config = {"extra": "allow"}


# -- location_update_v2 ---------------------------------------------------


class LocationUpdateState(BaseModel, frozen=True):
    """State data from a V2 location update websocket message."""

    dps: CoercedDoorPosition = DoorPositionStatus.NONE
    lock: str = "locked"
    remain_lock: WsDoorLockRuleStatus | None = None
    remain_unlock: WsDoorLockRuleStatus | None = None

    model_config = {"extra": "allow"}


class ThumbnailInfo(BaseModel, frozen=True):
    """Thumbnail data from a V2 location update."""

    url: str
    door_thumbnail_last_update: int

    model_config = {"extra": "allow"}


class LocationUpdateData(BaseModel, frozen=True):
    """Data payload from a V2 location update."""

    id: str
    location_type: str
    state: LocationUpdateState | None = None
    thumbnail: ThumbnailInfo | None = None

    model_config = {"extra": "allow"}


class LocationUpdateV2(WebsocketMessage, frozen=True):
    """V2 location update websocket message."""

    data: LocationUpdateData


# -- remote_view (doorbell start) -----------------------------------------


class RemoteViewData(BaseModel, frozen=True):
    """Data from a doorbell press start event."""

    door_name: str = ""
    request_id: str = ""
    channel: str = ""
    token: str = ""
    device_id: str = ""
    device_type: str = ""
    device_name: str = ""
    controller_id: str = ""
    floor_name: str = ""
    clear_request_id: str = ""
    in_or_out: str = ""
    create_time: int = 0
    reason_code: int = 0
    door_guard_ids: list[str] = []
    connected_uah_id: str = ""
    room_id: str = ""
    host_device_mac: str = ""

    model_config = {"extra": "allow"}


class RemoteView(WebsocketMessage, frozen=True):
    """Doorbell press start event."""

    data: RemoteViewData


# -- remote_view.change (doorbell stop) -----------------------------------


class RemoteViewChangeData(BaseModel, frozen=True):
    """Data from a doorbell press stop/change event."""

    reason_code: int = 0
    remote_call_request_id: str = ""

    model_config = {"extra": "allow"}


class RemoteViewChange(WebsocketMessage, frozen=True):
    """Doorbell press stop/change event."""

    data: RemoteViewChangeData


# -- device.update ---------------------------------------------------------


class DeviceUpdateDoor(BaseModel, frozen=True):
    """Door reference inside a device update."""

    unique_id: str = ""

    model_config = {"extra": "allow"}


class DeviceUpdateData(BaseModel, frozen=True):
    """Data from a device update event."""

    unique_id: str = ""
    device_type: str = ""
    door: DeviceUpdateDoor | None = None

    model_config = {"extra": "allow"}


class DeviceUpdate(WebsocketMessage, frozen=True):
    """Device update websocket event."""

    data: DeviceUpdateData


# -- logs.add (access event) ----------------------------------------------


class LogTarget(BaseModel, frozen=True):
    """Target entry in an access log event."""

    type: str = ""
    id: str = ""

    model_config = {"extra": "allow"}


class LogActor(BaseModel, frozen=True):
    """Actor in an access log event."""

    display_name: str = ""

    model_config = {"extra": "allow"}


class LogEvent(BaseModel, frozen=True):
    """Event details in an access log."""

    result: str = ""

    model_config = {"extra": "allow"}


class LogAuthentication(BaseModel, frozen=True):
    """Authentication details in an access log."""

    credential_provider: str = ""

    model_config = {"extra": "allow"}


class LogSource(BaseModel, frozen=True):
    """Source data from an access log event."""

    target: list[LogTarget] = []
    actor: LogActor = LogActor()
    event: LogEvent = LogEvent()
    authentication: LogAuthentication = LogAuthentication()

    model_config = {"extra": "allow"}


class LogAddData(BaseModel, frozen=True):
    """
    Data from an access log add event.

    The API sends ``_source`` which is aliased to ``source``.
    """

    source: LogSource = Field(default=LogSource(), alias="_source")

    model_config = {"extra": "allow", "populate_by_name": True}


class LogAdd(WebsocketMessage, frozen=True):
    """Access log add websocket event."""

    data: LogAddData


# -- hw.door_bell ----------------------------------------------------------


class HwDoorbellData(BaseModel, frozen=True):
    """Data from a hardware doorbell event."""

    door_id: str = ""
    door_name: str = ""
    request_id: str = ""

    model_config = {"extra": "allow"}


class HwDoorbell(WebsocketMessage, frozen=True):
    """Hardware doorbell websocket event."""

    data: HwDoorbellData


# -- setting.update (emergency) --------------------------------------------


class SettingUpdateData(BaseModel, frozen=True):
    """Data from a settings update event."""

    evacuation: bool = False
    lockdown: bool = False

    model_config = {"extra": "allow"}


class SettingUpdate(WebsocketMessage, frozen=True):
    """Settings update websocket event (evacuation/lockdown)."""

    data: SettingUpdateData


# -- remote_unlock (admin remote door unlock) ------------------------------


class RemoteUnlockData(BaseModel, frozen=True):
    """Data from a remote door unlock event."""

    unique_id: str = ""
    name: str = ""
    full_name: str = ""
    up_id: str = ""
    timezone: str = ""
    location_type: str = ""
    extra_type: str = ""
    level: int = 0
    work_time: str = ""
    work_time_id: str = ""
    extras: dict[str, Any] = {}

    model_config = {"extra": "allow"}


class RemoteUnlock(WebsocketMessage, frozen=True):
    """Remote door unlock websocket event."""

    data: RemoteUnlockData


# -- base.info (log counter notification) ----------------------------------


class BaseInfoData(BaseModel, frozen=True):
    """Data from a base info event."""

    top_log_count: int = 0

    model_config = {"extra": "allow"}


class BaseInfo(WebsocketMessage, frozen=True):
    """Base info websocket event (log counter notification)."""

    data: BaseInfoData


# -- Shared V2 meta / state models ----------------------------------------


class EventMeta(BaseModel, frozen=True):
    """Metadata attached to V2 websocket events."""

    object_type: str = ""
    target_field: list[str] | None = None
    all_field: bool = False
    id: str = ""
    source: str = ""

    model_config = {"extra": "allow"}


class EmergencyState(BaseModel, frozen=True):
    """Emergency state within a V2 location state."""

    software: str = "none"
    hardware: str = "none"

    model_config = {"extra": "allow"}


class V2LocationState(BaseModel, frozen=True):
    """Lock/DPS state from V2 location and device events."""

    lock: str = "locked"
    dps: CoercedDoorPosition = DoorPositionStatus.NONE
    dps_connected: bool = False
    emergency: EmergencyState = EmergencyState()
    is_unavailable: bool = False

    model_config = {"extra": "allow"}


# -- data.v2.location.update -----------------------------------------------


class V2LocationUpdateData(BaseModel, frozen=True):
    """Data from a V2 location update event."""

    id: str = ""
    location_type: str = ""
    name: str = ""
    up_id: str = ""
    extras: dict[str, Any] | None = None
    device_ids: list[str] = []
    thumbnail: ThumbnailInfo | None = None
    state: V2LocationState | None = None

    model_config = {"extra": "allow"}


class V2LocationUpdate(WebsocketMessage, frozen=True):
    """V2 location update websocket event."""

    data: V2LocationUpdateData
    meta: EventMeta | None = None


# -- data.v2.device.update -------------------------------------------------


class V2DeviceLocationState(V2LocationState, frozen=True):
    """Per-location state inside a V2 device update."""

    location_id: str = ""


class V2DeviceUpdateData(BaseModel, frozen=True):
    """Data from a V2 device update event."""

    id: str = ""
    name: str = ""
    alias: str = ""
    device_type: str = ""
    ip: str = ""
    mac: str = ""
    online: bool = False
    adopting: bool = False
    connected_hub_id: str = ""
    location_id: str = ""
    firmware: str = ""
    version: str = ""
    guid: str = ""
    start_time: int = 0
    hw_type: str = ""
    revision: str = ""
    cap: dict[str, Any] | None = None
    location_states: list[V2DeviceLocationState] = []
    category: list[str] = []

    model_config = {"extra": "allow"}


class V2DeviceUpdate(WebsocketMessage, frozen=True):
    """V2 device update websocket event."""

    data: V2DeviceUpdateData
    meta: EventMeta | None = None


# -- logs.insights.add (access insight event) ------------------------------


class InsightsMetadataEntry(BaseModel, frozen=True):
    """Single metadata entry in an insights event."""

    id: str = ""
    type: str = ""
    display_name: str = ""

    model_config = {"extra": "allow"}


class InsightsMetadata(BaseModel, frozen=True):
    """Typed metadata from an insights event for HA automations."""

    actor: InsightsMetadataEntry = InsightsMetadataEntry()
    door: InsightsMetadataEntry = InsightsMetadataEntry()
    authentication: InsightsMetadataEntry = InsightsMetadataEntry()
    device: InsightsMetadataEntry = InsightsMetadataEntry()
    building: InsightsMetadataEntry = InsightsMetadataEntry()
    camera: InsightsMetadataEntry = InsightsMetadataEntry()
    policy: InsightsMetadataEntry = InsightsMetadataEntry()
    opened_method: InsightsMetadataEntry = InsightsMetadataEntry()
    opened_direction: InsightsMetadataEntry = InsightsMetadataEntry()

    model_config = {"extra": "allow"}


class InsightsAddData(BaseModel, frozen=True):
    """Data from an access insights add event."""

    id: str = ""
    log_key: str = ""
    event_type: str = ""
    message: str = ""
    published: int = 0
    result: str = ""
    metadata: InsightsMetadata = InsightsMetadata()

    model_config = {"extra": "allow"}


class InsightsAdd(WebsocketMessage, frozen=True):
    """Access insights add event — primary event for entry/exit automations."""

    data: InsightsAddData


# -- data.location.update (legacy / V1) ------------------------------------


class LocationUpdateLegacyData(BaseModel, frozen=True):
    """Data from a legacy (V1) location update event."""

    unique_id: str = ""
    name: str = ""
    up_id: str = ""
    timezone: str = ""
    location_type: str = ""
    extra_type: str = ""
    full_name: str = ""
    level: int = 0
    work_time: str = ""
    work_time_id: str = ""
    extras: dict[str, Any] | None = None
    previous_name: list[str] | None = None

    model_config = {"extra": "allow"}


class LocationUpdateLegacy(WebsocketMessage, frozen=True):
    """Legacy (V1) location update websocket event."""

    data: LocationUpdateLegacyData


# -- WebSocket event → model dispatch ------------------------------------

_EVENT_MODELS: dict[str, type[WebsocketMessage]] = {
    "access.data.device.location_update_v2": LocationUpdateV2,
    "access.remote_view": RemoteView,
    "access.remote_view.change": RemoteViewChange,
    "access.data.device.update": DeviceUpdate,
    "access.logs.add": LogAdd,
    "access.hw.door_bell": HwDoorbell,
    "access.data.setting.update": SettingUpdate,
    "access.data.device.remote_unlock": RemoteUnlock,
    "access.base.info": BaseInfo,
    "access.data.v2.location.update": V2LocationUpdate,
    "access.data.v2.device.update": V2DeviceUpdate,
    "access.logs.insights.add": InsightsAdd,
    "access.data.location.update": LocationUpdateLegacy,
}


def create_from_unifi_dict(data: dict[str, Any]) -> WebsocketMessage:
    """
    Create a typed websocket message model from raw API data.

    Dispatches to the appropriate model class based on the ``event`` field.
    Falls back to the generic :class:`WebsocketMessage` for unknown events.
    """
    event = data.get("event", "")
    model_cls = _EVENT_MODELS.get(event, WebsocketMessage)
    return model_cls.model_validate(data)
