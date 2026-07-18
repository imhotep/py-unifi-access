"""User-related data models for the UniFi Access API."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class UserStatus(StrEnum):
    """User account status."""

    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    DEACTIVATED = "DEACTIVATED"


class User(BaseModel, frozen=True):
    """Single user as returned by the UniFi Access API."""

    id: str
    name: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    employee_number: str = ""
    status: UserStatus = UserStatus.ACTIVE
    pin_code: str | None = None

    model_config = {"extra": "allow"}

    @property
    def is_active(self) -> bool:
        """Return True when the user account is active."""
        return self.status == UserStatus.ACTIVE
