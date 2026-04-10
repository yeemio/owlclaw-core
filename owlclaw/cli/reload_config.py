"""Config reload command implementation."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from owlclaw.config import ConfigManager

console = Console()


def reload_config_command(config: str | None = None) -> tuple[dict[str, object], dict[str, object]]:
    """Reload configuration and return applied/skipped changes."""
    manager = ConfigManager.instance()
    result = manager.reload(config_path=config)
    applied = result.applied
    skipped = result.skipped

    console.print("[bold]Reload Result[/bold]")
    console.print(f"Applied: {len(applied)}")
    for key, value in applied.items():
        console.print(f"  + {key} = {value!r}")

    console.print(f"Skipped: {len(skipped)}")
    for key, value in skipped.items():
        console.print(f"  - {key} = {value!r} (requires restart)")

    if config is not None and not Path(config).exists():
        console.print(f"[yellow]Note:[/yellow] config file not found, defaults/env/overrides were used: {config}")
    return applied, skipped

