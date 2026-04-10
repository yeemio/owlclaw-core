"""owlclaw skill list — list Skills in directory. skill templates — list templates from library."""

import json
from pathlib import Path
from typing import Annotated

import typer

from owlclaw.capabilities.skills import SkillsLoader
from owlclaw.templates.skills import (
    TemplateRegistry,
    TemplateSearcher,
    get_default_templates_dir,
)
from owlclaw.templates.skills.models import TemplateCategory


def _list_skills_in_dir(base: Path) -> None:
    """List Skills (SKILL.md) found under the given path."""
    loader = SkillsLoader(base)
    skills = loader.scan()
    if not skills:
        typer.echo("No skills found.")
        return
    max_desc_len = 60
    for s in skills:
        desc = (s.description[:max_desc_len] + "…") if len(s.description) > max_desc_len else s.description
        typer.echo(f"  {s.name}: {desc}")


def list_command(
    path: Annotated[str, typer.Option("--path", "-p", help="Directory to scan for SKILL.md files.", is_flag=False)] = ".",
) -> None:
    """List Skills (name and description) found under the given path."""
    base = Path(path).resolve()
    if not base.is_dir():
        typer.echo(f"Error: path is not a directory: {base}", err=True)
        raise typer.Exit(2)
    _list_skills_in_dir(base)


def _list_templates(
    registry: TemplateRegistry,
    searcher: TemplateSearcher,
    category: str,
    tags: str,
    search: str,
    show: str,
    verbose: bool,
    json_output: bool,
) -> None:
    """List templates from the template library."""
    cat_enum: TemplateCategory | None = None
    if category:
        try:
            cat_enum = TemplateCategory(category.strip().lower())
        except ValueError:
            typer.echo(f"Error: invalid category '{category}'. Use: monitoring, analysis, workflow, integration, report.", err=True)
            raise typer.Exit(2) from None

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    if show:
        meta = registry.get_template(show)
        if meta is None:
            typer.echo(f"Error: template not found: {show}", err=True)
            raise typer.Exit(2) from None
        if json_output:
            out = {
                "id": meta.id,
                "name": meta.name,
                "category": meta.category.value,
                "description": meta.description,
                "tags": meta.tags,
                "parameters": [
                    {"name": p.name, "type": p.type, "description": p.description, "required": p.required, "default": p.default, "choices": p.choices}
                    for p in meta.parameters
                ],
                "examples": meta.examples,
                "file_path": str(meta.file_path),
            }
            typer.echo(json.dumps(out, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Template: {meta.id}")
            typer.echo(f"  Name: {meta.name}")
            typer.echo(f"  Category: {meta.category.value}")
            typer.echo(f"  Description: {meta.description}")
            typer.echo(f"  Tags: {', '.join(meta.tags)}")
            typer.echo("  Parameters:")
            for p in meta.parameters:
                req = "required" if p.required else f"default={p.default}"
                typer.echo(f"    - {p.name} ({p.type}, {req}): {p.description}")
            typer.echo("  Examples:")
            for ex in meta.examples:
                typer.echo(f"    - {ex}")
            typer.echo(f"  File: {meta.file_path}")
        return

    if search:
        results = searcher.search(search, category=cat_enum, tags=tag_list, limit=50)
        templates = [r.template for r in results]
    else:
        templates = registry.list_templates(category=cat_enum, tags=tag_list)

    if not templates:
        typer.echo("No templates found.")
        return

    if json_output:
        rows = []
        for t in templates:
            row: dict[str, object] = {
                "id": t.id,
                "name": t.name,
                "category": t.category.value,
                "description": t.description,
            }
            if verbose:
                row["tags"] = t.tags
                row["parameters"] = [p.name for p in t.parameters]
                row["examples"] = t.examples
            rows.append(row)
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    col_id = max(max(len(t.id) for t in templates), 4)
    col_name = max(max(len(t.name) for t in templates), 4)
    for t in templates:
        desc = t.description[:50] + "…" if len(t.description) > 50 else t.description
        typer.echo(f"  {t.id:<{col_id}}  {t.name:<{col_name}}  {desc}")
        if verbose:
            typer.echo(f"    tags: {', '.join(t.tags)}")
            typer.echo(f"    params: {', '.join(p.name for p in t.parameters)}")


def templates_command(
    category: Annotated[str, typer.Option("--category", "-c", help="Filter by category.", is_flag=False)] = "",
    tags: Annotated[str, typer.Option("--tags", help="Filter by tags, comma-separated.", is_flag=False)] = "",
    search: Annotated[str, typer.Option("--search", "-s", help="Search by name, description, or tags.", is_flag=False)] = "",
    show: Annotated[str, typer.Option("--show", help="Show details for template by ID.", is_flag=False)] = "",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show detailed info.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output in JSON format.")] = False,
) -> None:
    """List templates from the template library."""
    registry = TemplateRegistry(get_default_templates_dir())
    searcher = TemplateSearcher(registry)
    _list_templates(registry, searcher, category, tags, search, show, verbose, json_output)
