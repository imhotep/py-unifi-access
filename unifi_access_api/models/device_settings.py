"""Models for UniFi Access device settings (access methods)."""

from __future__ import annotations

from pydantic import BaseModel


class AccessMethod(BaseModel, frozen=True):
    """Generic access method with an enabled flag."""

    enabled: str = "no"
    model_config = {"extra": "allow"}

    @property
    def is_enabled(self) -> bool:
        """Return True when the access method is enabled."""
        return self.enabled == "yes"


class FaceAccessMethod(AccessMethod, frozen=True):
    """Face recognition access method with additional configuration."""

    anti_spoofing_level: str = "high"
    detect_distance: str = "near"


class AccessMethods(BaseModel, frozen=True):
    """All access methods supported by a device."""

    face: FaceAccessMethod = FaceAccessMethod()
    nfc: AccessMethod = AccessMethod(enabled="yes")
    pin_code: AccessMethod = AccessMethod(enabled="yes")
    bt_tap: AccessMethod = AccessMethod(enabled="yes")
    bt_button: AccessMethod = AccessMethod(enabled="yes")
    qr_code: AccessMethod = AccessMethod()
    touch_pass: AccessMethod = AccessMethod()
    model_config = {"extra": "allow"}


class DeviceSettings(BaseModel, frozen=True):
    """Settings returned by GET /api/v1/developer/devices/{device_id}/settings."""

    device_id: str = ""
    access_methods: AccessMethods = AccessMethods()
    model_config = {"extra": "allow"}
