"""CLI for the UniFi Access API client."""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp

try:
    import typer
except ImportError as _exc:
    raise SystemExit(
        "CLI dependencies not installed. "
        'Install with: pip install "py-unifi-access[cli]"'
    ) from _exc

from .client import UnifiAccessApiClient
from .exceptions import UnifiAccessError
from .models.door import DoorLockRule, DoorLockRuleType, EmergencyStatus
from .models.websocket import WebsocketMessage

# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------

app = typer.Typer(rich_markup_mode="rich")

# ---------------------------------------------------------------------------
# Shared options / env vars
# ---------------------------------------------------------------------------

OPTION_HOST = typer.Option(
    None,
    "--host",
    "-H",
    help="UniFi Access IP address or hostname",
    envvar="UNA_HOST",
)
OPTION_API_TOKEN = typer.Option(
    None,
    "--api-token",
    "-t",
    help="UniFi Access API token",
    envvar="UNA_API_TOKEN",
)
OPTION_VERIFY_SSL = typer.Option(
    False,
    "--verify-ssl/--no-verify-ssl",
    help="Verify SSL certificate",
    envvar="UNA_VERIFY_SSL",
)
OPTION_TIMEOUT = typer.Option(
    10,
    "--timeout",
    help="HTTP request timeout in seconds",
    envvar="UNA_TIMEOUT",
)


# ---------------------------------------------------------------------------
# Context / helpers
# ---------------------------------------------------------------------------


@dataclass
class CliParams:
    """Connection parameters resolved in the main callback."""

    host: str
    api_token: str
    verify_ssl: bool
    timeout: int


@asynccontextmanager
async def _connect(params: CliParams) -> AsyncIterator[UnifiAccessApiClient]:
    """Yield a connected API client; guarantees cleanup of session."""
    session = aiohttp.ClientSession()
    try:
        client = UnifiAccessApiClient(
            params.host,
            params.api_token,
            session,
            verify_ssl=params.verify_ssl,
            request_timeout=params.timeout,
        )
    except Exception:
        await session.close()
        raise
    try:
        yield client
    finally:
        await client.close()
        await session.close()


