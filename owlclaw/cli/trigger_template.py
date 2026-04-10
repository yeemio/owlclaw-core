"""Trigger template generation commands."""

from __future__ import annotations

from pathlib import Path


def db_change_template_command(
    *,
    output_dir: str = ".",
    channel: str = "position_changes",
    table_name: str = "positions",
    trigger_name: str = "position_changes_trigger",
    function_name: str = "notify_position_changes",
    force: bool = False,
) -> Path:
    """Generate PostgreSQL NOTIFY trigger SQL template into target directory."""
    project_root = Path(__file__).resolve().parents[2]
    template_path = project_root / "templates" / "notify_trigger.sql"
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    output_base = Path(output_dir).resolve()
    output_base.mkdir(parents=True, exist_ok=True)
    target = output_base / f"notify_trigger_{channel}.sql"
    if target.exists() and not force:
        raise FileExistsError(f"Target file already exists: {target}")

    content = template_path.read_text(encoding="utf-8")
    rendered = (
        content.replace("{{CHANNEL}}", channel)
        .replace("{{TABLE_NAME}}", table_name)
        .replace("{{TRIGGER_NAME}}", trigger_name)
        .replace("{{FUNCTION_NAME}}", function_name)
    )
    target.write_text(rendered, encoding="utf-8")
    return target
