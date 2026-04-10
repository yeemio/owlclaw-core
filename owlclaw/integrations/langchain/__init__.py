"""LangChain integration package (optional dependency boundary)."""

from owlclaw.integrations.langchain.adapter import LangChainAdapter, RunnableConfig
from owlclaw.integrations.langchain.config import LangChainConfig, PrivacyConfig, TracingConfig
from owlclaw.integrations.langchain.errors import ErrorHandler
from owlclaw.integrations.langchain.metrics import MetricsCollector
from owlclaw.integrations.langchain.privacy import PrivacyMasker
from owlclaw.integrations.langchain.retry import RetryPolicy, calculate_backoff_delay, should_retry
from owlclaw.integrations.langchain.schema import SchemaBridge, SchemaValidationError
from owlclaw.integrations.langchain.trace import TraceManager, TraceSpan
from owlclaw.integrations.langchain.version import check_langchain_version

__all__ = [
    "ErrorHandler",
    "LangChainAdapter",
    "LangChainConfig",
    "MetricsCollector",
    "PrivacyConfig",
    "PrivacyMasker",
    "RetryPolicy",
    "RunnableConfig",
    "SchemaBridge",
    "SchemaValidationError",
    "TraceManager",
    "TraceSpan",
    "TracingConfig",
    "calculate_backoff_delay",
    "check_langchain_version",
    "should_retry",
]
