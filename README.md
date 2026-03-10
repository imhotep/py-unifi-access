# py-unifi-access

[![CI](https://github.com/imhotep/py-unifi-access/actions/workflows/ci.yml/badge.svg)](https://github.com/imhotep/py-unifi-access/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

Async Python client for the UniFi Access local API with WebSocket event support. Designed for Home Assistant Core integrations.

## Features

- Async REST client (`aiohttp`) for doors, lock rules, emergency status
- WebSocket with auto-reconnect and exponential backoff
- Typed Pydantic v2 models for all API responses and 8 WebSocket event types
- Stateless design for Home Assistant's `DataUpdateCoordinator` pattern

## Installation

```bash
pip install py-unifi-access
```

## Usage

```python
import aiohttp
from unifi_access_api import UnifiAccessApiClient

async with aiohttp.ClientSession() as session:
    client = UnifiAccessApiClient("192.168.1.1", "your-api-token", session)

    # Authenticate
    await client.authenticate()

    # Get all doors (keyed by ID for quick lookup)
    doors = {d.id: d for d in await client.get_doors()}
    for door in doors.values():
        print(f"{door.name}: {door.door_position_status}")

    # Unlock a specific door by ID
    await client.unlock_door("your-door-id")

    # WebSocket for real-time events
    ws = client.start_websocket({
        "access.data.device.update": lambda msg: print(msg),
    })
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
pytest tests/ --cov
```
