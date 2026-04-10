"""CLI handlers for `owlclaw scan`."""

from __future__ import annotations

from pathlib import Path

import typer

from owlclaw.cli.scan import (
    ConfigManager,
    IncrementalScanner,
    JSONSerializer,
    ProjectScanner,
    ScanConfig,
    YAMLSerializer,
)
from owlclaw.cli.scan.models import ScanMetadata, ScanResult


class OutputFormatter:
    """Format scan results for stdout/file output."""

    def format(self, result: ScanResult, format_name: str, pretty: bool = True) -> str:
        if format_name == "json":
            return JSONSerializer(pretty=pretty).serialize(result)
        if format_name == "yaml":
            return YAMLSerializer().serialize(result)
        raise ValueError(f"unsupported format: {format_name}")


def run_scan_command(
    path: str = ".",
    format_name: str = "json",
    output: str = "",
    incremental: bool = False,
    workers: int = 1,
    config: str = "",
    verbose: bool = False,
) -> None:
    project_path = Path(path).resolve()
    manager = ConfigManager()
    loaded = manager.load(project_path, Path(config) if config else None)
    overrides = {
        "project_path": project_path,
        "incremental": incremental or loaded.incremental,
        "workers": workers if workers > 0 else loaded.workers,
    }
    scan_config = ScanConfig(**{**manager.to_dict(loaded), **overrides})
    scanner = ProjectScanner(scan_config)

    result = _run_incremental_scan(scanner, project_path) if scan_config.incremental else scanner.scan()

    formatter = OutputFormatter()
    payload = formatter.format(result, format_name=format_name, pretty=True)
    if output:
        Path(output).write_text(payload, encoding="utf-8")
    else:
        typer.echo(payload)

    if verbose:
        _print_stats(result.metadata)


def validate_scan_config_command(path: str = ".", config: str = "") -> None:
    project_path = Path(path).resolve()
    manager = ConfigManager()
    config_path = Path(config) if config else project_path / manager.DEFAULT_FILE
    if config and not config_path.exists():
        raise typer.Exit(2)

    if config_path.exists():
        manager.validate(_load_yaml(config_path))
    else:
        manager.validate({})
    typer.echo("scan config is valid")


def _run_incremental_scan(scanner: ProjectScanner, project_path: Path) -> ScanResult:
    incremental = IncrementalScanner(project_path)
    all_files = scanner.file_discovery.discover(project_path)
    changed_files = incremental.get_changed_files(all_files)
    mtimes, cached = incremental.load_cache()
    _ = mtimes  # keep for traceability; mtimes are refreshed on save.

    updated = {str(path.relative_to(project_path)).replace("\\", "/"): scanner._scan_file(path) for path in changed_files}
    merged = incremental.merge_results(cached, updated, all_files)
    incremental.save_cache(merged)

    failed = sum(1 for item in merged.values() if item.errors)
    metadata = ScanMetadata(
        project_path=str(project_path),
        scanned_files=len(merged),
        failed_files=failed,
        scan_time_seconds=0.0,
    )
    return ScanResult(metadata=metadata, files=merged)


def _load_yaml(path: Path) -> dict:
    import yaml  # type: ignore[import-untyped]

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("config file must contain object")
    return payload


def _print_stats(metadata: ScanMetadata) -> None:
    typer.echo(f"files_scanned={metadata.scanned_files}")
    typer.echo(f"files_failed={metadata.failed_files}")
    typer.echo(f"duration_sec={metadata.scan_time_seconds:.3f}")
