# py-unifi-access

[![CI](https://github.com/imhotep/py-unifi-access/actions/workflows/ci.yml/badge.svg)](https://github.com/imhotep/py-unifi-access/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

Async Python client for the UniFi Access local API with WebSocket event support. Designed for Home Assistant Core integrations.

## Features

- Async REST client (`aiohttp`) for doors, lock rules, emergency status
- WebSocket with auto-reconnect and exponential backoff
- Typed Pydantic v2 models for all API responses and 13 WebSocket event types
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

## Capturing WebSocket Messages

<a name="capturing-websocket-messages"></a>

If you encounter parsing errors or unexpected behaviour with WebSocket events,
capturing the raw message stream helps narrow down the issue.
The CLI (included in the `cli` extra) writes two files to the current directory:

| File | Content |
|---|---|
| `events_<datetime>_raw.jsonl` | All raw events, written **before** parsing |
| `events_<datetime>_parsed.jsonl` | Successfully parsed events only |

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install the package with CLI dependencies
pip install "py-unifi-access[cli]"

# 3. Record events (60 seconds, for example)
unifi-access -H 192.168.1.1 -t <TOKEN> --no-verify-ssl listen -d 60
```

Trigger the action you want to inspect (e.g. an access request) during the
capture window, then share the `events_*_raw.jsonl` file when reporting an issue.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
pytest tests/ --cov
```

## Releases

Merges to `main` now trigger an automatic release when the version in `pyproject.toml`
changes. The workflow will:

- run CI
- build the package
- publish it to PyPI
- create and push a matching Git tag
- create a GitHub release with generated notes

To make PyPI publishing work, configure a PyPI trusted publisher for this GitHub
repository and environment:

- repository: `imhotep/py-unifi-access`
- workflow file: `ci.yml`
- environment: `pypi`
