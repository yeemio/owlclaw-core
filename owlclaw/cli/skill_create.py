"""owlclaw skill create — conversational SKILL.md creation."""

from __future__ import annotations

from pathlib import Path

import typer

from owlclaw.capabilities.skill_doc_extractor import SkillDocExtractor
from owlclaw.capabilities.skill_creator import SkillConversationState, SkillCreatorAgent
from owlclaw.capabilities.skills import SkillsLoader
from owlclaw.cli.skill_templates import load_template


def _collect_capability_names(path: Path) -> list[str]:
    if not path.exists():
        return []
    loader = SkillsLoader(path)
    return [skill.name for skill in loader.scan()]


def create_command(
    interactive: bool = typer.Option(False, "--interactive", help="Start conversational creation flow."),
    from_template: str | None = typer.Option(None, "--from-template", help="Create skill from local template name."),
    from_doc: str | None = typer.Option(None, "--from-doc", help="Generate from business document (markdown/text)."),
    output: str = typer.Option("skills", "--output", help="Output directory for generated skill."),
    capabilities_path: str = typer.Option("skills", "--capabilities-path", help="Path used to discover existing capabilities."),
) -> None:
    """Create SKILL.md using interactive mode or templates."""
    if from_doc:
        extractor = SkillDocExtractor()
        try:
            written = extractor.generate_from_document(from_doc.strip(), output)
        except FileNotFoundError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(2) from exc
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(2) from exc
        if not written:
            typer.echo("No automatable sections found in source document.", err=True)
            raise typer.Exit(1)
        typer.echo(f"Generated {len(written)} skill file(s):")
        for path in written:
            typer.echo(f"- {path}")
        return

    out_dir = Path(output).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if from_template:
        content = load_template(from_template.strip())
        name_line = next((line for line in content.splitlines() if line.startswith("name: ")), "name: generated-skill")
        skill_name = name_line.replace("name: ", "", 1).strip()
        target_dir = out_dir / skill_name
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "SKILL.md"
        target_file.write_text(content, encoding="utf-8")
        typer.echo(f"Generated from template: {target_file}")
        return

    if not interactive:
        typer.echo("Error: use --interactive or --from-template.", err=True)
        raise typer.Exit(2)

    capabilities = _collect_capability_names(Path(capabilities_path).expanduser())
    creator = SkillCreatorAgent(available_capabilities=capabilities)
    state = SkillConversationState()

    typer.echo("OwlClaw Skill Creator\n")
    if capabilities:
        typer.echo(f"Detected capabilities: {', '.join(capabilities)}\n")
    first = typer.prompt("请描述你想让 Agent 做什么")
    creator.update_state_from_user_input(state, first)

    rounds = 0
    while rounds < creator.MAX_ROUNDS and not creator.is_complete(state):
        q = creator.next_question(state)
        if not q:
            break
        answer = typer.prompt(q)
        creator.update_state_from_user_input(state, answer)
        rounds += 1

    if not creator.is_complete(state):
        typer.echo("Error: 信息不足，无法生成 SKILL.md。请补充触发条件和核心目标。", err=True)
        raise typer.Exit(2)

    rendered = creator.generate_skill_markdown(state)
    preview = typer.confirm("已生成 SKILL.md，是否预览？", default=True)
    if preview:
        typer.echo("\n" + rendered)
    save = typer.confirm("是否保存到文件？", default=True)
    if not save:
        typer.echo("Cancelled.")
        raise typer.Exit(1)

    skill_name = next(
        (line.replace("name: ", "", 1).strip() for line in rendered.splitlines() if line.startswith("name: ")),
        "generated-skill",
    )
    target_dir = out_dir / skill_name
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "SKILL.md"
    target_file.write_text(rendered, encoding="utf-8")
    typer.echo(f"Saved: {target_file}")
