"""CLI tools — owlclaw db, owlclaw skill, owlclaw scan, owlclaw migrate."""

import argparse
import sys
from importlib import metadata

import typer
from click.exceptions import Exit as ClickExit

from owlclaw.cli.init_config import init_config_command
from owlclaw.cli.reload_config import reload_config_command

app = typer.Typer(
    name="owlclaw",
    help="OwlClaw — Agent base for business applications.",
)

_SUBAPPS_REGISTERED = False


def _register_subapps() -> None:
    global _SUBAPPS_REGISTERED
    if _SUBAPPS_REGISTERED:
        return
    from owlclaw.cli.db import db_app
    from owlclaw.cli.memory import memory_app
    from owlclaw.cli.skill import skill_app

    app.add_typer(db_app, name="db")
    app.add_typer(memory_app, name="memory")
    app.add_typer(skill_app, name="skill")
    _SUBAPPS_REGISTERED = True

# Keep subcommands registered for direct `CliRunner.invoke(app, ...)` usage in tests
# and library callers that import the Typer app object without going through main().
_register_subapps()


def _print_version_and_exit() -> None:
    """Print installed package version and exit."""
    try:
        version = metadata.version("owlclaw")
    except metadata.PackageNotFoundError:
        version = "unknown"
    print(f"owlclaw {version}")
    raise SystemExit(0)


