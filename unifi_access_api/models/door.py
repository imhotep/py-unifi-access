"""Door-related data models for the UniFi Access API."""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, field_validator, model_validator


def _coerce_door_position(v: str | None) -> str:
    """Coerce null/empty door position status to 'none'."""
    if not v:
        return "none"
    return v


class DoorPositionStatus(StrEnum):
    """Door position status."""

    OPEN = "open"
    CLOSE = "close"
    NONE = "none"


CoercedDoorPosition = Annotated[
    DoorPositionStatus, BeforeValidator(_coerce_door_position)
]


class DoorLockRelayStatus(StrEnum):
    """Door lock relay status."""

    LOCK = "lock"
    UNLOCK = "unlock"


class DoorLockRuleType(StrEnum):
    """Door lock rule type."""

    SCHEDULE = "schedule"
    KEEP_LOCK = "keep_lock"
    KEEP_UNLOCK = "keep_unlock"
    CUSTOM = "custom"
    LOCK_EARLY = "lock_early"
    LOCK_NOW = "lock_now"
    RESET = "reset"
    NONE = ""


class DoorLockRule(BaseModel):
    """Door lock rule for setting a rule on a door."""

    type: DoorLockRuleType
    interval: int = 0


class DoorLockRuleStatus(BaseModel, frozen=True):
    """Current door lock rule status from the API."""

    type: DoorLockRuleType = DoorLockRuleType.NONE
    ended_time: int = 0


class EmergencyStatus(BaseModel, frozen=True):
    """Emergency status for all doors."""

    evacuation: bool = False
    lockdown: bool = False


class Door(BaseModel, frozen=True):
    """Single door as returned by the UniFi Access API."""

    id: str
    name: str
    full_name: str = ""
    floor_id: str = ""
    type: str = "door"
    is_bind_hub: bool = False
    door_position_status: CoercedDoorPosition = DoorPositionStatus.NONE
    door_lock_relay_status: DoorLockRelayStatus = DoorLockRelayStatus.LOCK
    door_thumbnail: str | None = None
    door_thumbnail_last_update: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_extras(cls, data: Any) -> Any:
        """
        Flatten thumbnail fields from the nested ``extras`` dict.

        Known fields are promoted to top-level; ``extras`` is removed
        since ``Door`` does not use ``extra="allow"``.  ``setdefault``
        ensures an explicit top-level value always wins over ``extras``.
        """
        if isinstance(data, dict) and isinstance(extras := data.get("extras"), dict):
            data = {**data}
            data.pop("extras", None)
            data.setdefault("door_thumbnail", extras.get("door_thumbnail"))
            data.setdefault(
                "door_thumbnail_last_update",
                extras.get("door_thumbnail_last_update"),
            )
        return data

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        """Normalize door name using NFC normalization."""
        if not v:
            return ""
        return unicodedata.normalize("NFC", v.strip())

    def with_updates(self, **kwargs: object) -> Door:
        """Return a new Door with the given fields updated."""
        invalid = kwargs.keys() - self.__class__.model_fields.keys()
        if invalid:
            raise TypeError(f"Invalid field(s): {', '.join(sorted(invalid))}")
        return self.__class__.model_validate({**self.model_dump(), **kwargs})
