"""LangChain dependency/version checks."""

from __future__ import annotations

from importlib import metadata

from packaging.version import Version


def check_langchain_version(
    *,
    min_version: str = "0.1.0",
    max_version: str = "0.3.0",
) -> None:
    """Validate langchain and langchain-core are installed and in supported range."""
    min_v = Version(min_version)
    max_v = Version(max_version)

    langchain_v = _get_version_or_raise("langchain")
    _validate_range("langchain", langchain_v, min_v, max_v)

    core_v = _get_version_or_raise("langchain-core")
    _validate_range("langchain-core", core_v, min_v, max_v)


def _get_version_or_raise(package: str) -> Version:
    try:
        return Version(metadata.version(package))
    except metadata.PackageNotFoundError as exc:
        raise ImportError(
            f"{package} is not installed. Install LangChain support with: pip install owlclaw[langchain]"
        ) from exc


def _validate_range(package: str, version: Version, min_v: Version, max_v: Version) -> None:
    if version < min_v or version >= max_v:
        raise ImportError(
            f"{package} version {version} is not supported. "
            f"Expected >= {min_v} and < {max_v}."
        )