@app.command("init")
def init_command(
    path: str = typer.Option(".", "--path", help="Output directory"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing owlclaw.yaml"),
) -> None:
    """Generate default owlclaw.yaml in target directory."""
    init_config_command(path=path, force=force)


@app.command("reload")
def reload_command(
    config: str = typer.Option("", "--config", help="Optional config file path"),
) -> None:
    """Reload configuration and print applied/skipped changes."""
    reload_config_command(config=config or None)


def _dispatch_skill_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw skill ...` using argparse for Typer option-parse compatibility."""
    if not argv or argv[0] != "skill":
        return False

    if len(argv) < 2:
        _print_help_and_exit(["skill"])

    sub = argv[1]
    sub_argv = argv[2:]
    if "--help" in sub_argv or "-h" in sub_argv:
        _print_help_and_exit(["skill", sub])

    if sub == "init":
        from owlclaw.cli.skill_init import init_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill init")
        parser.add_argument("--name", default="")
        parser.add_argument("--description", default="")
        parser.add_argument("--output", "--path", "-o", "-p", dest="path", default=".")
        parser.add_argument("--template", default="")
        parser.add_argument("--category", "-c", default="")
        parser.add_argument("--params-file", dest="params_file", default="")
        parser.add_argument("--param", default="")
        parser.add_argument("--no-minimal", dest="no_minimal", action="store_true", default=False)
        parser.add_argument("--from-binding", dest="from_binding", default="")
        parser.add_argument("--force", "-f", action="store_true", default=False)
        ns = parser.parse_args(sub_argv)
        init_command(
            name=ns.name,
            description=ns.description,
            path=ns.path,
            template=ns.template,
            category=ns.category,
            params_file=ns.params_file,
            param=ns.param,
            no_minimal=ns.no_minimal,
            from_binding=ns.from_binding,
            force=ns.force,
        )
        return True

    if sub == "create":
        from owlclaw.cli.skill_create import create_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill create")
        parser.add_argument("--interactive", action="store_true", default=False)
        parser.add_argument("--from-template", default="")
        parser.add_argument("--from-doc", default="")
        parser.add_argument("--output", default="skills")
        parser.add_argument("--capabilities-path", default="skills")
        ns = parser.parse_args(sub_argv)
        create_command(
            interactive=ns.interactive,
            from_template=ns.from_template,
            from_doc=ns.from_doc,
            output=ns.output,
            capabilities_path=ns.capabilities_path,
        )
        return True

    if sub == "validate":
        from owlclaw.cli.skill_validate import validate_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill validate")
        parser.add_argument("paths", nargs="*", default=["."])
        parser.add_argument("--verbose", "-v", action="store_true", default=False)
        parser.add_argument("--strict", "-s", action="store_true", default=False)
        ns = parser.parse_args(sub_argv)
        validate_command(paths=ns.paths, verbose=ns.verbose, strict=ns.strict)
        return True

    if sub == "parse":
        from owlclaw.cli.skill_parse import parse_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill parse")
        parser.add_argument("path", nargs="?", default=".")
        parser.add_argument("--cache", action="store_true", default=False)
        ns = parser.parse_args(sub_argv)
        parse_command(path=ns.path, cache=ns.cache)
        return True

    if sub == "quality":
        from owlclaw.cli.skill_quality import quality_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill quality")
        parser.add_argument("skill_name", nargs="?", default="")
        parser.add_argument("--all", action="store_true", default=False)
        parser.add_argument("--trend", action="store_true", default=False)
        parser.add_argument("--period", default="30d")
        parser.add_argument("--suggest", action="store_true", default=False)
        parser.add_argument("--tenant", default="default")
        ns = parser.parse_args(sub_argv)
        quality_command(
            skill_name=ns.skill_name,
            all=ns.all,
            trend=ns.trend,
            period=ns.period,
            suggest=ns.suggest,
            tenant=ns.tenant,
        )
        return True

    if sub == "list-templates":
        from owlclaw.cli.skill_templates import list_templates_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill list-templates")
        parser.parse_args(sub_argv)
        list_templates_command()
        return True

    if sub == "list":
        from owlclaw.cli.skill_list import list_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill list")
        parser.add_argument("--path", "-p", default=".")
        ns = parser.parse_args(sub_argv)
        list_command(path=ns.path)
        return True

    if sub == "templates":
        from owlclaw.cli.skill_list import templates_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill templates")
        parser.add_argument("--category", "-c", default="")
        parser.add_argument("--tags", default="")
        parser.add_argument("--search", "-s", default="")
        parser.add_argument("--show", default="")
        parser.add_argument("--verbose", "-v", action="store_true", default=False)
        parser.add_argument("--json", dest="json_output", action="store_true", default=False)
        ns = parser.parse_args(sub_argv)
        templates_command(
            category=ns.category,
            tags=ns.tags,
            search=ns.search,
            show=ns.show,
            verbose=ns.verbose,
            json_output=ns.json_output,
        )
        return True

    if sub == "search":
        from owlclaw.cli.skill_hub import search_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill search")
        parser.add_argument("--query", "-q", default="")
        parser.add_argument("--mode", default="auto")
        parser.add_argument("--api-base-url", default="")
        parser.add_argument("--api-token", default="")
        parser.add_argument("--no-cache", action="store_true", default=False)
        parser.add_argument("--index-url", default="./index.json")
        parser.add_argument("--tags", default="")
        parser.add_argument("--industry", default="")
        parser.add_argument("--sort-by", default="name")
        parser.add_argument("--tag-mode", default="and")
        parser.add_argument("--include-draft", action="store_true", default=False)
        parser.add_argument("--install-dir", default="./.owlhub/skills")
        parser.add_argument("--lock-file", default="./skill-lock.json")
        parser.add_argument("--verbose", "-v", action="store_true", default=False)
        parser.add_argument("--quiet", action="store_true", default=False)
        ns = parser.parse_args(sub_argv)
        search_command(
            query=ns.query,
            mode=ns.mode,
            api_base_url=ns.api_base_url,
            api_token=ns.api_token,
            no_cache=ns.no_cache,
            index_url=ns.index_url,
            tags=ns.tags,
            industry=ns.industry,
            sort_by=ns.sort_by,
            tag_mode=ns.tag_mode,
            include_draft=ns.include_draft,
            install_dir=ns.install_dir,
            lock_file=ns.lock_file,
            verbose=ns.verbose,
            quiet=ns.quiet,
        )
        return True

    if sub == "install":
        from owlclaw.cli.skill_hub import install_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill install")
        parser.add_argument("name", nargs="?", default="")
        parser.add_argument("--version", default="")
        parser.add_argument("--package", default="")
        parser.add_argument("--no-deps", action="store_true", default=False)
        parser.add_argument("--force", action="store_true", default=False)
        parser.add_argument("--mode", default="auto")
        parser.add_argument("--api-base-url", default="")
        parser.add_argument("--api-token", default="")
        parser.add_argument("--no-cache", action="store_true", default=False)
        parser.add_argument("--index-url", default="./index.json")
        parser.add_argument("--install-dir", default="./.owlhub/skills")
        parser.add_argument("--lock-file", default="./skill-lock.json")
        parser.add_argument("--verbose", "-v", action="store_true", default=False)
        parser.add_argument("--quiet", action="store_true", default=False)
        ns = parser.parse_args(sub_argv)
        install_command(
            name=ns.name,
            version=ns.version,
            package=ns.package,
            no_deps=ns.no_deps,
            force=ns.force,
            mode=ns.mode,
            api_base_url=ns.api_base_url,
            api_token=ns.api_token,
            no_cache=ns.no_cache,
            index_url=ns.index_url,
            install_dir=ns.install_dir,
            lock_file=ns.lock_file,
            verbose=ns.verbose,
            quiet=ns.quiet,
        )
        return True

    if sub == "installed":
        from owlclaw.cli.skill_hub import installed_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill installed")
        parser.add_argument("--mode", default="auto")
        parser.add_argument("--api-base-url", default="")
        parser.add_argument("--api-token", default="")
        parser.add_argument("--no-cache", action="store_true", default=False)
        parser.add_argument("--index-url", default="./index.json")
        parser.add_argument("--install-dir", default="./.owlhub/skills")
        parser.add_argument("--lock-file", default="./skill-lock.json")
        parser.add_argument("--verbose", "-v", action="store_true", default=False)
        parser.add_argument("--quiet", action="store_true", default=False)
        ns = parser.parse_args(sub_argv)
        installed_command(
            mode=ns.mode,
            api_base_url=ns.api_base_url,
            api_token=ns.api_token,
            no_cache=ns.no_cache,
            index_url=ns.index_url,
            install_dir=ns.install_dir,
            lock_file=ns.lock_file,
            verbose=ns.verbose,
            quiet=ns.quiet,
        )
        return True

    if sub == "update":
        from owlclaw.cli.skill_hub import update_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill update")
        parser.add_argument("name", nargs="?", default="")
        parser.add_argument("--mode", default="auto")
        parser.add_argument("--api-base-url", default="")
        parser.add_argument("--api-token", default="")
        parser.add_argument("--no-cache", action="store_true", default=False)
        parser.add_argument("--index-url", default="./index.json")
        parser.add_argument("--install-dir", default="./.owlhub/skills")
        parser.add_argument("--lock-file", default="./skill-lock.json")
        parser.add_argument("--verbose", "-v", action="store_true", default=False)
        parser.add_argument("--quiet", action="store_true", default=False)
        ns = parser.parse_args(sub_argv)
        update_command(
            name=ns.name,
            mode=ns.mode,
            api_base_url=ns.api_base_url,
            api_token=ns.api_token,
            no_cache=ns.no_cache,
            index_url=ns.index_url,
            install_dir=ns.install_dir,
            lock_file=ns.lock_file,
            verbose=ns.verbose,
            quiet=ns.quiet,
        )
        return True

    if sub == "publish":
        from owlclaw.cli.skill_hub import publish_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill publish")
        parser.add_argument("path", nargs="?", default=".")
        parser.add_argument("--mode", default="api")
        parser.add_argument("--api-base-url", default="")
        parser.add_argument("--api-token", default="")
        parser.add_argument("--no-cache", action="store_true", default=False)
        parser.add_argument("--index-url", default="./index.json")
        parser.add_argument("--install-dir", default="./.owlhub/skills")
        parser.add_argument("--lock-file", default="./skill-lock.json")
        parser.add_argument("--verbose", "-v", action="store_true", default=False)
        parser.add_argument("--quiet", action="store_true", default=False)
        ns = parser.parse_args(sub_argv)
        publish_command(
            path=ns.path,
            mode=ns.mode,
            api_base_url=ns.api_base_url,
            api_token=ns.api_token,
            no_cache=ns.no_cache,
            index_url=ns.index_url,
            install_dir=ns.install_dir,
            lock_file=ns.lock_file,
            verbose=ns.verbose,
            quiet=ns.quiet,
        )
        return True

    if sub == "cache-clear":
        from owlclaw.cli.skill_hub import cache_clear_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw skill cache-clear")
        parser.add_argument("--index-url", default="./index.json")
        parser.add_argument("--install-dir", default="./.owlhub/skills")
        parser.add_argument("--lock-file", default="./skill-lock.json")
        ns = parser.parse_args(sub_argv)
        cache_clear_command(index_url=ns.index_url, install_dir=ns.install_dir, lock_file=ns.lock_file)
        return True

    print(f"Error: unknown skill subcommand: {sub}", file=sys.stderr)
    raise SystemExit(2)


def _dispatch_console_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw console` via argparse."""
    if not argv or argv[0] != "console":
        return False
    if "--help" in argv or "-h" in argv:
        _print_help_and_exit(["console"])
    parser = argparse.ArgumentParser(add_help=False, prog="owlclaw console")
    parser.add_argument("--port", type=int, default=8000)
    ns = parser.parse_args(argv[1:])
    from owlclaw.cli.console import console_command

    console_command(port=ns.port)
    return True


def _dispatch_memory_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw memory ...` using argparse for Typer option-parse compatibility."""
    if not argv or argv[0] != "memory":
        return False

    if len(argv) < 2:
        _print_help_and_exit(["memory"])

    sub = argv[1]
    sub_argv = argv[2:]
    if "--help" in sub_argv or "-h" in sub_argv:
        _print_help_and_exit(["memory", sub])

    if sub == "list":
        from owlclaw.cli.memory import list_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw memory list")
        parser.add_argument("--agent", required=True)
        parser.add_argument("--tenant", default="default")
        parser.add_argument("--tags", default="")
        parser.add_argument("--page", type=int, default=1)
        parser.add_argument("--page-size", type=int, default=20)
        parser.add_argument("--include-archived", action="store_true", default=False)
        parser.add_argument("--backend", default="pgvector")
        ns = parser.parse_args(sub_argv)
        list_command(
            agent=ns.agent,
            tenant=ns.tenant,
            tags=ns.tags,
            page=ns.page,
            page_size=ns.page_size,
            include_archived=ns.include_archived,
            backend=ns.backend,
        )
        return True

    if sub == "prune":
        from owlclaw.cli.memory import prune_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw memory prune")
        parser.add_argument("--agent", required=True)
        parser.add_argument("--tenant", default="default")
        parser.add_argument("--before", default="")
        parser.add_argument("--tags", default="")
        parser.add_argument("--backend", default="pgvector")
        ns = parser.parse_args(sub_argv)
        prune_command(
            agent=ns.agent,
            tenant=ns.tenant,
            before=ns.before,
            tags=ns.tags,
            backend=ns.backend,
        )
        return True

    if sub == "reset":
        from owlclaw.cli.memory import reset_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw memory reset")
        parser.add_argument("--agent", required=True)
        parser.add_argument("--tenant", default="default")
        parser.add_argument("--confirm", action="store_true", default=False)
        parser.add_argument("--backend", default="pgvector")
        ns = parser.parse_args(sub_argv)
        reset_command(
            agent=ns.agent,
            tenant=ns.tenant,
            confirm=ns.confirm,
            backend=ns.backend,
        )
        return True

    if sub == "stats":
        from owlclaw.cli.memory import stats_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw memory stats")
        parser.add_argument("--agent", required=True)
        parser.add_argument("--tenant", default="default")
        parser.add_argument("--backend", default="pgvector")
        ns = parser.parse_args(sub_argv)
        stats_command(
            agent=ns.agent,
            tenant=ns.tenant,
            backend=ns.backend,
        )
        return True

    if sub == "migrate-backend":
        from owlclaw.cli.memory import migrate_backend_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw memory migrate-backend")
        parser.add_argument("--agent", required=True)
        parser.add_argument("--tenant", default="default")
        parser.add_argument("--source-backend", required=True)
        parser.add_argument("--target-backend", required=True)
        parser.add_argument("--batch-size", type=int, default=200)
        parser.add_argument("--include-archived", dest="include_archived", action="store_true", default=True)
        parser.add_argument("--exclude-archived", dest="include_archived", action="store_false")
        ns = parser.parse_args(sub_argv)
        migrate_backend_command(
            agent=ns.agent,
            tenant=ns.tenant,
            source_backend=ns.source_backend,
            target_backend=ns.target_backend,
            batch_size=ns.batch_size,
            include_archived=ns.include_archived,
        )
        return True

    print(f"Error: unknown memory subcommand: {sub}", file=sys.stderr)
    raise SystemExit(2)


def _dispatch_ledger_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw ledger ...` via argparse."""
    if not argv or argv[0] != "ledger":
        return False
    if len(argv) < 2:
        _print_help_and_exit(["ledger"])

    sub = argv[1]
    sub_argv = argv[2:]
    if "--help" in sub_argv or "-h" in sub_argv:
        _print_help_and_exit(["ledger", sub])

    if sub == "query":
        from owlclaw.cli.ledger import query_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw ledger query")
        parser.add_argument("--tenant", default="default")
        parser.add_argument("--agent-id", default="")
        parser.add_argument("--caller", default="")
        parser.add_argument("--caller-prefix", default="")
        parser.add_argument("--status", default="")
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--order", choices=["asc", "desc"], default="desc")
        parser.add_argument("--database-url", default="")
        ns = parser.parse_args(sub_argv)
        query_command(
            tenant=ns.tenant,
            agent_id=ns.agent_id,
            caller=ns.caller,
            caller_prefix=ns.caller_prefix,
            status=ns.status,
            limit=ns.limit,
            order_desc=(ns.order == "desc"),
            database_url=ns.database_url,
        )
        return True

    print(f"Error: unknown ledger subcommand: {sub}", file=sys.stderr)
    raise SystemExit(2)


def _dispatch_agent_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw agent ...` signal operations via argparse."""
    if not argv or argv[0] != "agent":
        return False
    if len(argv) < 2:
        _print_help_and_exit(["agent"])

    sub = argv[1]
    sub_argv = argv[2:]
    if "--help" in sub_argv or "-h" in sub_argv:
        _print_help_and_exit(["agent", sub])

    from owlclaw.cli.agent_signal import (
        instruct_command,
        pause_command,
        resume_command,
        status_command,
        trigger_command,
    )

    if sub == "pause":
        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw agent pause")
        parser.add_argument("--agent", "--agent-id", dest="agent", required=True)
        parser.add_argument("--tenant", default="default")
        parser.add_argument("--operator", default="cli")
        ns = parser.parse_args(sub_argv)
        print(pause_command(agent=ns.agent, tenant=ns.tenant, operator=ns.operator))
        return True

    if sub == "resume":
        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw agent resume")
        parser.add_argument("--agent", "--agent-id", dest="agent", required=True)
        parser.add_argument("--tenant", default="default")
        parser.add_argument("--operator", default="cli")
        ns = parser.parse_args(sub_argv)
        print(resume_command(agent=ns.agent, tenant=ns.tenant, operator=ns.operator))
        return True

    if sub == "trigger":
        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw agent trigger")
        parser.add_argument("--agent", "--agent-id", dest="agent", required=True)
        parser.add_argument("--tenant", default="default")
        parser.add_argument("--operator", default="cli")
        parser.add_argument("--focus", default="")
        parser.add_argument("--message", default="")
        ns = parser.parse_args(sub_argv)
        print(
            trigger_command(
                agent=ns.agent,
                tenant=ns.tenant,
                operator=ns.operator,
                message=ns.message,
                focus=ns.focus or None,
            )
        )
        return True

    if sub == "instruct":
        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw agent instruct")
        parser.add_argument("--agent", "--agent-id", dest="agent", required=True)
        parser.add_argument("--tenant", default="default")
        parser.add_argument("--operator", default="cli")
        parser.add_argument("--message", required=True)
        parser.add_argument("--ttl", type=int, default=3600)
        ns = parser.parse_args(sub_argv)
        print(
            instruct_command(
                agent=ns.agent,
                tenant=ns.tenant,
                operator=ns.operator,
                message=ns.message,
                ttl_seconds=ns.ttl,
            )
        )
        return True

    if sub == "status":
        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw agent status")
        parser.add_argument("--agent", "--agent-id", dest="agent", required=True)
        parser.add_argument("--tenant", default="default")
        ns = parser.parse_args(sub_argv)
        print(status_command(agent=ns.agent, tenant=ns.tenant))
        return True

    print(f"Error: unknown agent subcommand: {sub}", file=sys.stderr)
    raise SystemExit(2)


def _dispatch_migration_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw migration ...` via argparse."""
    if not argv or argv[0] != "migration":
        return False
    if len(argv) < 2:
        _print_help_and_exit(["migration"])

    sub = argv[1]
    sub_argv = argv[2:]
    if "--help" in sub_argv or "-h" in sub_argv:
        _print_help_and_exit(["migration", sub])

    from owlclaw.cli.migration import set_command, status_command, suggest_command

    if sub == "status":
        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw migration status")
        parser.add_argument("--config", default="")
        ns = parser.parse_args(sub_argv)
        status_command(config=ns.config)
        return True

    if sub == "set":
        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw migration set")
        parser.add_argument("skill")
        parser.add_argument("weight", type=int)
        parser.add_argument("--config", default="")
        ns = parser.parse_args(sub_argv)
        set_command(skill=ns.skill, weight=ns.weight, config=ns.config)
        return True

    if sub == "suggest":
        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw migration suggest")
        parser.add_argument("--config", default="")
        ns = parser.parse_args(sub_argv)
        suggest_command(config=ns.config)
        return True

    print(f"Error: unknown migration subcommand: {sub}", file=sys.stderr)
    raise SystemExit(2)


def _dispatch_approval_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw approval ...` via argparse."""
    if not argv or argv[0] != "approval":
        return False
    if len(argv) < 2:
        _print_help_and_exit(["approval"])

    sub = argv[1]
    sub_argv = argv[2:]
    if "--help" in sub_argv or "-h" in sub_argv:
        _print_help_and_exit(["approval", sub])

    from owlclaw.cli.migration import approval_approve_command, approval_list_command

    if sub == "list":
        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw approval list")
        parser.add_argument("--status", default="")
        parser.add_argument("--store", default="")
        ns = parser.parse_args(sub_argv)
        approval_list_command(status=ns.status, store=ns.store)
        return True

    if sub == "approve":
        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw approval approve")
        parser.add_argument("request_id")
        parser.add_argument("--approver", default="cli")
        parser.add_argument("--store", default="")
        ns = parser.parse_args(sub_argv)
        approval_approve_command(request_id=ns.request_id, approver=ns.approver, store=ns.store)
        return True

    print(f"Error: unknown approval subcommand: {sub}", file=sys.stderr)
    raise SystemExit(2)


def _dispatch_trigger_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw trigger ...` using argparse for command-specific templates."""
    if not argv or argv[0] != "trigger":
        return False
    if len(argv) < 2:
        _print_help_and_exit(["trigger"])

    sub = argv[1]
    sub_argv = argv[2:]
    if "--help" in sub_argv or "-h" in sub_argv:
        if sub == "template" and sub_argv and sub_argv[0] == "db-change":
            _print_help_and_exit(["trigger", "template", "db-change"])
        _print_help_and_exit(["trigger", sub])

    if sub != "template":
        print(f"Error: unknown trigger subcommand: {sub}", file=sys.stderr)
        raise SystemExit(2)

    if len(sub_argv) < 1:
        _print_help_and_exit(["trigger", "template"])

    template_name = sub_argv[0]
    if template_name != "db-change":
        print(f"Error: unknown trigger template: {template_name}", file=sys.stderr)
        raise SystemExit(2)

    from owlclaw.cli.trigger_template import db_change_template_command

    parser = argparse.ArgumentParser(add_help=False, prog="owlclaw trigger template db-change")
    parser.add_argument("--output", "--path", "-o", "-p", dest="output_dir", default=".")
    parser.add_argument("--channel", default="position_changes")
    parser.add_argument("--table", dest="table_name", default="positions")
    parser.add_argument("--trigger-name", dest="trigger_name", default="position_changes_trigger")
    parser.add_argument("--function-name", dest="function_name", default="notify_position_changes")
    parser.add_argument("--force", "-f", action="store_true", default=False)
    ns = parser.parse_args(sub_argv[1:])
    target = db_change_template_command(
        output_dir=ns.output_dir,
        channel=ns.channel,
        table_name=ns.table_name,
        trigger_name=ns.trigger_name,
        function_name=ns.function_name,
        force=ns.force,
    )
    print(f"Generated: {target}")
    return True


def _dispatch_scan_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw scan ...` via argparse."""
    if not argv or argv[0] != "scan":
        return False

    if len(argv) >= 3 and argv[1] == "config" and argv[2] == "validate":
        from owlclaw.cli.scan_cli import validate_scan_config_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw scan config validate")
        parser.add_argument("--path", default=".")
        parser.add_argument("--config", default="")
        ns = parser.parse_args(argv[3:])
        validate_scan_config_command(path=ns.path, config=ns.config)
        return True

    from owlclaw.cli.scan_cli import run_scan_command

    parser = argparse.ArgumentParser(add_help=False, prog="owlclaw scan")
    parser.add_argument("--path", default=".")
    parser.add_argument("--format", dest="format_name", choices=["json", "yaml"], default="json")
    parser.add_argument("--output", default="")
    parser.add_argument("--incremental", action="store_true", default=False)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--config", default="")
    parser.add_argument("--verbose", "-v", action="store_true", default=False)
    ns = parser.parse_args(argv[1:])
    run_scan_command(
        path=ns.path,
        format_name=ns.format_name,
        output=ns.output,
        incremental=ns.incremental,
        workers=ns.workers,
        config=ns.config,
        verbose=ns.verbose,
    )
    return True


def _dispatch_migrate_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw migrate ...` via argparse."""
    if not argv or argv[0] != "migrate":
        return False
    if len(argv) < 2:
        _print_help_and_exit(["migrate"])

    sub = argv[1]
    if sub == "config":
        if len(argv) < 3:
            _print_help_and_exit(["migrate", "config"])
        action = argv[2]
        if action != "validate":
            print(f"Error: unknown migrate config subcommand: {action}", file=sys.stderr)
            raise SystemExit(2)
        from owlclaw.cli.migrate.config_cli import validate_migrate_config_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw migrate config validate")
        parser.add_argument("--config", default=".owlclaw-migrate.yaml")
        ns = parser.parse_args(argv[3:])
        validate_migrate_config_command(config=ns.config)
        return True

    if sub == "init":
        from owlclaw.cli.migrate.config_cli import init_migrate_config_command

        parser = argparse.ArgumentParser(add_help=False, prog="owlclaw migrate init")
        parser.add_argument("--path", default=".")
        parser.add_argument("--project", default="")
        parser.add_argument("--output", default="")
        parser.add_argument("--output-mode", choices=["handler", "binding", "both", "mcp"], default="handler")
        parser.add_argument("--force", action="store_true", default=False)
        parser.add_argument("--non-interactive", action="store_true", default=False)
        ns = parser.parse_args(argv[2:])
        init_migrate_config_command(
            path=ns.path,
            project=ns.project,
            output=ns.output,
            output_mode=ns.output_mode,
            force=ns.force,
            interactive=not ns.non_interactive,
        )
        return True

    if sub != "scan":
        print(f"Error: unknown migrate subcommand: {sub}", file=sys.stderr)
        raise SystemExit(2)

    from owlclaw.cli.migrate.scan_cli import run_migrate_scan_command

    parser = argparse.ArgumentParser(add_help=False, prog="owlclaw migrate scan")
    parser.add_argument("--project", default="")
    parser.add_argument("--openapi", default="")
    parser.add_argument("--orm", default="")
    parser.add_argument("--output-mode", choices=["handler", "binding", "both", "mcp"], default="handler")
    parser.add_argument("--output", "--path", "-o", "-p", dest="output", default=".")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--report-json", default="")
    parser.add_argument("--report-md", default="")
    parser.add_argument("--force", action="store_true", default=False)
    ns = parser.parse_args(argv[2:])
    run_migrate_scan_command(
        project=ns.project,
        openapi=ns.openapi,
        orm=ns.orm,
        output_mode=ns.output_mode,
        output=ns.output,
        dry_run=ns.dry_run,
        report_json=ns.report_json,
        report_md=ns.report_md,
        force=ns.force,
    )
    return True


def _dispatch_release_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw release ...` via argparse."""
    if not argv or argv[0] != "release":
        return False
    if len(argv) < 2:
        _print_help_and_exit(["release"])

    sub = argv[1]
    if sub != "gate":
        print(f"Error: unknown release subcommand: {sub}", file=sys.stderr)
        raise SystemExit(2)

    if len(argv) < 3:
        _print_help_and_exit(["release", "gate"])
    target = argv[2]
    if "--help" in argv or "-h" in argv:
        _print_help_and_exit(["release", "gate", target])
    if target != "owlhub":
        print(f"Error: unknown release gate target: {target}", file=sys.stderr)
        raise SystemExit(2)

    from owlclaw.cli.release_gate import release_gate_owlhub_command

    parser = argparse.ArgumentParser(add_help=False, prog="owlclaw release gate owlhub")
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--index-url", required=True)
    parser.add_argument("--query", default="skill")
    parser.add_argument("--work-dir", default=".owlhub/release-gate")
    parser.add_argument("--output", default="")
    ns = parser.parse_args(argv[3:])
    release_gate_owlhub_command(
        api_base_url=ns.api_base_url,
        index_url=ns.index_url,
        query=ns.query,
        work_dir=ns.work_dir,
        output=ns.output,
    )
    return True


def _dispatch_start_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw start` via argparse."""
    if not argv or argv[0] != "start":
        return False
    if "--help" in argv or "-h" in argv:
        _print_help_and_exit(["start"])
    from owlclaw.cli.start import start_command

    parser = argparse.ArgumentParser(add_help=False, prog="owlclaw start")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    ns = parser.parse_args(argv[1:])
    start_command(host=ns.host, port=ns.port)
    return True


def _dispatch_console_command(argv: list[str]) -> bool:
    """Dispatch `owlclaw console` via argparse."""
    if not argv or argv[0] != "console":
        return False
    if "--help" in argv or "-h" in argv:
        _print_help_and_exit(["console"])
    from owlclaw.cli.console import console_command

    parser = argparse.ArgumentParser(add_help=False, prog="owlclaw console")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-open", action="store_true", default=False)
    ns = parser.parse_args(argv[1:])
    console_command(port=ns.port, open_browser=not ns.no_open)
    return True


def _print_help_and_exit(argv: list[str]) -> None:
    """Print plain help when Typer/Rich make_metavar bug triggers (--help)."""
    argv = [a for a in argv if a not in ("--help", "-h")]
    if not argv:
        print("Usage: owlclaw [OPTIONS] COMMAND [ARGS]...")
        print("\n  OwlClaw — Agent base for business applications.\n")
        print("Options:")
        print("  --version, -V  Show installed version and exit")
        print("Commands:")
        print("  db     Database: init, migrate, status")
        print("  memory Agent memory: list, prune, reset, stats")
        print("  ledger Query governance audit ledger")
        print("  agent  Manual control via signal (pause/resume/trigger/instruct/status)")
        print("  skill  Create, validate, list Agent Skills (SKILL.md)")
        print("  console Open Console URL in browser")
        print("  trigger Trigger templates (db-change)")
        print("  migrate Migrate legacy APIs/models to OwlClaw assets")
        print("  release Release validation and gate checks")
        print("  start   Start local console host")
        print("  console Open console in browser")
        print("\n  owlclaw db --help   owlclaw skill --help")
        sys.exit(0)
    if argv == ["start"]:
        print("Usage: owlclaw start [OPTIONS]")
        print("\n  Start local OwlClaw host with optional console mount.")
        print("Options:")
        print("  --host TEXT     Bind host (default: 127.0.0.1)")
        print("  --port INTEGER  Bind port (default: 8000)")
        print("  --help          Show this message and exit")
        sys.exit(0)
    if argv == ["console"]:
        print("Usage: owlclaw console [OPTIONS]")
        print("\n  Open OwlClaw Console URL in browser.")
        print("Options:")
        print("  --port INTEGER  Console port (default: 8000)")
        print("  --no-open       Do not open browser")
        print("  --help          Show this message and exit")
        sys.exit(0)
    if argv == ["db"]:
        print("Usage: owlclaw db [OPTIONS] COMMAND [ARGS]...")
        print("\n  Database operations: init, migrate, status, revision, rollback, backup.\n")
        print("Commands:")
        print("  init     Create owlclaw (and optionally hatchet) database, role, pgvector")
        print("  migrate  Run Alembic migrations (owlclaw schema)")
        print("  status   Show connection and migration status")
        print("  revision Create new migration script (--empty or autogenerate)")
        print("  rollback Roll back migrations (--target, --steps, or one step)")
        print("  backup   Create database backup (pg_dump)")
        print("  restore  Restore database from backup (psql/pg_restore)")
        print("  check    Run database health checks")
        print("\n  owlclaw db init --help | owlclaw db backup --help | owlclaw db check --help")
        sys.exit(0)
    if argv == ["db", "revision"]:
        print("Usage: owlclaw db revision [OPTIONS]")
        print("\n  Create a new migration script (autogenerate or empty template).\n")
        print("Options:")
        print("  -m, --message TEXT    Revision message (required for autogenerate)")
        print("  --empty               Create empty migration template")
        print("  --database-url TEXT   Database URL (default: OWLCLAW_DATABASE_URL)")
        print("  --help                Show this message and exit")
        sys.exit(0)
    if argv == ["db", "rollback"]:
        print("Usage: owlclaw db rollback [OPTIONS]")
        print("\n  Roll back database migrations (Alembic downgrade).\n")
        print("Options:")
        print("  -t, --target TEXT     Revision to downgrade to (e.g. base or revision id)")
        print("  -s, --steps INTEGER  Number of revisions to downgrade (default: 1 if omitted)")
        print("  --database-url TEXT  Database URL (default: OWLCLAW_DATABASE_URL)")
        print("  --dry-run             Show what would be rolled back without executing")
        print("  -y, --yes             Skip confirmation prompt")
        print("  --help                Show this message and exit")
        sys.exit(0)
    if argv == ["db", "backup"]:
        print("Usage: owlclaw db backup [OPTIONS]")
        print("\n  Create database backup using pg_dump.\n")
        print("Options:")
        print("  -o, --output PATH     Output file path (required)")
        print("  -F, --format [plain|custom]  Backup format (default: plain)")
        print("  --schema-only         Dump schema only")
        print("  --data-only           Dump data only")
        print("  --database-url TEXT  Database URL (default: OWLCLAW_DATABASE_URL)")
        print("  --help                Show this message and exit")
        sys.exit(0)
    if argv == ["db", "restore"]:
        print("Usage: owlclaw db restore [OPTIONS]")
        print("\n  Restore database from backup file (SQL or pg_dump custom).\n")
        print("Options:")
        print("  -i, --input PATH      Input backup file (required)")
        print("  --clean               Drop existing objects before restore (pg_restore only)")
        print("  --database-url TEXT  Database URL (default: OWLCLAW_DATABASE_URL)")
        print("  -y, --yes             Skip confirmation prompt")
        print("  --help                Show this message and exit")
        sys.exit(0)
    if argv == ["db", "init"]:
        print("Usage: owlclaw db init [OPTIONS]")
        print("\n  Create owlclaw (and optionally hatchet) database, role, and pgvector.\n")
        print("Options:")
        print("  --admin-url TEXT       PostgreSQL superuser URL (default: OWLCLAW_ADMIN_URL)")
        print("  --owlclaw-password     Password for role owlclaw (default: random)")
        print("  --hatchet-password     Password for role hatchet (default: random)")
        print("  --skip-hatchet         Do not create hatchet database/role")
        print("  --dry-run              Show what would be done without executing")
        print("  --help                 Show this message and exit")
        sys.exit(0)
    if argv == ["db", "migrate"]:
        print("Usage: owlclaw db migrate [OPTIONS] [TARGET]")
        print("\n  Run Alembic migrations. OWLCLAW_DATABASE_URL required.\n")
        print("  --dry-run  Show pending migrations only")
        sys.exit(0)
    if argv == ["db", "status"]:
        print("Usage: owlclaw db status")
        print("\n  Show database connection and migration status.")
        sys.exit(0)
    if argv == ["db", "check"]:
        print("Usage: owlclaw db check [OPTIONS]")
        print("\n  Run database health checks (connection, migration, pgvector, pool, disk, slow queries).\n")
        print("Options:")
        print("  --database-url TEXT  Database URL (default: OWLCLAW_DATABASE_URL)")
        print("  -v, --verbose        Show detailed progress")
        print("  --help               Show this message and exit")
        sys.exit(0)
    if argv == ["skill"]:
        print("Usage: owlclaw skill [OPTIONS] COMMAND [ARGS]...")
        print("\n  Create, parse, validate, and list Agent Skills (SKILL.md). Local only.\n")
        print("Commands:")
        print("  init      Scaffold a new SKILL.md from template")
        print("  create    Conversationally create SKILL.md")
        print("  list-templates  List local AI-assist templates")
        print("  parse     Parse SKILL.md and show resolved metadata")
        print("  quality   Show quality score/trend/suggestions")
        print("  validate  Validate SKILL.md in current dir")
        print("  list      List skills in a directory")
        print("  templates List templates from the template library")
        print("  search    Search skills from OwlHub")
        print("  install   Install a skill from OwlHub")
        print("  installed List installed OwlHub skills")
        print("  update    Update installed OwlHub skills")
        print("  publish   Publish a local skill to OwlHub API")
        print("  cache-clear  Clear local OwlHub cache")
        print("\nExamples:")
        print("  owlclaw skill search --query monitor inventory alerts --tags trading --industry retail --sort-by quality_score")
        print("  owlclaw skill install entry-monitor --verbose")
        print("  owlclaw skill install --package ./package.yaml")
        print("  owlclaw skill publish ./my-skill --api-base-url http://localhost:8000 --api-token <token>")
        print("\n  owlclaw skill init --help | owlclaw skill search --help | owlclaw skill publish --help")
        sys.exit(0)
    if argv == ["skill", "search"]:
        print("Usage: owlclaw skill search [OPTIONS]")
        print("\n  Search skills in OwlHub index/API.")
        print("Options:")
        print("  -q, --query TEXT      Search query")
        print("  --tags TEXT           Comma-separated tags")
        print("  --industry TEXT       Industry filter")
        print("  --sort-by TEXT        name|quality_score")
        print("  --tag-mode TEXT       and|or")
        print("  --include-draft       Include draft skills")
        print("  -v, --verbose         Show detailed progress")
        print("  --quiet               Suppress non-error output")
        print("  --help                Show this message and exit")
        sys.exit(0)
    if argv == ["skill", "install"]:
        print("Usage: owlclaw skill install [name] [OPTIONS]")
        print("\n  Install one skill or a package.yaml-defined bundle from OwlHub.")
        print("Options:")
        print("  --version TEXT        Exact version")
        print("  --package TEXT        Install from package.yaml")
        print("  --no-deps             Skip dependency install")
        print("  --force               Ignore moderation/checksum constraints")
        print("  -v, --verbose         Show detailed progress")
        print("  --quiet               Suppress non-error output")
        print("  --help                Show this message and exit")
        sys.exit(0)
    if argv == ["skill", "publish"]:
        print("Usage: owlclaw skill publish [path] [OPTIONS]")
        print("\n  Publish one local skill package to OwlHub API.")
        print("Options:")
        print("  --api-base-url TEXT   OwlHub API base URL")
        print("  --api-token TEXT      OwlHub API token")
        print("  -v, --verbose         Show detailed progress")
        print("  --quiet               Suppress non-error output")
        print("  --help                Show this message and exit")
        sys.exit(0)
    if argv == ["init"]:
        print("Usage: owlclaw init [OPTIONS]")
        print("\n  Generate default owlclaw.yaml in target directory.")
        print("Options:")
        print("  --path TEXT   Output directory (default: current directory)")
        print("  --force       Overwrite existing owlclaw.yaml")
        print("  --help        Show this message and exit")
        sys.exit(0)
    if argv == ["reload"]:
        print("Usage: owlclaw reload [OPTIONS]")
        print("\n  Reload configuration and display applied/skipped changes.")
        print("Options:")
        print("  --config TEXT  Optional config file path")
        print("  --help         Show this message and exit")
        sys.exit(0)
    if argv == ["console"]:
        print("Usage: owlclaw console [OPTIONS]")
        print("\n  Open OwlClaw Console in browser.")
        print("Options:")
        print("  --port INTEGER  Console host port (default: 8000)")
        print("  --help          Show this message and exit")
        sys.exit(0)
    if argv == ["memory"]:
        print("Usage: owlclaw memory [OPTIONS] COMMAND [ARGS]...")
        print("\n  Agent memory operations (list, prune, reset, stats).\n")
        print("Commands:")
        print("  list    List memory entries with pagination and tag filter")
        print("  prune   Delete memory entries by time/tag filter")
        print("  reset   Delete all memory entries for an agent")
        print("  stats   Show memory statistics")
        print("  migrate-backend  Migrate memory data between backends")
        print("\n  owlclaw memory list --help | owlclaw memory prune --help")
        sys.exit(0)
    if argv == ["memory", "list"]:
        print("Usage: owlclaw memory list --agent <name> [OPTIONS]")
        print("\n  List memory entries with pagination and tag filter.")
        sys.exit(0)
    if argv == ["memory", "prune"]:
        print("Usage: owlclaw memory prune --agent <name> [OPTIONS]")
        print("\n  Delete memory entries by time/tag filter.")
        sys.exit(0)
    if argv == ["memory", "reset"]:
        print("Usage: owlclaw memory reset --agent <name> --confirm [OPTIONS]")
        print("\n  Delete all memory entries for an agent.")
        sys.exit(0)
    if argv == ["memory", "stats"]:
        print("Usage: owlclaw memory stats --agent <name> [OPTIONS]")
        print("\n  Show memory statistics.")
        sys.exit(0)
    if argv == ["memory", "migrate-backend"]:
        print("Usage: owlclaw memory migrate-backend --agent <name> --source-backend <x> --target-backend <y> [OPTIONS]")
        print("\n  Migrate memory entries between storage backends.")
        sys.exit(0)
    if argv == ["ledger"]:
        print("Usage: owlclaw ledger [OPTIONS] COMMAND [ARGS]...")
        print("\n  Governance ledger query operations.")
        print("Commands:")
        print("  query   Query ledger records with tenant/agent/caller filters")
        print("\n  owlclaw ledger query --help")
        sys.exit(0)
    if argv == ["ledger", "query"]:
        print("Usage: owlclaw ledger query [OPTIONS]")
        print("\n  Query governance ledger records.")
        print("Options:")
        print("  --tenant TEXT         Tenant id (default: default)")
        print("  --agent-id TEXT       Agent id filter")
        print("  --caller TEXT         Exact caller filter")
        print("  --caller-prefix TEXT  Caller prefix filter (e.g. mionyee.)")
        print("  --status TEXT         status filter")
        print("  --limit INTEGER       Max records (default: 20)")
        print("  --order [asc|desc]    Sort by created_at (default: desc)")
        print("  --database-url TEXT   Optional database URL override")
        print("  --help                Show this message and exit")
        sys.exit(0)
    if argv == ["agent"]:
        print("Usage: owlclaw agent [OPTIONS] COMMAND [ARGS]...")
        print("\n  Manual signal operations for an agent.")
        print("Commands:")
        print("  pause    Pause autonomous scheduling")
        print("  resume   Resume autonomous scheduling")
        print("  trigger  Trigger one manual run")
        print("  instruct Inject operator instruction")
        print("  status   Show pause/instruction status")
        print("\n  owlclaw agent pause --help | owlclaw agent status --help")
        sys.exit(0)
    if argv == ["agent", "pause"]:
        print("Usage: owlclaw agent pause --agent-id <id> [OPTIONS]")
        print("\n  Pause an agent via signal router.")
        sys.exit(0)
    if argv == ["agent", "resume"]:
        print("Usage: owlclaw agent resume --agent-id <id> [OPTIONS]")
        print("\n  Resume an agent via signal router.")
        sys.exit(0)
    if argv == ["agent", "trigger"]:
        print("Usage: owlclaw agent trigger --agent-id <id> [OPTIONS]")
        print("\n  Force trigger one run via signal router.")
        sys.exit(0)
    if argv == ["agent", "instruct"]:
        print("Usage: owlclaw agent instruct --agent-id <id> --message <text> [OPTIONS]")
        print("\n  Queue one operator instruction with TTL.")
        sys.exit(0)
    if argv == ["agent", "status"]:
        print("Usage: owlclaw agent status --agent-id <id> [OPTIONS]")
        print("\n  Show agent paused state and pending instructions.")
        sys.exit(0)
    if argv == ["migration"]:
        print("Usage: owlclaw migration [OPTIONS] COMMAND [ARGS]...")
        print("\n  Progressive migration controls.")
        print("Commands:")
        print("  status   Show migration_weight by skill")
        print("  set      Set migration_weight for one skill")
        print("  suggest  Recommend next migration_weight step")
        print("\n  owlclaw migration status --help")
        sys.exit(0)
    if argv == ["migration", "status"]:
        print("Usage: owlclaw migration status [OPTIONS]")
        print("\n  Show migration_weight by skill from owlclaw.yaml.")
        print("Options:")
        print("  --config TEXT  Optional config file path")
        print("  --help         Show this message and exit")
        sys.exit(0)
    if argv == ["migration", "set"]:
        print("Usage: owlclaw migration set SKILL WEIGHT [OPTIONS]")
        print("\n  Set migration_weight (0-100) for one skill.")
        print("Options:")
        print("  --config TEXT  Optional config file path")
        print("  --help         Show this message and exit")
        sys.exit(0)
    if argv == ["migration", "suggest"]:
        print("Usage: owlclaw migration suggest [OPTIONS]")
        print("\n  Suggest next migration_weight step by skill.")
        print("Options:")
        print("  --config TEXT  Optional config file path")
        print("  --help         Show this message and exit")
        sys.exit(0)
    if argv == ["approval"]:
        print("Usage: owlclaw approval [OPTIONS] COMMAND [ARGS]...")
        print("\n  Approval queue operations.")
        print("Commands:")
        print("  list     List approval requests")
        print("  approve  Approve one request by id")
        print("\n  owlclaw approval list --help")
        sys.exit(0)
    if argv == ["approval", "list"]:
        print("Usage: owlclaw approval list [OPTIONS]")
        print("\n  List approval requests from local store.")
        print("Options:")
        print("  --status TEXT  Optional status filter")
        print("  --store TEXT   Optional approval store path")
        print("  --help         Show this message and exit")
        sys.exit(0)
    if argv == ["approval", "approve"]:
        print("Usage: owlclaw approval approve REQUEST_ID [OPTIONS]")
        print("\n  Approve one request in local store.")
        print("Options:")
        print("  --approver TEXT  Approver identity (default: cli)")
        print("  --store TEXT     Optional approval store path")
        print("  --help           Show this message and exit")
        sys.exit(0)
    if argv == ["trigger"]:
        print("Usage: owlclaw trigger [OPTIONS] COMMAND [ARGS]...")
        print("\n  Trigger tooling commands.")
        print("Commands:")
        print("  template  Generate trigger templates")
        print("\n  owlclaw trigger template --help")
        sys.exit(0)
    if argv == ["migrate"]:
        print("Usage: owlclaw migrate [OPTIONS] COMMAND [ARGS]...")
        print("\n  Migration tooling commands.")
        print("Commands:")
        print("  scan   Scan project/OpenAPI/ORM sources and generate handlers or binding skills")
        print("  init   Interactive migrate config wizard")
        print("  config Validate migrate config")
        print("\n  owlclaw migrate scan --help")
        sys.exit(0)
    if argv == ["migrate", "config"]:
        print("Usage: owlclaw migrate config [OPTIONS] COMMAND [ARGS]...")
        print("\n  Migrate config commands.")
        print("Commands:")
        print("  validate  Validate .owlclaw-migrate.yaml")
        print("\n  owlclaw migrate config validate --help")
        sys.exit(0)
    if argv == ["migrate", "config", "validate"]:
        print("Usage: owlclaw migrate config validate [OPTIONS]")
        print("\n  Validate migrate config file.")
        print("Options:")
        print("  --config TEXT  Path to config file (default: .owlclaw-migrate.yaml)")
        print("  --help         Show this message and exit")
        sys.exit(0)
    if argv == ["migrate", "init"]:
        print("Usage: owlclaw migrate init [OPTIONS]")
        print("\n  Create .owlclaw-migrate.yaml (interactive by default).")
        print("Options:")
        print("  --path TEXT                     Directory for config file (default: .)")
        print("  --project TEXT                  Default project path")
        print("  --output TEXT                   Default output directory")
        print("  --output-mode [handler|binding|both|mcp]")
        print("                                  Default output mode")
        print("  --force                         Overwrite existing config")
        print("  --non-interactive               Use provided options only")
        print("  --help                          Show this message and exit")
        sys.exit(0)
    if argv == ["release"]:
        print("Usage: owlclaw release [OPTIONS] COMMAND [ARGS]...")
        print("\n  Release validation and gate tooling.")
        print("Commands:")
        print("  gate  Run release gate checks")
        print("\n  owlclaw release gate --help")
        sys.exit(0)
    if argv == ["release", "gate"]:
        print("Usage: owlclaw release gate [OPTIONS] TARGET")
        print("\n  Run release gate checks for one target.")
        print("Targets:")
        print("  owlhub  OwlHub API/index/CLI smoke gate")
        print("\n  owlclaw release gate owlhub --help")
        sys.exit(0)
    if argv == ["release", "gate", "owlhub"]:
        print("Usage: owlclaw release gate owlhub [OPTIONS]")
        print("\n  Run OwlHub release gate checks.")
        print("Options:")
        print("  --api-base-url TEXT  OwlHub API base URL (required)")
        print("  --index-url TEXT     Public index URL (required)")
        print("  --query TEXT         Query used for CLI search smoke check (default: skill)")
        print("  --work-dir TEXT      Local temp dir for gate run (default: .owlhub/release-gate)")
        print("  --output TEXT        Optional output JSON file path")
        print("  --help               Show this message and exit")
        sys.exit(0)
    if argv == ["migrate", "scan"]:
        print("Usage: owlclaw migrate scan [OPTIONS]")
        print("\n  Scan OpenAPI/ORM inputs and generate handler stubs and/or binding SKILL.md.")
        print("Options:")
        print("  --project TEXT                 Python project path for handler migration scan")
        print("  --openapi TEXT                 OpenAPI spec path (.yaml/.yml/.json)")
        print("  --orm TEXT                     ORM operations descriptor path (.yaml/.yml/.json)")
        print("  --output-mode [handler|binding|both|mcp]")
        print("                                 Output type (default: handler)")
        print("  --output, --path TEXT          Output directory (default: .)")
        print("  --dry-run                      Preview generated file paths without writing")
        print("  --report-json TEXT             Report JSON path (default: <output>/migration_report.json)")
        print("  --report-md TEXT               Report Markdown path (default: <output>/migration_report.md)")
        print("  --force                        Overwrite existing target files")
        print("  --help                         Show this message and exit")
        sys.exit(0)
    if argv == ["trigger", "template"]:
        print("Usage: owlclaw trigger template [OPTIONS] TEMPLATE")
        print("\n  Generate trigger templates.")
        print("Templates:")
        print("  db-change  PostgreSQL NOTIFY trigger SQL")
        print("\n  owlclaw trigger template db-change --help")
        sys.exit(0)
    if argv == ["trigger", "db-change"] or argv == ["trigger", "template", "db-change"]:
        print("Usage: owlclaw trigger template db-change [OPTIONS]")
        print("\n  Generate PostgreSQL NOTIFY trigger SQL template.")
        print("Options:")
        print("  --output, --path TEXT      Output directory (default: .)")
        print("  --channel TEXT             NOTIFY channel (default: position_changes)")
        print("  --table TEXT               Source table (default: positions)")
        print("  --trigger-name TEXT        Trigger name (default: position_changes_trigger)")
        print("  --function-name TEXT       Function name (default: notify_position_changes)")
        print("  --force                    Overwrite existing target file")
        print("  --help                     Show this message and exit")
        sys.exit(0)
    if argv == ["skill", "init"]:
        print("Usage: owlclaw skill init [OPTIONS]")
        print("\n  Scaffold a new SKILL.md in current directory.")
        print("  --from-binding TEXT  Generate a business-rules template from existing binding SKILL.md")
        sys.exit(0)
    if argv == ["skill", "create"]:
        print("Usage: owlclaw skill create [OPTIONS]")
        print("\n  Conversationally create SKILL.md via interactive prompts.")
        print("  --interactive                Enable interactive mode")
        print("  --from-template TEXT         Generate from local template")
        print("  --from-doc TEXT              Generate from business doc (markdown/text)")
        print("  --output TEXT                Output directory (default: skills)")
        print("  --capabilities-path TEXT     Path to discover existing capabilities")
        sys.exit(0)
    if argv == ["skill", "validate"]:
        print("Usage: owlclaw skill validate [OPTIONS] [DIR]")
        print("\n  Validate SKILL.md (default: current dir).")
        sys.exit(0)
    if argv == ["skill", "parse"]:
        print("Usage: owlclaw skill parse [PATH] [--cache]")
        print("\n  Parse SKILL.md and output resolved parse metadata as JSON.")
        sys.exit(0)
    if argv == ["skill", "quality"]:
        print("Usage: owlclaw skill quality [SKILL_NAME] [OPTIONS]")
        print("\n  Show skill quality score, trend and suggestions.")
        print("  --all      Show latest quality for all skills")
        print("  --trend    Show trend points in selected period")
        print("  --period   Period selector (e.g. 7d, 30d, 12w)")
        print("  --suggest  Show improvement suggestions")
        sys.exit(0)
    if argv == ["skill", "list-templates"]:
        print("Usage: owlclaw skill list-templates")
        print("\n  List local AI-assist templates from ~/.owlclaw/templates.")
        sys.exit(0)
    if argv == ["skill", "list"]:
        print("Usage: owlclaw skill list [OPTIONS]")
        print("\n  List Agent Skills in directory.")
        sys.exit(0)
    if argv == ["skill", "templates"]:
        print("Usage: owlclaw skill templates [OPTIONS]")
        print("\n  List templates from the template library.")
        sys.exit(0)
    # Fallback
    print("Usage: owlclaw [OPTIONS] COMMAND [ARGS]...")
    print("Options:")
    print("  --version, -V  Show installed version and exit")
    print("  db     Database: init, migrate, status, revision, rollback")
    print("  memory Agent memory: list, prune, reset, stats, migrate-backend")
    print("  ledger Query governance audit ledger")
    print("  agent  Manual control via signal (pause, resume, trigger, instruct, status)")
    print("  migration Progressive migration controls")
    print("  approval  Approval queue operations")
    print("  skill  Create, parse, validate, list Agent Skills (SKILL.md)")
    print("  console Open Console URL in browser")
    print("  trigger Trigger templates (db-change)")
    print("  migrate Migrate legacy APIs/models to OwlClaw assets")
    print("  release Release validation and gate checks")
    print("  start   Start local console host")
    print("  console Open console in browser")
    sys.exit(0)


def _dispatch_db_revision(argv: list[str]) -> bool:
    """Dispatch `owlclaw db revision` via argparse (avoids Typer Option secondary-flag issue)."""
    if len(argv) < 2 or argv[0] != "db" or argv[1] != "revision":
        return False
    if "--help" in argv or "-h" in argv:
        _print_help_and_exit(["db", "revision"])
    import argparse

    from owlclaw.cli.db_revision import revision_command
    parser = argparse.ArgumentParser(prog="owlclaw db revision")
    parser.add_argument("-m", "--message", default="", help="Revision message")
    parser.add_argument("--empty", action="store_true", help="Empty migration template")
    parser.add_argument("--database-url", dest="database_url", default="", help="Database URL")
    ns = parser.parse_args(argv[2:])
    revision_command(message=ns.message, empty_template=ns.empty, database_url=ns.database_url or "")
    return True


def _dispatch_db_rollback(argv: list[str]) -> bool:
    """Dispatch `owlclaw db rollback` via argparse (Typer optional-option compatibility)."""
    if len(argv) < 2 or argv[0] != "db" or argv[1] != "rollback":
        return False
    if "--help" in argv or "-h" in argv:
        _print_help_and_exit(["db", "rollback"])
    parser = argparse.ArgumentParser(prog="owlclaw db rollback")
    parser.add_argument("-t", "--target", default="", help="Revision to downgrade to")
    parser.add_argument("-s", "--steps", type=int, default=0, help="Number of revisions to downgrade")
    parser.add_argument("--database-url", dest="database_url", default="", help="Database URL")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be rolled back")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    ns = parser.parse_args(argv[2:])
    from owlclaw.cli.db_rollback import rollback_command
    rollback_command(
        target=ns.target or "",
        steps=ns.steps or 0,
        database_url=ns.database_url or "",
        dry_run=ns.dry_run,
        yes=ns.yes,
    )
    return True


def _dispatch_db_backup(argv: list[str]) -> bool:
    """Dispatch `owlclaw db backup` via argparse."""
    if len(argv) < 2 or argv[0] != "db" or argv[1] != "backup":
        return False
    if "--help" in argv or "-h" in argv:
        _print_help_and_exit(["db", "backup"])
    parser = argparse.ArgumentParser(prog="owlclaw db backup")
    parser.add_argument("-o", "--output", required=True, help="Output file path")
    parser.add_argument("-F", "--format", dest="format_name", default="plain", help="plain or custom")
    parser.add_argument("--schema-only", action="store_true", help="Schema only")
    parser.add_argument("--data-only", action="store_true", help="Data only")
    parser.add_argument("--database-url", dest="database_url", default="", help="Database URL")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed progress")
    ns = parser.parse_args(argv[2:])
    from owlclaw.cli.db_backup import backup_command
    backup_command(
        output=ns.output,
        format_name=ns.format_name or "plain",
        schema_only=ns.schema_only,
        data_only=ns.data_only,
        database_url=ns.database_url or "",
        verbose=ns.verbose,
    )
    return True


def _dispatch_db_restore(argv: list[str]) -> bool:
    """Dispatch `owlclaw db restore` via argparse."""
    if len(argv) < 2 or argv[0] != "db" or argv[1] != "restore":
        return False
    if "--help" in argv or "-h" in argv:
        _print_help_and_exit(["db", "restore"])
    parser = argparse.ArgumentParser(prog="owlclaw db restore")
    parser.add_argument("-i", "--input", required=True, help="Input backup file path")
    parser.add_argument("--clean", action="store_true", help="Drop objects before restore (pg_restore)")
    parser.add_argument("--database-url", dest="database_url", default="", help="Database URL")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed progress")
    ns = parser.parse_args(argv[2:])
    from owlclaw.cli.db_restore import restore_command
    restore_command(
        input_path=ns.input,
        clean=ns.clean,
        database_url=ns.database_url or "",
        yes=ns.yes,
        verbose=ns.verbose,
    )
    return True


def main() -> None:
    """CLI entry point — dispatches to subcommands."""
    if "--version" in sys.argv or "-V" in sys.argv:
        _print_version_and_exit()
    if "--help" in sys.argv or "-h" in sys.argv:
        argv = [a for a in sys.argv[1:] if a not in ("--help", "-h")]
        _print_help_and_exit(argv)
    try:
        _main_impl()
    except KeyboardInterrupt:
        sys.exit(130)


def _main_impl() -> None:
    """Inner main; KeyboardInterrupt is handled in main()."""
    _register_subapps()
    try:
        if _dispatch_db_revision(sys.argv[1:]):
            return
    except SystemExit:
        raise
    try:
        if _dispatch_db_rollback(sys.argv[1:]):
            return
    except SystemExit:
        raise
    except Exception:
        raise
    try:
        if _dispatch_db_backup(sys.argv[1:]):
            return
    except SystemExit:
        raise
    except Exception:
        raise
    try:
        if _dispatch_db_restore(sys.argv[1:]):
            return
    except SystemExit:
        raise
    except Exception:
        raise
    try:
        if _dispatch_console_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_memory_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_ledger_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_agent_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_migration_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_approval_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_trigger_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_scan_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_migrate_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_release_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_start_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_console_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        if _dispatch_skill_command(sys.argv[1:]):
            return
    except ClickExit as e:
        raise SystemExit(e.exit_code) from None
    try:
        app()
    except TypeError as e:
        if "make_metavar" in str(e):
            argv = sys.argv[1:]
            _print_help_and_exit(argv)
        raise


if __name__ == "__main__":
    main()
