"""owlclaw skill parse — parse SKILL.md and show resolved metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import typer

from owlclaw.capabilities.skills import SkillsLoader


def _collect_payload(skill: Any) -> dict[str, Any]:
    payload = cast(dict[str, Any], skill.to_dict())
    payload["file_path"] = str(skill.file_path)
    return payload


def parse_command(
    path: str = typer.Argument(".", help="Skill directory or capabilities root."),
    cache: bool = typer.Option(False, "--cache", help="Warm parse cache by scanning all skills."),
) -> None:
    """Parse SKILL.md files and print resolved parse results as JSON."""
    base = Path(path).expanduser()
    loader = SkillsLoader(base)
    skills = loader.scan()
    if not skills:
        typer.echo("[]")
        return
    if cache:
        # `scan()` already warms NL cache through parser path; this branch keeps flag explicit.
        _ = [skill.name for skill in skills]
    payload = [_collect_payload(skill) for skill in skills]
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
