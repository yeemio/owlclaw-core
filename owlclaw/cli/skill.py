"""owlclaw skill — init, validate, list (local Skills CLI)."""

import typer

from owlclaw.cli.skill_hub import (
    cache_clear_command,
    install_command,
    installed_command,
    publish_command,
    search_command,
    update_command,
)
from owlclaw.cli.skill_init import init_command
from owlclaw.cli.skill_list import list_command, templates_command
from owlclaw.cli.skill_parse import parse_command
from owlclaw.cli.skill_quality import quality_command
from owlclaw.cli.skill_templates import list_templates_command
from owlclaw.cli.skill_validate import validate_command

skill_app = typer.Typer(
    name="skill",
    help="Create, validate, and list Agent Skills (SKILL.md). Local only.",
)

skill_app.command("init")(init_command)
skill_app.command("list-templates")(list_templates_command)
skill_app.command("parse")(parse_command)
skill_app.command("quality")(quality_command)
skill_app.command("validate")(validate_command)
skill_app.command("list")(list_command)
skill_app.command("templates")(templates_command)
skill_app.command("search")(search_command)
skill_app.command("install")(install_command)
skill_app.command("installed")(installed_command)
skill_app.command("update")(update_command)
skill_app.command("publish")(publish_command)
skill_app.command("cache-clear")(cache_clear_command)
