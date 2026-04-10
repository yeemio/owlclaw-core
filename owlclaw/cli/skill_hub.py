"""owlclaw skill hub commands: search/install/installed."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import typer
import yaml  # type: ignore[import-untyped]

from owlclaw.cli.api_client import SkillHubApiClient
from owlclaw.owlhub import OwlHubClient
from owlclaw.owlhub.client import SearchResult
from owlclaw.owlhub.semantic_search import SemanticDocument, SemanticSearcher
from owlclaw.templates.skills import TemplateRegistry, get_default_templates_dir

logger = logging.getLogger(__name__)


def _create_index_client(index_url: str, install_dir: str, lock_file: str, *, no_cache: bool = False) -> OwlHubClient:
    return OwlHubClient(
        index_url=index_url,
        install_dir=Path(install_dir).resolve(),
        lock_file=Path(lock_file).resolve(),
        no_cache=no_cache,
    )


def _echo(
    message: str,
    *,
    quiet: bool = False,
    color: str | None = None,
    err: bool = False,
) -> None:
    if quiet is True and not err:
        return
    typer.secho(message, fg=color, err=err)


def search_command(
    query: str = typer.Option("", "--query", "-q", help="Search query."),
    index_url: str = typer.Option("./index.json", "--index-url", help="Path/URL to index.json."),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags filter."),
    industry: str = typer.Option("", "--industry", help="Industry filter (e.g. retail)."),
    sort_by: str = typer.Option("name", "--sort-by", help="Sort by: name or quality_score."),
    tag_mode: str = typer.Option("and", "--tag-mode", help="Tag filter mode: and/or."),
    include_draft: bool = typer.Option(False, "--include-draft", help="Include draft versions in results."),
    mode: str = typer.Option("auto", "--mode", help="Hub mode: auto/index/api."),
    api_base_url: str = typer.Option("", "--api-base-url", help="OwlHub API base URL."),
    api_token: str = typer.Option("", "--api-token", help="OwlHub API token."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass local cache."),
    install_dir: str = typer.Option(
        "./.owlhub/skills", "--install-dir", help="Install directory for skills."
    ),
    lock_file: str = typer.Option("./skill-lock.json", "--lock-file", help="Lock file path."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed progress and diagnostics."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress non-error output."),
) -> None:
    """Search skills in OwlHub index."""
    index_client = _create_index_client(index_url=index_url, install_dir=install_dir, lock_file=lock_file, no_cache=no_cache)
    client = SkillHubApiClient(
        index_client=index_client,
        api_base_url=api_base_url,
        api_token=api_token,
        mode=mode,
        no_cache=no_cache,
    )
    if verbose:
        _echo(
            f"Search context: mode={mode} api_base_url={api_base_url or '-'} index_url={index_url}",
            quiet=quiet,
            color="blue",
        )
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else []
    results = _search_with_semantic_ranking(
        client=client,
        query=query,
        tags=tag_list,
        industry=industry,
        tag_mode=tag_mode,
        include_draft=include_draft,
        sort_by=sort_by,
        verbose=verbose,
        quiet=quiet,
    )
    if not results:
        _echo("No skills found.", quiet=quiet, color="yellow")
        return
    for item in results:
        rendered_tags = ",".join(item.tags) if item.tags else "-"
        score_text = f" score={item.score:.3f}" if item.score is not None else ""
        quality_text = f" quality={item.quality_score:.3f}" if item.quality_score is not None else ""
        warning_text = " [LOW_QUALITY]" if item.low_quality_warning else ""
        install_hint = _format_install_hint(item)
        _echo(f"{item.name}@{item.version} [{item.version_state}] ({item.publisher}) [{item.source}] - {item.description}", quiet=quiet)
        _echo(
            f"  tags={rendered_tags} industry={item.industry or '-'}{score_text}{quality_text}{warning_text}",
            quiet=quiet,
            color="blue",
        )
        _echo(f"  install: {install_hint}", quiet=quiet, color="green")


def install_command(
    name: str = typer.Argument("", help="Skill name to install."),
    version: str = typer.Option("", "--version", help="Exact version to install."),
    package: str = typer.Option("", "--package", help="Path to package.yaml for batch install."),
    no_deps: bool = typer.Option(False, "--no-deps", help="Skip dependency installation."),
    force: bool = typer.Option(False, "--force", help="Force install on checksum/moderation errors."),
    mode: str = typer.Option("auto", "--mode", help="Hub mode: auto/index/api."),
    api_base_url: str = typer.Option("", "--api-base-url", help="OwlHub API base URL."),
    api_token: str = typer.Option("", "--api-token", help="OwlHub API token."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass local cache."),
    index_url: str = typer.Option("./index.json", "--index-url", help="Path/URL to index.json."),
    install_dir: str = typer.Option(
        "./.owlhub/skills", "--install-dir", help="Install directory for skills."
    ),
    lock_file: str = typer.Option("./skill-lock.json", "--lock-file", help="Lock file path."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed progress and diagnostics."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress non-error output."),
) -> None:
    """Install one skill from OwlHub index."""
    index_client = _create_index_client(index_url=index_url, install_dir=install_dir, lock_file=lock_file, no_cache=no_cache)
    client = SkillHubApiClient(
        index_client=index_client,
        api_base_url=api_base_url,
        api_token=api_token,
        mode=mode,
        no_cache=no_cache,
    )
    if verbose:
        _echo(f"Step 1/3: resolving package {name}@{version or 'latest'}", quiet=quiet, color="blue")
    if package:
        try:
            package_payload = _load_package_file(Path(package).resolve())
        except Exception as exc:
            _echo(f"Error: invalid package file '{package}' ({exc})", err=True, color="red")
            raise typer.Exit(2) from exc
        installed = _install_package_skills(
            client=client,
            package_payload=package_payload,
            no_deps=no_deps,
            force=force,
            verbose=verbose,
            quiet=quiet,
        )
        _echo(f"Installed {len(installed)} skills from package '{package_payload.get('name', '')}'.", quiet=quiet, color="green")
        return
    if not name.strip():
        _echo("Error: missing skill name (or pass --package path/to/package.yaml).", err=True, color="red")
        raise typer.Exit(2)
    if not no_deps:
        candidates = index_client.search(query=name, include_draft=True)
        selected = [item for item in candidates if item.name == name and (not version or item.version == version)]
        if selected:
            dependencies = selected[-1].dependencies
            if dependencies:
                _echo("Dependencies:", quiet=quiet, color="blue")
                for dep_name, constraint in sorted(dependencies.items()):
                    _echo(f"  - {dep_name} ({constraint})", quiet=quiet)
    if verbose:
        _echo("Step 2/3: downloading and verifying package checksum", quiet=quiet, color="blue")
    try:
        installed_path = client.install(name=name, version=version or None, no_deps=no_deps, force=force)
    except Exception as exc:
        logger.exception(
            "Skill install failed: %s",
            json.dumps({"event": "skill_install_error", "name": name, "version": version or "latest"}, ensure_ascii=False),
        )
        _echo(
            f"Error: install failed for {name}@{version or 'latest'} ({exc}). Try --verbose for details.",
            err=True,
            color="red",
        )
        raise typer.Exit(1) from exc
    logger.info(
        "%s",
        json.dumps(
            {
                "event": "skill_install",
                "name": name,
                "version": version or "latest",
                "path": str(installed_path),
                "mode": mode,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    if client.last_install_warning:
        _echo(f"Warning: {client.last_install_warning}", quiet=quiet, color="yellow")
    if verbose:
        _echo("Step 3/3: finalizing lock file and installation metadata", quiet=quiet, color="blue")
    _echo(f"Installed: {name} -> {installed_path}", quiet=quiet, color="green")


def _load_package_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError("package file does not exist")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("package.yaml must be a mapping")
    skills = data.get("skills")
    if not isinstance(skills, list) or not skills:
        raise ValueError("package.yaml must define non-empty skills list")
    normalized_skills = [str(item).strip() for item in skills if isinstance(item, str) and str(item).strip()]
    if not normalized_skills:
        raise ValueError("package.yaml skills list contains no valid skill names")
    data["skills"] = normalized_skills
    return data


def _install_package_skills(
    *,
    client: SkillHubApiClient,
    package_payload: dict[str, Any],
    no_deps: bool,
    force: bool,
    verbose: bool,
    quiet: bool,
) -> list[Path]:
    installed: list[Path] = []
    skill_names = package_payload.get("skills", [])
    if not isinstance(skill_names, list):
        return installed
    for skill_name in skill_names:
        if not isinstance(skill_name, str) or not skill_name.strip():
            continue
        normalized = skill_name.strip()
        if verbose:
            _echo(f"Installing from package: {normalized}", quiet=quiet, color="blue")
        target = client.install(name=normalized, version=None, no_deps=no_deps, force=force)
        installed.append(target)
        _echo(f"Installed: {normalized} -> {target}", quiet=quiet, color="green")
    return installed


def _search_with_semantic_ranking(
    *,
    client: SkillHubApiClient,
    query: str,
    tags: list[str],
    industry: str,
    tag_mode: str,
    include_draft: bool,
    sort_by: str,
    verbose: bool,
    quiet: bool,
) -> list[SearchResult]:
    normalized_query = query.strip()
    if not normalized_query:
        return client.search(
            query=query,
            tags=tags,
            tag_mode=tag_mode,
            include_draft=include_draft,
            industry=industry,
            sort_by=sort_by,
        )

    owlhub_candidates = client.search(
        query="",
        tags=tags,
        tag_mode=tag_mode,
        include_draft=include_draft,
        industry=industry,
        sort_by=sort_by,
    )
    template_candidates = _build_template_candidates(industry=industry)
    all_candidates = owlhub_candidates + template_candidates
    semantic_docs = [
        SemanticDocument(
            doc_id=_result_id(item),
            text=_result_text(item),
        )
        for item in all_candidates
    ]
    cache_path = Path(".owlclaw/cache/skill_embeddings.json")
    try:
        searcher = SemanticSearcher(cache_path=cache_path)
        ranked_pairs = searcher.rank(normalized_query, semantic_docs, top_k=20)
        scores = {doc_id: score for doc_id, score in ranked_pairs}
        ranked_results = [
            SearchResult(
                name=item.name,
                publisher=item.publisher,
                version=item.version,
                description=item.description,
                tags=item.tags,
                version_state=item.version_state,
                download_url=item.download_url,
                checksum=item.checksum,
                dependencies=item.dependencies,
                industry=item.industry,
                source=item.source,
                score=scores[_result_id(item)],
            )
            for item in all_candidates
            if _result_id(item) in scores
        ]
        ranked_results.sort(key=lambda item: item.score or 0.0, reverse=True)
        return ranked_results
    except Exception as exc:
        if verbose:
            _echo(
                f"Semantic search unavailable ({exc}); fallback to keyword search.",
                quiet=quiet,
                color="yellow",
            )
        keyword_results = client.search(
            query=normalized_query,
            tags=tags,
            tag_mode=tag_mode,
            include_draft=include_draft,
            industry=industry,
            sort_by=sort_by,
        )
        keyword_results.extend(_keyword_template_candidates(normalized_query, industry=industry))
        return keyword_results


def _build_template_candidates(*, industry: str) -> list[SearchResult]:
    candidates: list[SearchResult] = []
    requested_industry = industry.strip().lower()
    registry = TemplateRegistry(get_default_templates_dir())
    for template in registry.list_templates():
        template_industry = _template_industry(template.category.value)
        if requested_industry and template_industry != requested_industry:
            continue
        candidates.append(
            SearchResult(
                name=template.id,
                publisher="local-template",
                version="template",
                description=template.description,
                tags=template.tags,
                version_state="template",
                download_url="",
                checksum="",
                dependencies={},
                industry=template_industry,
                source="template",
            )
        )
    return candidates


def _keyword_template_candidates(query: str, *, industry: str) -> list[SearchResult]:
    query_lower = query.strip().lower()
    if not query_lower:
        return []
    filtered = []
    for item in _build_template_candidates(industry=industry):
        if query_lower in _result_text(item).lower():
            filtered.append(item)
    return filtered


def _template_industry(category: str) -> str:
    mapping = {
        "monitoring": "operations",
        "analysis": "analytics",
        "workflow": "general",
        "integration": "software",
        "report": "general",
    }
    return mapping.get(category.strip().lower(), "general")


def _result_id(item: SearchResult) -> str:
    return f"{item.source}:{item.publisher}/{item.name}@{item.version}"


def _result_text(item: SearchResult) -> str:
    return " ".join(
        part
        for part in [
            item.name,
            item.description,
            " ".join(item.tags),
            item.industry,
        ]
        if part
    )


def _format_install_hint(item: SearchResult) -> str:
    if item.source == "template":
        return f"owlclaw skill init --template {item.name}"
    suffix = f" --version {item.version}" if item.version else ""
    return f"owlclaw skill install {item.name}{suffix}"


def installed_command(
    mode: str = typer.Option("auto", "--mode", help="Hub mode: auto/index/api."),
    api_base_url: str = typer.Option("", "--api-base-url", help="OwlHub API base URL."),
    api_token: str = typer.Option("", "--api-token", help="OwlHub API token."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass local cache."),
    index_url: str = typer.Option("./index.json", "--index-url", help="Path/URL to index.json."),
    install_dir: str = typer.Option(
        "./.owlhub/skills", "--install-dir", help="Install directory for skills."
    ),
    lock_file: str = typer.Option("./skill-lock.json", "--lock-file", help="Lock file path."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed progress and diagnostics."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress non-error output."),
) -> None:
    """List installed skills from lock file."""
    client = SkillHubApiClient(
        index_client=_create_index_client(index_url=index_url, install_dir=install_dir, lock_file=lock_file, no_cache=no_cache),
        api_base_url=api_base_url,
        api_token=api_token,
        mode=mode,
        no_cache=no_cache,
    )
    installed = client.list_installed()
    if not installed:
        _echo("No installed skills.", quiet=quiet, color="yellow")
        return
    if verbose:
        _echo(f"Loaded {len(installed)} installed skill entries from {lock_file}", quiet=quiet, color="blue")
    for item in installed:
        state = item.get("version_state", "released")
        _echo(f"{item.get('name')}@{item.get('version')} [{state}] ({item.get('publisher')})", quiet=quiet)


def update_command(
    name: str = typer.Argument("", help="Optional skill name to update."),
    mode: str = typer.Option("auto", "--mode", help="Hub mode: auto/index/api."),
    api_base_url: str = typer.Option("", "--api-base-url", help="OwlHub API base URL."),
    api_token: str = typer.Option("", "--api-token", help="OwlHub API token."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass local cache."),
    index_url: str = typer.Option("./index.json", "--index-url", help="Path/URL to index.json."),
    install_dir: str = typer.Option(
        "./.owlhub/skills", "--install-dir", help="Install directory for skills."
    ),
    lock_file: str = typer.Option("./skill-lock.json", "--lock-file", help="Lock file path."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed progress and diagnostics."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress non-error output."),
) -> None:
    """Update one skill or all installed skills."""
    client = SkillHubApiClient(
        index_client=_create_index_client(index_url=index_url, install_dir=install_dir, lock_file=lock_file, no_cache=no_cache),
        api_base_url=api_base_url,
        api_token=api_token,
        mode=mode,
        no_cache=no_cache,
    )
    if verbose:
        _echo(f"Checking updates for {name or 'all installed skills'}", quiet=quiet, color="blue")
    changes = client.update(name=name or None)
    if not changes:
        _echo("No updates available.", quiet=quiet, color="yellow")
        return
    for change in changes:
        _echo(f"Updated: {change['name']} {change['from_version']} -> {change['to_version']}", quiet=quiet, color="green")


def publish_command(
    path: str = typer.Argument(".", help="Skill package directory path."),
    mode: str = typer.Option("api", "--mode", help="Hub mode: auto/index/api."),
    api_base_url: str = typer.Option("", "--api-base-url", help="OwlHub API base URL."),
    api_token: str = typer.Option("", "--api-token", help="OwlHub API token."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass local cache."),
    index_url: str = typer.Option("./index.json", "--index-url", help="Path/URL to index.json."),
    install_dir: str = typer.Option(
        "./.owlhub/skills", "--install-dir", help="Install directory for skills."
    ),
    lock_file: str = typer.Option("./skill-lock.json", "--lock-file", help="Lock file path."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed progress and diagnostics."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress non-error output."),
) -> None:
    """Publish one local skill to OwlHub API."""
    client = SkillHubApiClient(
        index_client=_create_index_client(index_url=index_url, install_dir=install_dir, lock_file=lock_file, no_cache=no_cache),
        api_base_url=api_base_url,
        api_token=api_token,
        mode=mode,
        no_cache=no_cache,
    )
    if verbose:
        _echo(f"Publishing skill package from {Path(path).resolve()}", quiet=quiet, color="blue")
    try:
        result = client.publish(skill_path=Path(path).resolve())
    except Exception as exc:
        logger.exception(
            "Skill publish failed: %s",
            json.dumps({"event": "skill_publish_error", "path": str(Path(path).resolve()), "mode": mode}, ensure_ascii=False),
        )
        _echo(
            f"Error: publish failed for {Path(path).resolve()} ({exc}). Check API credentials and package structure.",
            err=True,
            color="red",
        )
        raise typer.Exit(1) from exc
    logger.info(
        "%s",
        json.dumps(
            {
                "event": "skill_publish",
                "path": str(Path(path).resolve()),
                "mode": mode,
                "review_id": result.get("review_id", ""),
                "status": result.get("status", ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    _echo(
        f"Published: review_id={result.get('review_id', '')} status={result.get('status', '')}",
        quiet=quiet,
        color="green",
    )


def cache_clear_command(
    index_url: str = typer.Option("./index.json", "--index-url", help="Path/URL to index.json."),
    install_dir: str = typer.Option(
        "./.owlhub/skills", "--install-dir", help="Install directory for skills."
    ),
    lock_file: str = typer.Option("./skill-lock.json", "--lock-file", help="Lock file path."),
) -> None:
    """Clear local OwlHub cache files."""
    client = _create_index_client(index_url=index_url, install_dir=install_dir, lock_file=lock_file, no_cache=False)
    removed = client.clear_cache()
    typer.echo(f"Cache cleared: {removed} files")
