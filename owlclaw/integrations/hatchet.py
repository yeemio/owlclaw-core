"""
Hatchet integration — durable execution, scheduling, and cron.

All Hatchet SDK usage is isolated in this module. The OwlClaw API is
HatchetConfig, HatchetClient, and the task() decorator.
"""

import logging
import os
import re
import signal
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)

# Lazy import to avoid loading hatchet_sdk when not using Hatchet
def _get_hatchet():
    from hatchet_sdk import Hatchet
    from hatchet_sdk.config import ClientConfig, ClientTLSConfig

    return Hatchet, ClientConfig, ClientTLSConfig


def _substitute_env(value: str) -> str:
    """Replace ${VAR} and $VAR with environment variable values."""
    if not isinstance(value, str):
        return value
    pattern = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
    def repl(match):
        name = match.group(1) or match.group(2)
        return os.environ.get(name, "")
    return pattern.sub(repl, value)


def _substitute_env_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively substitute ${VAR} in string values."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = _substitute_env_dict(v)
        elif isinstance(v, str):
            out[k] = _substitute_env(v)
        else:
            out[k] = v
    return out


class HatchetConfig(BaseModel):
    """Hatchet connection and worker configuration."""

    server_url: str = "http://localhost:7077"
    api_token: str | None = None
    grpc_host_port: str | None = None
    grpc_tls_strategy: str = "tls"
    connect_timeout_seconds: float = 30.0

    @model_validator(mode="before")
    @classmethod
    def server_url_from_env(cls, data: Any) -> Any:
        if isinstance(data, dict):
            updates: dict[str, Any] = {}
            if "server_url" not in data:
                url = os.environ.get("HATCHET_SERVER_URL", "").strip()
                if url:
                    updates["server_url"] = url
            if "grpc_tls_strategy" not in data:
                tls_strategy = os.environ.get("HATCHET_GRPC_TLS_STRATEGY", "").strip()
                if tls_strategy:
                    updates["grpc_tls_strategy"] = tls_strategy
            if "grpc_host_port" not in data:
                host_port = os.environ.get("HATCHET_GRPC_HOST_PORT", "").strip()
                if host_port:
                    updates["grpc_host_port"] = host_port
            if updates:
                data = {**data, **updates}
        return data
    namespace: str = "owlclaw"
    mode: str = "production"

    # Hatchet Server connects to the independent hatchet database (see docs/DATABASE_ARCHITECTURE.md)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "hatchet"
    postgres_user: str = "hatchet"
    postgres_password: str = ""

    max_concurrent_tasks: int = 10
    worker_name: str | None = None

    @field_validator("mode")
    @classmethod
    def mode_must_be_production_or_lite(cls, v: str) -> str:
        if v not in ("production", "lite"):
            raise ValueError("mode must be 'production' or 'lite'")
        return v

    @field_validator("server_url")
    @classmethod
    def server_url_format(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("server_url must start with http:// or https://")
        return v.rstrip("/")

    @field_validator("postgres_port")
    @classmethod
    def postgres_port_range(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("postgres_port must be between 1 and 65535")
        return v

    @field_validator("grpc_tls_strategy")
    @classmethod
    def grpc_tls_strategy_not_empty(cls, v: str) -> str:
        value = v.strip().lower()
        if not value:
            raise ValueError("grpc_tls_strategy cannot be empty")
        return value

    @field_validator("grpc_host_port")
    @classmethod
    def grpc_host_port_not_empty(cls, v: str | None) -> str | None:
        if v is None:
            return None
        value = v.strip()
        if not value:
            raise ValueError("grpc_host_port cannot be empty")
        return value

    @field_validator("connect_timeout_seconds")
    @classmethod
    def connect_timeout_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        return v

    @classmethod
    def from_yaml(cls, config_path: Path | str) -> "HatchetConfig":
        """Load configuration from owlclaw.yaml (hatchet section)."""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        hatchet_data = data.get("hatchet", {})
        hatchet_data = _substitute_env_dict(hatchet_data)
        return cls.model_validate(hatchet_data)


def _server_url_to_host_port(server_url: str) -> str:
    """Convert http://host:port to host:port."""
    if server_url.startswith("http://"):
        rest = server_url[7:]
    elif server_url.startswith("https://"):
        rest = server_url[8:]
    else:
        rest = server_url
    if "/" in rest:
        rest = rest.split("/", 1)[0]
    return rest if ":" in rest else f"{rest}:7077"


# Minimal cron format: 5 or 6 fields (sec optional): sec min hour day month dow, or min hour day month dow
_CRON_PARTS = re.compile(
    r"^(\S+\s+){4}\S+$|^(\S+\s+){5}\S+$"
)


def _validate_cron(cron: str) -> None:
    """Validate cron expression has 5 or 6 space-separated parts."""
    if not cron or not cron.strip():
        raise ValueError("cron expression cannot be empty")
    if not _CRON_PARTS.match(cron.strip()):
        raise ValueError(
            "cron must have 5 fields (min hour day month dow) or 6 (sec min hour day month dow)"
        )


class HatchetClient:
    """OwlClaw wrapper around the Hatchet SDK."""

    def __init__(self, config: HatchetConfig) -> None:
        self.config = config
        self._hatchet: Any = None
        self._workflows: dict[str, Any] = {}

    def connect(self) -> None:
        """Connect to Hatchet Server."""
        token = self.config.api_token or os.environ.get("HATCHET_API_TOKEN", "")
        if not token:
            raise ValueError(
                "Hatchet API token required: set api_token in config or HATCHET_API_TOKEN"
            )
        hatchet_cls, client_config_cls, client_tls_config_cls = _get_hatchet()
        try:
            grpc_host_port = self.config.grpc_host_port or _server_url_to_host_port(self.config.server_url)
            client_config = client_config_cls(
                host_port=grpc_host_port,
                server_url=self.config.server_url,
                token=token,
                namespace=self.config.namespace,
                tls_config=client_tls_config_cls(strategy=self.config.grpc_tls_strategy),
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(hatchet_cls, config=client_config)
                try:
                    self._hatchet = future.result(timeout=self.config.connect_timeout_seconds)
                except FutureTimeoutError as exc:
                    future.cancel()
                    raise TimeoutError(
                        f"Timed out connecting to Hatchet after {self.config.connect_timeout_seconds:.1f}s"
                    ) from exc
            logger.info("Connected to Hatchet at %s", self.config.server_url)
        except Exception as e:
            logger.exception("Failed to connect to Hatchet")
            raise ConnectionError(f"Failed to connect to Hatchet Server: {e}") from e

    def disconnect(self) -> None:
        """Disconnect from Hatchet Server."""
        if self._hatchet is not None:
            self._hatchet = None
            logger.info("Disconnected from Hatchet Server")

    def task(
        self,
        name: str | None = None,
        cron: str | None = None,
        retries: int = 3,
        timeout: int | None = None,
        priority: int = 1,
    ) -> Callable:
        """Decorator to register a function as a Hatchet task (standalone workflow)."""

        def decorator(func: Callable) -> Callable:
            if self._hatchet is None:
                raise RuntimeError("Must call connect() before registering tasks")
            if cron is not None:
                _validate_cron(cron)
            task_name = str(name or getattr(func, "__name__", "anonymous")).strip() or "anonymous"
            on_crons = [cron] if cron else None
            from datetime import timedelta
            exec_timeout = timedelta(seconds=timeout) if timeout else timedelta(seconds=60)
            standalone = self._hatchet.task(
                name=task_name,
                on_crons=on_crons,
                retries=retries,
                execution_timeout=exec_timeout,
                default_priority=priority,
            )(func)
            self._workflows[task_name] = standalone
            return func

        return decorator

    def durable_task(
        self,
        name: str | None = None,
        cron: str | None = None,
        retries: int = 3,
        timeout: int | None = None,
        priority: int = 1,
    ) -> Callable:
        """Decorator to register a function as a Hatchet durable task (supports ctx.aio_sleep_for)."""

        def decorator(func: Callable) -> Callable:
            if self._hatchet is None:
                raise RuntimeError("Must call connect() before registering tasks")
            if cron is not None:
                _validate_cron(cron)
            task_name = str(name or getattr(func, "__name__", "anonymous")).strip() or "anonymous"
            on_crons = [cron] if cron else None
            exec_timeout = timedelta(seconds=timeout) if timeout else timedelta(seconds=60)
            standalone = self._hatchet.durable_task(
                name=task_name,
                on_crons=on_crons,
                retries=retries,
                execution_timeout=exec_timeout,
                default_priority=priority,
            )(func)
            self._workflows[task_name] = standalone
            return func

        return decorator

    async def run_task_now(self, task_name: str, **kwargs: Any) -> str:
        """Trigger an immediate run of the task. Returns workflow run id."""
        standalone = self._workflows.get(task_name)
        if standalone is None:
            raise ValueError(f"Task '{task_name}' not registered")
        wf = standalone._workflow
        try:
            result = await wf.aio_run(input=kwargs)
            return getattr(result, "workflow_run_id", getattr(result, "id", "")) or ""
        except Exception:
            logger.exception("Failed to run task %s", task_name)
            raise

    async def schedule_task(
        self,
        task_name: str,
        delay_seconds: int,
        **kwargs: Any,
    ) -> str:
        """Schedule a task to run after delay_seconds. Returns workflow run id if available."""
        if delay_seconds <= 0:
            raise ValueError("delay_seconds must be positive")
        standalone = self._workflows.get(task_name)
        if standalone is None:
            raise ValueError(f"Task '{task_name}' not registered")
        run_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        input_data = kwargs
        try:
            wf = standalone._workflow
            result = await wf.aio_schedule(
                run_at=run_at,
                input=input_data,
            )
            return getattr(result, "workflow_run_id", "") or f"scheduled-{task_name}"
        except Exception:
            logger.exception("Failed to schedule task %s", task_name)
            raise

    async def schedule_cron(
        self,
        workflow_name: str,
        cron_name: str,
        expression: str,
        input_data: dict[str, Any],
        *,
        additional_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a cron trigger for a workflow. Returns cron trigger id."""
        if self._hatchet is None:
            raise RuntimeError("Not connected to Hatchet")
        if workflow_name not in self._workflows:
            raise ValueError(f"Workflow '{workflow_name}' not registered")
        try:
            cron_result = await self._hatchet.cron.aio.create(
                workflow_name=workflow_name,
                cron_name=cron_name,
                expression=expression,
                input=input_data,  # Hatchet API param is 'input'
                additional_metadata=additional_metadata or {},
            )
            return getattr(cron_result, "id", getattr(cron_result, "cron_id", cron_name)) or cron_name
        except Exception as e:
            logger.exception("Failed to create cron trigger for %s: %s", workflow_name, e)
            raise

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a scheduled or running task by run id."""
        if self._hatchet is None:
            return False
        try:
            await self._hatchet.runs.aio_cancel(task_id)
            return True
        except Exception as e:
            logger.warning("Failed to cancel task %s: %s", task_id, e)
            return False

    async def cancel_cron(self, cron_id: str) -> bool:
        """Delete a cron trigger by id."""
        if self._hatchet is None:
            return False
        try:
            await self._hatchet.cron.aio.delete(cron_id=cron_id)
            return True
        except Exception as e:
            logger.warning("Failed to delete cron trigger %s: %s", cron_id, e)
            return False

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        """Get status of a workflow run."""
        if self._hatchet is None:
            raise RuntimeError("Not connected to Hatchet")
        status = await self._hatchet.runs.aio_get_status(task_id)
        status_str = str(status.value) if hasattr(status, "value") else str(status)
        return {
            "id": task_id,
            "status": status_str,
        }

    async def list_scheduled_tasks(self) -> list[dict[str, Any]]:
        """List pending (queued) workflow runs."""
        if self._hatchet is None:
            return []
        try:
            from hatchet_sdk.clients.rest.models.v1_task_status import V1TaskStatus
            runs = await self._hatchet.runs.aio_list(
                statuses=[V1TaskStatus.QUEUED],
            )
            rows = runs if isinstance(runs, list) else (getattr(runs, "rows", None) or [])
            out = []
            for r in rows:
                meta = getattr(r, "metadata", r)
                out.append({
                    "id": getattr(meta, "id", getattr(r, "id", "")),
                    "workflow": getattr(r, "workflow_id", getattr(r, "workflow_name", "")),
                    "scheduled_at": getattr(r, "created_at", None),
                })
            return out
        except Exception as e:
            logger.warning("Failed to list scheduled tasks: %s", e)
            return []

    def start_worker(self) -> None:
        """Start the Hatchet worker (blocking).

        On Windows, SIGQUIT is not defined; the Hatchet SDK expects it for
        graceful shutdown. We set signal.SIGQUIT = signal.SIGTERM only here,
        immediately before starting the worker, so the process behaves as a
        Hatchet worker and is not used as a library in the same process with
        other code that might rely on SIGQUIT being absent. This is the only
        place in OwlClaw that mutates the signal module.
        """
        if self._hatchet is None:
            raise RuntimeError("Must call connect() before start_worker()")
        if not hasattr(signal, "SIGQUIT"):
            # Scoped to this process: Hatchet worker expects SIGQUIT for shutdown.
            signal.SIGQUIT = signal.SIGTERM  # type: ignore[attr-defined,misc]
        worker_name = self.config.worker_name or f"owlclaw-worker-{os.getpid()}"
        workflows = list(self._workflows.values())
        if not workflows:
            raise RuntimeError("No tasks registered; register at least one with @client.task()")
        worker = self._hatchet.worker(
            name=worker_name,
            slots=self.config.max_concurrent_tasks,
            workflows=workflows,
        )
        worker.start()
