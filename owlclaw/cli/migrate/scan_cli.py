"""CLI handlers for `owlclaw migrate scan`."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import typer
import yaml  # type: ignore[import-untyped]

from owlclaw.cli.migrate.generators import BindingGenerator, OpenAPIEndpoint, ORMOperation

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def run_migrate_scan_command(
    *,
    openapi: str = "",
    orm: str = "",
    project: str = "",
    output_mode: str = "handler",
    output: str = ".",
    dry_run: bool = False,
    report_json: str = "",
    report_md: str = "",
    force: bool = False,
) -> None:
    mode = output_mode.strip().lower()
    if mode not in {"handler", "binding", "both", "mcp"}:
        raise typer.Exit(2)
    if not openapi.strip() and not orm.strip() and not project.strip():
        raise typer.Exit(2)

    output_dir = Path(output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    generator = BindingGenerator()

    endpoint_results: list[str] = []
    orm_results: list[str] = []
    previews: dict[str, str] = {}
    binding_count = 0
    handler_count = 0
    mcp_count = 0
    manual_review_items: list[str] = []

    if openapi.strip():
        endpoints = _load_openapi_endpoints(Path(openapi))
        for endpoint in endpoints:
            result = generator.generate_from_openapi(endpoint)
            if mode == "mcp":
                target = output_dir / "mcp_tools" / f"{result.skill_name}.json"
                _check_conflict(target, dry_run=dry_run, force=force)
                tool_payload = generator.generate_mcp_tool_definition(endpoint)
                rendered = json.dumps(tool_payload, ensure_ascii=False, indent=2)
                if not dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(rendered + "\n", encoding="utf-8")
                previews[str(target)] = _preview_lines(rendered)
                endpoint_results.append(str(target))
                mcp_count += 1
            if mode in {"binding", "both"}:
                target = output_dir / result.skill_name / "SKILL.md"
                _check_conflict(target, dry_run=dry_run, force=force)
                if not dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(result.skill_content, encoding="utf-8")
                previews[str(target)] = _preview_lines(result.skill_content)
                endpoint_results.append(str(target))
                binding_count += 1
            if mode in {"handler", "both"}:
                handler_path = output_dir / "handlers" / f"{result.skill_name}.py"
                _check_conflict(handler_path, dry_run=dry_run, force=force)
                if dry_run:
                    endpoint_results.append(str(handler_path))
                    previews[str(handler_path)] = _preview_lines(_render_handler_stub(result.skill_name))
                else:
                    endpoint_results.append(str(_write_handler_stub(output_dir, result.skill_name)))
                handler_count += 1

    if orm.strip():
        operations = _load_orm_operations(Path(orm))
        for operation in operations:
            result = generator.generate_from_orm(operation)
            if mode in {"binding", "both"}:
                target = output_dir / result.skill_name / "SKILL.md"
                _check_conflict(target, dry_run=dry_run, force=force)
                if not dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(result.skill_content, encoding="utf-8")
                previews[str(target)] = _preview_lines(result.skill_content)
                orm_results.append(str(target))
                binding_count += 1
            if mode in {"handler", "both"}:
                handler_path = output_dir / "handlers" / f"{result.skill_name}.py"
                _check_conflict(handler_path, dry_run=dry_run, force=force)
                if dry_run:
                    orm_results.append(str(handler_path))
                    previews[str(handler_path)] = _preview_lines(_render_handler_stub(result.skill_name))
                else:
                    orm_results.append(str(_write_handler_stub(output_dir, result.skill_name)))
                handler_count += 1

    if project.strip() and mode in {"handler", "both"}:
        candidates = _scan_python_candidates(Path(project))
        for candidate in candidates:
            target = output_dir / "handlers" / f"{candidate.skill_name}.py"
            _check_conflict(target, dry_run=dry_run, force=force)
            content = _render_python_handler(candidate)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            previews[str(target)] = _preview_lines(content)
            endpoint_results.append(str(target))
            handler_count += 1
            if candidate.manual_review_notes:
                manual_review_items.extend(candidate.manual_review_notes)

    generated = endpoint_results + orm_results
    if not generated:
        raise typer.Exit(2)

    report_payload = {
        "mode": mode,
        "dry_run": dry_run,
        "generated_count": len(generated),
        "generated_binding_count": binding_count,
        "generated_handler_count": handler_count,
        "generated_mcp_count": mcp_count,
        "generated_files": generated,
        "manual_review": sorted(set(manual_review_items)),
        "stats": {
            "openapi_endpoints": len(endpoint_results),
            "orm_operations": len(orm_results),
            "manual_review_count": len(set(manual_review_items)),
            "estimated_effort_hours": round(len(generated) * 0.5, 1),
        },
    }

    typer.echo(f"generated={len(generated)}")
    if dry_run:
        typer.echo("dry_run=true")
    for path in generated:
        typer.echo(path)
        if dry_run:
            typer.echo(f"preview: {previews.get(path, '')}")

    if dry_run:
        return

    report_json_path = Path(report_json) if report_json.strip() else output_dir / "migration_report.json"
    report_md_path = Path(report_md) if report_md.strip() else output_dir / "migration_report.md"
    report_json_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md_path.write_text(_render_report_markdown(report_payload), encoding="utf-8")
    typer.echo(f"report_json={report_json_path}")
    typer.echo(f"report_md={report_md_path}")


def _load_openapi_endpoints(path: Path) -> list[OpenAPIEndpoint]:
    payload = _load_data(path)
    if not isinstance(payload, dict):
        return []

    servers = payload.get("servers", [])
    server_url = ""
    if isinstance(servers, list) and servers:
        first = servers[0]
        if isinstance(first, dict):
            server_url = str(first.get("url", "")).strip()

    components = payload.get("components", {})
    security_schemes: dict[str, dict[str, Any]] = {}
    if isinstance(components, dict):
        raw = components.get("securitySchemes", {})
        if isinstance(raw, dict):
            for name, spec in raw.items():
                if isinstance(name, str) and isinstance(spec, dict):
                    security_schemes[name] = spec

    global_security = payload.get("security", [])
    endpoints: list[OpenAPIEndpoint] = []
    paths = payload.get("paths", {})
    if not isinstance(paths, dict):
        return []
    for api_path, operations in paths.items():
        if not isinstance(api_path, str) or not isinstance(operations, dict):
            continue
        for method, op in operations.items():
            m = str(method).lower()
            if m not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            security = op.get("security", global_security)
            endpoints.append(
                OpenAPIEndpoint(
                    method=m,
                    path=api_path,
                    operation_id=str(op.get("operationId", "")),
                    summary=str(op.get("summary", "")),
                    description=str(op.get("description", "")),
                    parameters=op.get("parameters", []) if isinstance(op.get("parameters", []), list) else [],
                    request_body=op.get("requestBody", {}) if isinstance(op.get("requestBody", {}), dict) else {},
                    responses=op.get("responses", {}) if isinstance(op.get("responses", {}), dict) else {},
                    security=security if isinstance(security, list) else [],
                    security_schemes=security_schemes,
                    server_url=server_url,
                )
            )
    return endpoints


def _load_orm_operations(path: Path) -> list[ORMOperation]:
    payload = _load_data(path)
    if not isinstance(payload, dict):
        return []
    operations = payload.get("operations", [])
    if not isinstance(operations, list):
        return []
    out: list[ORMOperation] = []
    for item in operations:
        if not isinstance(item, dict):
            continue
        model_name = str(item.get("model_name", "")).strip()
        table_name = str(item.get("table_name", "")).strip()
        if not model_name or not table_name:
            continue
        columns = item.get("columns", [])
        filters = item.get("filters", [])
        out.append(
            ORMOperation(
                model_name=model_name,
                table_name=table_name,
                columns=[str(c) for c in columns if isinstance(c, str)],
                filters=[str(c) for c in filters if isinstance(c, str)],
                connection_env=str(item.get("connection_env", "READ_DB_DSN")),
            )
        )
    return out


def _load_data(path: Path) -> dict[str, Any] | list[Any] | str | None:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        return cast(dict[str, Any] | list[Any] | str | None, json.loads(text))
    return cast(dict[str, Any] | list[Any] | str | None, yaml.safe_load(text))


def _write_handler_stub(output_dir: Path, skill_name: str) -> Path:
    handlers_dir = output_dir / "handlers"
    handlers_dir.mkdir(parents=True, exist_ok=True)
    path = handlers_dir / f"{skill_name}.py"
    path.write_text(_render_handler_stub(skill_name), encoding="utf-8")
    return path


def _render_handler_stub(skill_name: str) -> str:
    return (
        "from __future__ import annotations\n\n"
        f"async def {skill_name.replace('-', '_')}_handler(params: dict) -> dict:\n"
        '    """Generated handler stub from migrate scan."""\n'
        '    return {"status": "todo", "params": params}\n'
    )


def _check_conflict(path: Path, *, dry_run: bool, force: bool) -> None:
    if path.exists() and not force:
        typer.echo(f"conflict: target exists: {path}", err=True)
        if dry_run:
            return
        raise typer.Exit(2)


def _preview_lines(content: str, limit: int = 2) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return " | ".join(lines[:limit])


def _render_report_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Migration Report",
        "",
        f"- mode: `{payload['mode']}`",
        f"- dry_run: `{payload['dry_run']}`",
        f"- generated_count: `{payload['generated_count']}`",
        f"- generated_binding_count: `{payload['generated_binding_count']}`",
        f"- generated_handler_count: `{payload['generated_handler_count']}`",
        f"- generated_mcp_count: `{payload.get('generated_mcp_count', 0)}`",
        "",
        "## Stats",
        "",
        f"- openapi_endpoints: `{payload['stats']['openapi_endpoints']}`",
        f"- orm_operations: `{payload['stats']['orm_operations']}`",
        f"- manual_review_count: `{payload['stats']['manual_review_count']}`",
        f"- estimated_effort_hours: `{payload['stats']['estimated_effort_hours']}`",
        "",
        "## Generated Files",
        "",
    ]
    for item in payload["generated_files"]:
        lines.append(f"- `{item}`")
    manual_review = payload.get("manual_review", [])
    if isinstance(manual_review, list) and manual_review:
        lines.extend(["", "## MANUAL_REVIEW", ""])
        for item in manual_review:
            lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


@dataclass(slots=True)
class _PyParam:
    name: str
    type_name: str
    has_type: bool


@dataclass(slots=True)
class _PyCandidate:
    skill_name: str
    function_name: str
    module_path: str
    parameters: list[_PyParam]
    manual_review_notes: list[str]


def _scan_python_candidates(project_path: Path) -> list[_PyCandidate]:
    candidates: list[_PyCandidate] = []
    root = project_path.resolve()
    for file_path in root.rglob("*.py"):
        if file_path.name.startswith("test_"):
            continue
        if ".venv" in file_path.parts:
            continue
        rel = file_path.relative_to(root)
        module_path = ".".join(rel.with_suffix("").parts)
        if module_path.endswith(".__init__"):
            module_path = module_path.rsplit(".__init__", 1)[0]
        if not module_path:
            continue
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name.startswith("_"):
                continue
            params: list[_PyParam] = []
            manual: list[str] = []
            for arg in node.args.args:
                if arg.arg in {"self", "cls"}:
                    continue
                if arg.annotation is None:
                    params.append(_PyParam(name=arg.arg, type_name="Any", has_type=False))
                    manual.append(f"{module_path}.{node.name}:{arg.arg} missing type hint")
                else:
                    params.append(_PyParam(name=arg.arg, type_name=ast.unparse(arg.annotation), has_type=True))
            candidates.append(
                _PyCandidate(
                    skill_name=node.name.replace("_", "-"),
                    function_name=node.name,
                    module_path=module_path,
                    parameters=params,
                    manual_review_notes=manual,
                )
            )
    return candidates


def _render_python_handler(candidate: _PyCandidate) -> str:
    params = ", ".join(f"{param.name}: {param.type_name}" for param in candidate.parameters) or ""
    args = ", ".join(f"{param.name}={param.name}" for param in candidate.parameters)
    lines = [
        "from __future__ import annotations",
        "",
        "from importlib import import_module",
        "from inspect import isawaitable",
        "from typing import Any",
        "",
        "",
        "def register_handlers(app: Any) -> None:",
        f'    @app.handler("{candidate.skill_name}")',
        f"    async def {candidate.function_name}_handler({params}) -> dict[str, Any]:",
        f'        module = import_module("{candidate.module_path}")',
        f'        target = getattr(module, "{candidate.function_name}")',
        f"        result = target({args})" if args else "        result = target()",
        "        if isawaitable(result):",
        "            result = await result",
        '        return {"result": result}',
    ]
    if candidate.manual_review_notes:
        lines.extend(["", "# MANUAL_REVIEW"])
        for note in candidate.manual_review_notes:
            lines.append(f"# {note}")
    lines.append("")
    return "\n".join(lines)