def _run(coro: Coroutine[Any, Any, None]) -> None:
    """Run *coro* synchronously, converting API errors to user-friendly output."""
    try:
        asyncio.run(coro)
    except UnifiAccessError as exc:
        typer.secho(f"Error: {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc


# ---------------------------------------------------------------------------
# Main callback — resolves connection parameters (no I/O yet)
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    host: str | None = OPTION_HOST,
    api_token: str | None = OPTION_API_TOKEN,
    verify_ssl: bool = OPTION_VERIFY_SSL,
    timeout: int = OPTION_TIMEOUT,
) -> None:
    """UniFi Access CLI — interact with the local UniFi Access API."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)
    if not host:
        host = typer.prompt("Host")
    if not api_token:
        api_token = typer.prompt("API token", hide_input=True)
    ctx.obj = CliParams(
        host=host, api_token=api_token, verify_ssl=verify_ssl, timeout=timeout
    )


# ---------------------------------------------------------------------------
# doors
# ---------------------------------------------------------------------------


@app.command()
def doors(ctx: typer.Context) -> None:
    """List all doors."""

    async def _task() -> None:
        async with _connect(ctx.obj) as client:
            door_list = await client.get_doors()
            typer.echo(
                json.dumps(
                    [d.model_dump() for d in door_list],
                    indent=2,
                    ensure_ascii=False,
                )
            )

    _run(_task())


@app.command()
def door(
    ctx: typer.Context, door_id: str = typer.Argument(..., help="Door ID")
) -> None:
    """Show a specific door by ID."""

    async def _task() -> None:
        async with _connect(ctx.obj) as client:
            door_list = await client.get_doors()
            found = next((d for d in door_list if d.id == door_id), None)
            if found is None:
                typer.secho(f"Door not found: {door_id}", fg="red")
                raise typer.Exit(1)
            typer.echo(json.dumps(found.model_dump(), indent=2, ensure_ascii=False))

    _run(_task())


# ---------------------------------------------------------------------------
# unlock
# ---------------------------------------------------------------------------


@app.command()
def unlock(
    ctx: typer.Context,
    door_id: str = typer.Argument(..., help="Door ID to unlock"),
) -> None:
    """Unlock a door."""

    async def _task() -> None:
        async with _connect(ctx.obj) as client:
            await client.unlock_door(door_id)
            typer.secho(f"Door {door_id} unlocked", fg="green")

    _run(_task())


# ---------------------------------------------------------------------------
# lock-rule
# ---------------------------------------------------------------------------


@app.command("lock-rule")
def lock_rule(
    ctx: typer.Context,
    door_id: str = typer.Argument(..., help="Door ID"),
) -> None:
    """Get the current lock rule for a door."""

    async def _task() -> None:
        async with _connect(ctx.obj) as client:
            rule_status = await client.get_door_lock_rule(door_id)
            typer.echo(
                json.dumps(rule_status.model_dump(), indent=2, ensure_ascii=False)
            )

    _run(_task())


@app.command("set-lock-rule")
def set_lock_rule(
    ctx: typer.Context,
    door_id: str = typer.Argument(..., help="Door ID"),
    rule_type: str = typer.Option(
        ...,
        "--type",
        "-r",
        help="Lock rule type (keep_lock, keep_unlock, reset, ...)",
    ),
    interval: int = typer.Option(0, "--interval", "-i", help="Interval in seconds"),
) -> None:
    """Set a lock rule for a door."""

    async def _task() -> None:
        async with _connect(ctx.obj) as client:
            rule = DoorLockRule(type=DoorLockRuleType(rule_type), interval=interval)
            await client.set_door_lock_rule(door_id, rule)
            typer.secho(f"Lock rule set for door {door_id}: {rule_type}", fg="green")

    _run(_task())


# ---------------------------------------------------------------------------
# emergency
# ---------------------------------------------------------------------------


@app.command()
def emergency(ctx: typer.Context) -> None:
    """Get current emergency status."""

    async def _task() -> None:
        async with _connect(ctx.obj) as client:
            status = await client.get_emergency_status()
            typer.echo(json.dumps(status.model_dump(), indent=2, ensure_ascii=False))

    _run(_task())


@app.command("set-emergency")
def set_emergency(
    ctx: typer.Context,
    evacuation: bool = typer.Option(
        False, "--evacuation/--no-evacuation", help="Enable or disable evacuation mode"
    ),
    lockdown: bool = typer.Option(
        False, "--lockdown/--no-lockdown", help="Enable or disable lockdown mode"
    ),
) -> None:
    """Set emergency status (evacuation and/or lockdown)."""

    async def _task() -> None:
        async with _connect(ctx.obj) as client:
            status = EmergencyStatus(evacuation=evacuation, lockdown=lockdown)
            await client.set_emergency_status(status)
            typer.echo(json.dumps(status.model_dump(), indent=2, ensure_ascii=False))
            typer.secho("Emergency status updated", fg="green")

    _run(_task())


# ---------------------------------------------------------------------------
# listen (websocket)
# ---------------------------------------------------------------------------


def _resolve_output_paths(
    no_save: bool,
    output: str | None,
    raw_file: str | None,
    parsed_file: str | None,
) -> tuple[str | None, str | None]:
    """Return (raw_path, parsed_path) or (None, None) when saving is disabled."""
    if no_save:
        return None, None
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    basename = output or f"events_{ts}"
    return (
        raw_file or f"{basename}_raw.jsonl",
        parsed_file or f"{basename}_parsed.jsonl",
    )


def _print_listen_summary(rf: str | None, raw_count: int, parsed_count: int) -> None:
    """Print a summary after the listen loop finishes."""
    if rf:
        typer.secho(
            f"\n{raw_count} raw / {parsed_count} parsed events written",
            fg="green",
        )
        if raw_count > parsed_count:
            typer.secho(
                f"  {raw_count - parsed_count} event(s) failed to parse "
                f"— check {rf} for raw data",
                fg="yellow",
            )
    else:
        typer.echo("\nStopped.")


@app.command()
def listen(
    ctx: typer.Context,
    duration: int = typer.Option(
        0, "--duration", "-d", help="Seconds to listen (0 = indefinite)"
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Basename for output files (default: events_<datetime>)",
    ),
    raw_file: str | None = typer.Option(
        None, "--raw-file", help="Override path for raw JSONL output"
    ),
    parsed_file: str | None = typer.Option(
        None, "--parsed-file", help="Override path for parsed JSONL output"
    ),
    no_save: bool = typer.Option(
        False, "--no-save", help="Disable writing output files"
    ),
) -> None:
    """
    Listen to real-time websocket events.

    By default writes two JSONL files:
      events_<datetime>_raw.jsonl    — every raw event (before parsing)
      events_<datetime>_parsed.jsonl — successfully parsed events

    Use -o to set a custom basename, --raw-file / --parsed-file to override
    individual paths, or --no-save to disable file output entirely.
    """

    async def _task() -> None:
        stop_event = asyncio.Event()
        raw_count = 0
        parsed_count = 0

        rf, pf = _resolve_output_paths(no_save, output, raw_file, parsed_file)

        loop = asyncio.get_running_loop()
        with contextlib.suppress(NotImplementedError):  # Windows
            loop.add_signal_handler(signal.SIGINT, stop_event.set)

        raw_out = open(rf, "a", encoding="utf-8") if rf else None  # noqa: SIM115
        parsed_out = open(pf, "a", encoding="utf-8") if pf else None  # noqa: SIM115
        try:
            async with _connect(ctx.obj) as client:

                def on_raw(raw: dict[str, Any]) -> None:
                    nonlocal raw_count
                    if raw_out:
                        raw_out.write(json.dumps(raw, ensure_ascii=False) + "\n")
                        raw_out.flush()
                        raw_count += 1

                def on_message(msg: WebsocketMessage) -> None:
                    nonlocal parsed_count
                    dump = msg.model_dump()
                    if parsed_out:
                        parsed_out.write(json.dumps(dump, ensure_ascii=False) + "\n")
                        parsed_out.flush()
                        parsed_count += 1
                    typer.echo(json.dumps(dump, indent=2, ensure_ascii=False))

                handlers: dict[str, Any] = {"*": on_message}
                client.start_websocket(
                    handlers,
                    on_connect=lambda: typer.secho("Websocket connected", fg="green"),
                    on_disconnect=lambda: typer.secho(
                        "Websocket disconnected", fg="yellow"
                    ),
                    on_raw_message=on_raw,
                )

                if rf:
                    typer.secho(f"Raw events  -> {rf}", fg="cyan")
                    typer.secho(f"Parsed events -> {pf}", fg="cyan")
                typer.echo("Listening for events... (Ctrl+C to stop)")

                if duration > 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop_event.wait(), timeout=duration)
                else:
                    await stop_event.wait()
        finally:
            if raw_out:
                raw_out.close()
            if parsed_out:
                parsed_out.close()

        _print_listen_summary(rf, raw_count, parsed_count)

    _run(_task())


# ---------------------------------------------------------------------------
# authenticate (test connection)
# ---------------------------------------------------------------------------


@app.command()
def authenticate(ctx: typer.Context) -> None:
    """Test API connectivity and token validity."""

    async def _task() -> None:
        async with _connect(ctx.obj) as client:
            await client.authenticate()
            typer.secho("Authentication successful", fg="green")

    _run(_task())
