fix: flatten extras thumbnail fields onto Door model

## What

The UniFi Access REST API (`GET /api/v1/developer/doors`) returns thumbnail data nested under `extras`:

```json
{
  "id": "9ca4ff0a-...",
  "name": "Haustür Hub",
  "extras": {
    "door_thumbnail": "/preview/camera_...1773519902.png",
    "door_thumbnail_last_update": 1773519902
  }
}
```

The `Door` model had no `extras` field and no `extra = "allow"` config, so Pydantic silently discarded the thumbnail data during parsing.

## Why

The Home Assistant `unifi_access` integration calls `get_doors()` at startup to populate all door entities. Without parsing `extras`, image entities start as `unknown` and the proxy endpoint returns HTTP 500 — thumbnails only appear if a WebSocket `location_update_v2` event happens to arrive later.

## Changes

### `unifi_access_api/models/door.py`
- Add `door_thumbnail: str | None` and `door_thumbnail_last_update: int | None` fields directly on `Door`
- Add `model_validator(mode="before")` that flattens known fields from the nested `extras` dict onto the model, without mutating the input

### `tests/test_models.py`
- Extras with both thumbnail fields → flattened correctly
- Missing extras → `None` defaults
- Empty extras → `None` defaults
- Input dict not mutated by validator
- `model_dump()` → `model_validate()` round-trip preserves thumbnails
- `with_updates()` preserves and can set thumbnail fields

### `unifi_access_api/cli.py`
- Extract `_resolve_output_paths()` and `_print_listen_summary()` from `listen()` to fix pre-existing `PLR0915` (too many statements) ruff violation

## Result

`door.door_thumbnail` and `door.door_thumbnail_last_update` are available immediately after `get_doors()` — no WebSocket event needed.
