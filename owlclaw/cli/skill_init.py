"""owlclaw skill init — create a new Skill from template or default scaffold."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, cast

import click
import typer
import yaml  # type: ignore[import-untyped]

from owlclaw.capabilities.tool_schema import extract_tools_schema
from owlclaw.templates.skills import (
    TemplateRegistry,
    TemplateRenderer,
    TemplateValidator,
    get_default_templates_dir,
)
from owlclaw.templates.skills.models import TemplateCategory

DEFAULT_SKILL_TEMPLATE = """---
name: {name}
description: {description}
---

# Instructions

Describe when and how to use this skill.

Optional binding snippet (uncomment when needed):

```yaml
# tools:
#   fetch-data:
#     id: string
#     binding:
#       type: http
#       method: GET
#       url: https://api.example.com/items/{{id}}
```
"""

_FRONTMATTER_PATTERN = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n(.*))?$", re.DOTALL)


def _parse_param_args(param_args: list[str]) -> dict[str, Any]:
    """Parse --param key=value into a dict."""
    result: dict[str, Any] = {}
    for s in param_args:
        if "=" not in s:
            raise ValueError(f"Invalid --param entry (expected key=value): {s}")
        k, _, v = s.partition("=")
        key = k.strip()
        if not key:
            raise ValueError(f"Invalid --param entry (empty key): {s}")
        result[key] = v.strip()
    return result


def _load_params_file(path: Path) -> dict[str, Any]:
    """Load params from JSON or YAML file."""
    import json

    import yaml  # type: ignore[import-untyped]

    content = path.read_text(encoding="utf-8")
    loaded: Any
    if path.suffix.lower() in (".json",):
        loaded = json.loads(content)
    elif path.suffix.lower() in (".yaml", ".yml"):
        loaded = yaml.safe_load(content) or {}
    else:
        raise ValueError(f"Unsupported params file format: {path.suffix}")
    if not isinstance(loaded, dict):
        raise ValueError("params file must contain an object")
    return cast(dict[str, Any], loaded)


def _run_interactive_wizard(
    registry: TemplateRegistry,
    category: TemplateCategory | None,
    prefill_name: str | None,
) -> tuple[str, dict[str, Any]]:
    """Interactive wizard: select template, collect parameters. Returns (template_id, params)."""
    templates = registry.list_templates(category=category)
    if not templates:
        typer.echo("No templates found.", err=True)
        raise typer.Exit(1)

    # Show category/template selection
    typer.echo("\nAvailable templates:")
    for i, t in enumerate(templates, 1):
        typer.echo(f"  {i}. {t.id} — {t.name}")

    choices = [str(i) for i in range(1, len(templates) + 1)]
    sel = typer.prompt(
        f"Select template (1-{len(templates)})",
        default="1",
        type=click.Choice(choices),
    )
    meta = templates[int(sel) - 1]

    # Collect parameters
    params: dict[str, Any] = {}
    for p in meta.parameters:
        default_val: Any = p.default
        if p.name == "skill_name" and prefill_name:
            default_val = prefill_name
        if p.required or default_val is None:
            prompt_text = p.description or p.name
            if default_val is not None:
                val = typer.prompt(prompt_text, default=str(default_val))
            else:
                val = typer.prompt(prompt_text)
            params[p.name] = val
        else:
            params[p.name] = default_val

    return meta.id, params


def _normalize_skill_name(raw_name: str) -> str:
    """Normalize user input into kebab-case skill name."""
    kebab = re.sub(r"[^\w\s-]", "", str(raw_name))
    kebab = re.sub(r"[\s_]+", "-", kebab).strip("-").lower()
    return kebab


def _resolve_skill_file(path: Path) -> Path:
    if path.is_file():
        return path
    return path / "SKILL.md"


def _load_frontmatter(skill_file: Path) -> dict[str, Any]:
    content = skill_file.read_text(encoding="utf-8").lstrip("\ufeff")
    match = _FRONTMATTER_PATTERN.match(content)
    if not match:
        raise ValueError(f"Invalid SKILL.md frontmatter format: {skill_file}")
    frontmatter_raw = match.group(1)
    payload = yaml.safe_load(frontmatter_raw)
    if not isinstance(payload, dict):
        raise ValueError(f"Frontmatter must be a mapping: {skill_file}")
    return cast(dict[str, Any], payload)


def _build_from_binding_content(skill_name: str, description: str, frontmatter: dict[str, Any]) -> str:
    tools_schema, _errors = extract_tools_schema(frontmatter)
    binding_tools: list[str] = []
    for tool_name, tool_def in tools_schema.items():
        if isinstance(tool_def, dict) and isinstance(tool_def.get("binding"), dict):
            binding_tools.append(tool_name)
    if not binding_tools:
        raise ValueError("source SKILL.md does not contain binding tools")

    metadata = frontmatter.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    owlclaw_config = frontmatter.get("owlclaw", {})
    if not isinstance(owlclaw_config, dict):
        owlclaw_config = {}

    output_frontmatter = {
        "name": skill_name,
        "description": description,
        "metadata": metadata,
        "owlclaw": owlclaw_config,
    }
    header = yaml.safe_dump(output_frontmatter, allow_unicode=True, sort_keys=False).strip()
    tool_lines = "\n".join(f"- `{tool_name}`: describe when to call this tool." for tool_name in sorted(binding_tools))
    body = (
        "# Instructions\n\n"
        "## Tool Decision Guide\n\n"
        f"{tool_lines}\n\n"
        "## Business Rules\n\n"
        "1. Describe trigger conditions in natural language.\n"
        "2. Define validation and risk checks before tool calls.\n"
        "3. Define post-call actions and exception handling.\n"
    )
    return f"---\n{header}\n---\n\n{body}"


def init_command(
    name: Annotated[str, typer.Option("--name", help="Skill name.", is_flag=False)] = "",
    description: Annotated[str, typer.Option("--description", help="Skill description for minimal mode.", is_flag=False)] = "",
    path: Annotated[str, typer.Option("--output", "--path", "-o", "-p", help="Output directory.", is_flag=False)] = ".",
    template: Annotated[
        str,
        typer.Option(
            "--template",
            help="Template ID (e.g. monitoring/health-check). Leave empty for interactive wizard. Use 'default' for legacy scaffold.",
            is_flag=False,
        ),
    ] = "",
    category: Annotated[
        str,
        typer.Option("--category", "-c", help="Filter templates by category (monitoring, analysis, workflow, integration, report).", is_flag=False),
    ] = "",
    params_file: Annotated[
        str,
        typer.Option("--params-file", help="JSON or YAML file path with template parameters (non-interactive).", is_flag=False),
    ] = "",
    param: Annotated[
        str,
        typer.Option("--param", help="Template parameters as key=value, comma-separated (e.g. skill_name=X,skill_description=Y).", is_flag=False),
    ] = "",
    no_minimal: Annotated[
        bool,
        typer.Option(
            "--no-minimal",
            help="Disable default minimal scaffold and use template wizard when --template is not set.",
        ),
    ] = False,
    from_binding: Annotated[
        str,
        typer.Option(
            "--from-binding",
            help="Generate a business-rules template from an existing binding SKILL.md path.",
            is_flag=False,
        ),
    ] = "",
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite existing SKILL.md if present.")] = False,
) -> None:
    """Create a new Skill directory and SKILL.md from template or default scaffold."""
    base = Path(path).resolve()
    if base.exists() and not base.is_dir():
        typer.echo(f"Error: output path is not a directory: {base}", err=True)
        raise typer.Exit(2)
    if not base.is_dir():
        base.mkdir(parents=True, exist_ok=True)

    validator = TemplateValidator()

    def _validate_generated_skill(skill_file: Path) -> None:
        errs = validator.validate_skill_file(skill_file)
        if errs:
            for e in errs:
                level = "Error" if e.severity == "error" else "Warning"
                typer.echo(f"{level}: {e.field}: {e.message}", err=True)
            if any(e.severity == "error" for e in errs):
                raise typer.Exit(1)

    if from_binding.strip():
        source_path = Path(from_binding).resolve()
        source_skill_file = _resolve_skill_file(source_path)
        if not source_skill_file.exists():
            typer.echo(f"Error: binding source SKILL.md not found: {source_skill_file}", err=True)
            raise typer.Exit(2)
        try:
            source_frontmatter = _load_frontmatter(source_skill_file)
        except Exception as e:
            typer.echo(f"Error: failed to read binding source: {e}", err=True)
            raise typer.Exit(2) from e

        source_name = str(source_frontmatter.get("name", "")).strip() or "binding-skill"
        normalized_name = _normalize_skill_name(name.strip() or source_name)
        if not normalized_name:
            typer.echo("Error: --name must contain at least one alphanumeric character.", err=True)
            raise typer.Exit(2)
        resolved_description = description.strip() or f"Business rules for {source_name}"
        if not resolved_description:
            typer.echo("Error: description must be a non-empty string.", err=True)
            raise typer.Exit(2)

        skill_dir = Path(path).resolve() / normalized_name
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists() and not force:
            typer.echo(f"Error: {skill_file} already exists. Use --force to overwrite.", err=True)
            raise typer.Exit(2)
        skill_dir.mkdir(parents=True, exist_ok=True)
        try:
            content = _build_from_binding_content(normalized_name, resolved_description, source_frontmatter)
        except Exception as e:
            typer.echo(f"Error: failed to build from binding source: {e}", err=True)
            raise typer.Exit(2) from e
        skill_file.write_text(content, encoding="utf-8")
        _validate_generated_skill(skill_file)
        typer.echo(f"Created: {skill_file}")
        return

    # Minimal mode is the default scaffold. Template mode is enabled only with explicit non-default template.
    use_template_library = bool(template and template != "default")
    if not use_template_library and (params_file or param.strip()):
        typer.echo(
            "Error: --params-file/--param require --template in non-minimal mode.",
            err=True,
        )
        raise typer.Exit(2)

    use_minimal = not use_template_library and (not no_minimal or template == "default")

    if use_minimal:
        if name.strip():
            skill_name_input = name.strip()
            skill_description_input = description.strip() or f"Description for {name.strip()}."
        else:
            skill_name_input = typer.prompt("Skill name")
            skill_description_input = description.strip() or typer.prompt("Skill description")
        normalized_name = _normalize_skill_name(skill_name_input)
        if not normalized_name:
            typer.echo("Error: --name must contain at least one alphanumeric character.", err=True)
            raise typer.Exit(2)
        if not skill_description_input.strip():
            typer.echo("Error: description must be a non-empty string.", err=True)
            raise typer.Exit(2)
        skill_dir = base / normalized_name
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists() and not force:
            typer.echo(f"Error: {skill_file} already exists. Use --force to overwrite.", err=True)
            raise typer.Exit(2)
        skill_dir.mkdir(parents=True, exist_ok=True)
        content = DEFAULT_SKILL_TEMPLATE.format(name=normalized_name, description=skill_description_input.strip())
        skill_file.write_text(content, encoding="utf-8")
        _validate_generated_skill(skill_file)
        typer.echo(f"Created: {skill_file}")
        return

    # Template library mode
    templates_dir = get_default_templates_dir()
    registry = TemplateRegistry(templates_dir)
    renderer = TemplateRenderer(registry)

    cat_enum: TemplateCategory | None = None
    if category:
        try:
            cat_enum = TemplateCategory(category.strip().lower())
        except ValueError:
            typer.echo(f"Error: invalid category '{category}'. Use: monitoring, analysis, workflow, integration, report.", err=True)
            raise typer.Exit(2) from None

    params: dict[str, Any] = {}
    template_id: str
    non_interactive = bool(params_file or param.strip())

    if params_file:
        params_path = Path(params_file)
        if not params_path.exists():
            typer.echo(f"Error: params file not found: {params_path}", err=True)
            raise typer.Exit(2)
        try:
            loaded = _load_params_file(params_path)
        except Exception as e:
            typer.echo(f"Error: cannot load params file: {e}", err=True)
            raise typer.Exit(2) from e
        if not isinstance(loaded, dict):
            typer.echo("Error: params file must contain a JSON/YAML object.", err=True)
            raise typer.Exit(2)
        params = loaded
    if param.strip():
        # Parse "k1=v1,k2=v2" or "k1=v1"
        parts = [p.strip() for p in param.split(",") if p.strip()]
        try:
            params.update(_parse_param_args(parts))
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(2) from e
    if non_interactive and not template:
        typer.echo(
            "Error: --template is required in non-interactive mode when using --params-file or --param.",
            err=True,
        )
        raise typer.Exit(2)

    if not template:
        # Full interactive wizard
        template_id, wiz_params = _run_interactive_wizard(registry, cat_enum, name)
        params.update(wiz_params)
    else:
        meta = registry.get_template(template)
        if meta is None:
            typer.echo(f"Error: template not found: {template}", err=True)
            raise typer.Exit(2)
        template_id = template
        missing_required: list[str] = []
        # Prompt for missing required params
        for param_def in meta.parameters:
            if param_def.required and param_def.name not in params:
                if non_interactive:
                    missing_required.append(param_def.name)
                    continue
                default_val = param_def.default
                if param_def.name == "skill_name" and name.strip():
                    default_val = name
                prompt_text = param_def.description or param_def.name
                if default_val is not None:
                    val = typer.prompt(prompt_text, default=str(default_val))
                else:
                    val = typer.prompt(prompt_text)
                params[param_def.name] = val
            elif not param_def.required and param_def.default is not None and param_def.name not in params:
                params[param_def.name] = param_def.default
        if missing_required:
            typer.echo(
                "Error: missing required template parameters in non-interactive mode: "
                + ", ".join(missing_required),
                err=True,
            )
            raise typer.Exit(2)

    content = renderer.render(template_id, params)

    # Determine output path: use skill_name (kebab) as subdir
    skill_name_val = params.get("skill_name") or (name.strip() or None)
    if not skill_name_val:
        typer.echo("Error: skill_name is required.", err=True)
        raise typer.Exit(2)

    # kebab-case for directory name
    kebab = _normalize_skill_name(str(skill_name_val))
    if not kebab:
        typer.echo("Error: skill_name must contain at least one alphanumeric character.", err=True)
        raise typer.Exit(2)

    skill_dir = base / kebab
    skill_file = skill_dir / "SKILL.md"

    if skill_file.exists() and not force:
        typer.echo(f"Error: {skill_file} already exists. Use --force to overwrite.", err=True)
        raise typer.Exit(2)

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(content, encoding="utf-8")

    _validate_generated_skill(skill_file)
    typer.echo(f"Created: {skill_file}")
