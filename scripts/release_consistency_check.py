"""Check local release consistency before external publish."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py310 fallback
    import tomli as tomllib  # type: ignore[no-redef]

def check_consistency(repo: Path) -> int:
    pyproject = repo / "pyproject.toml"
    changelog = repo / "CHANGELOG.md"
    release_workflow = repo / ".github" / "workflows" / "release.yml"

    if not pyproject.exists() or not changelog.exists() or not release_workflow.exists():
        print("missing required files")
        return 2

    version = _extract_poetry_version(pyproject.read_text(encoding="utf-8"))
    if not version:
        print("failed to parse version from pyproject.toml")
        return 2
    changelog_text = changelog.read_text(encoding="utf-8")
    workflow_text = release_workflow.read_text(encoding="utf-8")

    if f"## [{version}] " not in changelog_text:
        print(f"changelog missing version section: {version}")
        return 2
    if "notes-file CHANGELOG.md" not in workflow_text:
        print("release workflow does not use changelog notes file")
        return 2
    if "pyproject.toml" not in workflow_text:
        print("release workflow does not derive version from pyproject.toml")
        return 2

    print(f"release_consistency_ok=true version={version}")
    return 0


def _extract_poetry_version(pyproject_text: str) -> str | None:
    try:
        payload = tomllib.loads(pyproject_text)
    except Exception:
        return None
    tool = payload.get("tool")
    if not isinstance(tool, dict):
        return None
    poetry = tool.get("poetry")
    if not isinstance(poetry, dict):
        return None
    version = poetry.get("version")
    return version if isinstance(version, str) else None


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    raise SystemExit(check_consistency(repo))


if __name__ == "__main__":
    main()
