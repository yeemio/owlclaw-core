"""Config template initialization command."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

console = Console()


def init_config_command(path: str = ".", force: bool = False) -> Path:
    """Create owlclaw.yaml from templates/owlclaw.yaml."""
    project_root = Path(__file__).resolve().parents[2]
    template = project_root / "templates" / "owlclaw.yaml"
    if not template.exists():
        raise FileNotFoundError(f"Template not found: {template}")

    target_dir = Path(path).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / "owlclaw.yaml"
    if output_path.exists() and not force:
        raise FileExistsError(f"Config already exists: {output_path}")
    output_path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    console.print(f"[green]Created[/green] {output_path}")
    return output_path

