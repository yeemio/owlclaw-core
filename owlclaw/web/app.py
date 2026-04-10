"""Console app assembly entrypoint."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from owlclaw.web.api import create_api_app
from owlclaw.web.api.deps import clear_providers, set_providers
from owlclaw.web.providers import create_default_provider_bundle


def create_console_app(*, providers: dict[str, Any] | None = None) -> FastAPI:
    """Create FastAPI app and wire provider registry."""
    provider_bundle = providers or create_default_provider_bundle()
    clear_providers()
    set_providers(**provider_bundle)
    return create_api_app()

