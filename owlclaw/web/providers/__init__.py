"""Default providers for console backend."""

from __future__ import annotations

from typing import Any

from owlclaw.web.providers.agents import DefaultAgentsProvider
from owlclaw.web.providers.capabilities import DefaultCapabilitiesProvider
from owlclaw.web.providers.governance import DefaultGovernanceProvider
from owlclaw.web.providers.ledger import DefaultLedgerProvider
from owlclaw.web.providers.overview import DefaultOverviewProvider
from owlclaw.web.providers.settings import DefaultSettingsProvider
from owlclaw.web.providers.triggers import DefaultTriggersProvider


def create_default_provider_bundle() -> dict[str, Any]:
    """Create default provider set for API bootstrap."""
    return {
        "overview": DefaultOverviewProvider(),
        "governance": DefaultGovernanceProvider(),
        "triggers": DefaultTriggersProvider(),
        "agents": DefaultAgentsProvider(),
        "capabilities": DefaultCapabilitiesProvider(),
        "ledger": DefaultLedgerProvider(),
        "settings": DefaultSettingsProvider(),
    }


__all__ = [
    "DefaultOverviewProvider",
    "DefaultGovernanceProvider",
    "DefaultAgentsProvider",
    "DefaultCapabilitiesProvider",
    "DefaultLedgerProvider",
    "DefaultSettingsProvider",
    "DefaultTriggersProvider",
    "create_default_provider_bundle",
]
