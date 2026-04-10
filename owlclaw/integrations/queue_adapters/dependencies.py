"""Queue adapter dependency checks."""

from __future__ import annotations

from importlib.util import find_spec

_ADAPTER_DEPENDENCIES: dict[str, tuple[str, str]] = {
    "kafka": ("aiokafka", "poetry add aiokafka"),
    "rabbitmq": ("aio_pika", "poetry add aio-pika"),
    "sqs": ("aioboto3", "poetry add aioboto3"),
}


def ensure_adapter_dependency(adapter_type: str) -> None:
    """Validate optional dependency for a concrete queue adapter."""
    normalized = adapter_type.strip().lower()
    if normalized == "mock":
        return
    dependency = _ADAPTER_DEPENDENCIES.get(normalized)
    if dependency is None:
        supported = ", ".join(sorted([*list(_ADAPTER_DEPENDENCIES.keys()), "mock"]))
        raise ValueError(f"Unsupported queue adapter type: {adapter_type}. Supported: {supported}")

    package_name, install_command = dependency
    if find_spec(package_name) is not None:
        return
    raise RuntimeError(
        f"Queue adapter '{normalized}' requires optional dependency '{package_name}'. "
        f"Install with: {install_command}"
    )
