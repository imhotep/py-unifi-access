## refactor: production-ready async lib for HA Core integration

### Core Library
- Async aiohttp client with external session support and optional `ssl_context`
- Pydantic v2 frozen models with `extra="allow"` for forward compatibility
- Exception hierarchy (`ApiAuthError`, `ApiConnectionError`, `ApiSSLError`, `ApiForbiddenError`, `ApiNotFoundError`, `ApiRateLimitError`) for HA error handling
- `Door.with_updates()` for immutable state updates
- WebSocket with auto-reconnect, exponential backoff, heartbeat, configurable `max_retries`

### WebSocket Event Models (13 events)
- 8 documented events: `LocationUpdateV2`, `RemoteView`, `RemoteViewChange`, `DeviceUpdate`, `LogAdd`, `HwDoorbell`, `SettingUpdate`, `RemoteUnlock`
- 5 undocumented events (discovered via real device capture, not in API PDF):
  - **`InsightsAdd`** (`access.logs.insights.add`) — entry/exit event with typed `actor`, `door`, `authentication` metadata for HA automations
  - **`V2LocationUpdate`** (`access.data.v2.location.update`) — door lock/DPS/emergency state with `CoercedDoorPosition`
  - **`V2DeviceUpdate`** (`access.data.v2.device.update`) — device status with per-location lock/DPS states
  - **`LocationUpdateLegacy`** (`access.data.location.update`) — legacy V1 location update
  - **`BaseInfo`** (`access.base.info`) — log counter notification
- `create_from_unifi_dict()` dispatch factory with wildcard handler support

### CLI (optional)
- `pip install "py-unifi-access[cli]"` — typer-based CLI for testing/debugging
- Commands: `doors`, `door-detail`, `unlock`, `lock`, `listen`
- `listen --output/-o events.json` to capture WebSocket events to file

### Quality
- 145 tests (pytest-asyncio), ruff + mypy strict, `py.typed` marker
- Pre-commit hooks: ruff, ruff-format, mypy, debug-statements
- CI: GitHub Actions (lint + test matrix 3.12/3.13/3.14 + Codecov)
- Dependabot for Actions + pip weekly updates
- MIT license, `.editorconfig`
