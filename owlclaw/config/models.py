"""Configuration models for OwlClaw."""

from __future__ import annotations

from types import ModuleType
from typing import Any, Literal

from pydantic import BaseModel, Field

_pydantic_settings: ModuleType | None
try:
    import pydantic_settings as _pydantic_settings
except ImportError:  # pragma: no cover - compatibility fallback
    _pydantic_settings = None

if _pydantic_settings is None:
    _RuntimeBaseSettings: Any = BaseModel
else:
    _RuntimeBaseSettings = _pydantic_settings.BaseSettings


class AgentConfig(BaseModel):
    """Agent runtime configuration."""

    soul: str = Field(default="docs/SOUL.md", description="Path to SOUL.md file.")
    identity: str = Field(default="docs/IDENTITY.md", description="Path to IDENTITY.md file.")
    heartbeat_interval_minutes: int = Field(default=30, ge=1, le=1440)
    max_iterations: int = Field(default=10, ge=1, le=100)


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker configuration."""

    failure_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    window_size: int = Field(default=10, ge=1)


class RateLimitConfig(BaseModel):
    """Rate limit configuration."""

    max_calls_per_minute: int = Field(default=60, ge=1)


class GovernanceConfig(BaseModel):
    """Governance configuration."""

    monthly_budget: float = Field(default=500.0, ge=0.0)
    budget_alert_thresholds: list[float] = Field(default_factory=lambda: [0.5, 0.8, 1.0])
    fail_policy: Literal["open", "close"] = Field(
        default="close",
        description="'close' is production default; use 'open' only for dev/test debugging.",
    )
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


class CronTriggersConfig(BaseModel):
    """Cron trigger runtime configuration."""

    max_concurrent: int = Field(default=10, ge=1)
    default_timeout_seconds: int = Field(default=300, ge=1)
    enabled: bool = Field(default=True)


class CronGovernanceDefaultsConfig(BaseModel):
    """Default governance values for cron triggers."""

    max_daily_runs: int = Field(default=24, ge=1)
    max_daily_cost: float = Field(default=10.0, ge=0.0)
    cooldown_seconds: int = Field(default=0, ge=0)


class CronRetryConfig(BaseModel):
    """Retry defaults for cron execution."""

    retry_on_failure: bool = Field(default=True)
    max_retries: int = Field(default=3, ge=0)
    retry_delay_seconds: int = Field(default=60, ge=0)


class CronNotificationConfig(BaseModel):
    """Notification defaults for cron execution."""

    enabled: bool = Field(default=False)
    channels: list[str] = Field(default_factory=list)


class TriggersConfig(BaseModel):
    """Trigger subsystem configuration."""

    cron: CronTriggersConfig = Field(default_factory=CronTriggersConfig)
    governance: CronGovernanceDefaultsConfig = Field(default_factory=CronGovernanceDefaultsConfig)
    retry: CronRetryConfig = Field(default_factory=CronRetryConfig)
    notifications: CronNotificationConfig = Field(default_factory=CronNotificationConfig)


class LLMIntegrationConfig(BaseModel):
    """LLM integration defaults."""

    model: str = Field(default="gpt-4o-mini")
    fallback_models: list[str] = Field(default_factory=list)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    mock_mode: bool = Field(default=False)
    mock_responses: dict[str, Any] = Field(default_factory=dict)


class HatchetIntegrationConfig(BaseModel):
    """Hatchet integration configuration."""

    server_url: str = Field(default="")
    api_token: str = Field(default="")
    namespace: str = Field(default="default")


class LangfuseIntegrationConfig(BaseModel):
    """Langfuse integration configuration."""

    enabled: bool = Field(default=False)
    host: str = Field(default="https://cloud.langfuse.com")


class IntegrationsConfig(BaseModel):
    """External integrations configuration."""

    llm: LLMIntegrationConfig = Field(default_factory=LLMIntegrationConfig)
    hatchet: HatchetIntegrationConfig = Field(default_factory=HatchetIntegrationConfig)
    langfuse: LangfuseIntegrationConfig = Field(default_factory=LangfuseIntegrationConfig)


class SanitizerConfig(BaseModel):
    """Input sanitization configuration."""

    enabled: bool = Field(default=True)
    custom_rules: list[dict[str, Any]] = Field(default_factory=list)


class RiskGateConfig(BaseModel):
    """Risk gate configuration."""

    enabled: bool = Field(default=True)
    confirmation_timeout_seconds: int = Field(default=300, ge=1)
    default_risk_level: str = Field(default="low")


class DataMaskerConfig(BaseModel):
    """Data masking configuration."""

    enabled: bool = Field(default=True)
    rules: list[dict[str, Any]] = Field(default_factory=list)


class SecurityConfig(BaseModel):
    """Security subsystem configuration."""

    sanitizer: SanitizerConfig = Field(default_factory=SanitizerConfig)
    risk_gate: RiskGateConfig = Field(default_factory=RiskGateConfig)
    data_masker: DataMaskerConfig = Field(default_factory=DataMaskerConfig)


class MemoryConfig(BaseModel):
    """Memory subsystem configuration."""

    vector_backend: str = Field(default="pgvector")
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dimensions: int = Field(default=1536, ge=1)
    max_entries: int = Field(default=10000, ge=1)
    retention_days: int = Field(default=365, ge=1)


class OwlClawConfig(_RuntimeBaseSettings):
    """Root configuration model for OwlClaw."""

    agent: AgentConfig = Field(default_factory=AgentConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    triggers: TriggersConfig = Field(default_factory=TriggersConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

    model_config: Any = (
        {
            "env_prefix": "OWLCLAW_",
            "env_nested_delimiter": "__",
            "extra": "ignore",
        }
        if _pydantic_settings is None
        else _pydantic_settings.SettingsConfigDict(
            env_prefix="OWLCLAW_",
            env_nested_delimiter="__",
            extra="ignore",
        )
    )

