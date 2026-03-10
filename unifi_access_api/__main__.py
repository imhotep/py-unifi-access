"""Allow running the CLI via ``python -m unifi_access_api``."""

from __future__ import annotations

from .cli import app

if __name__ == "__main__":
    app()
