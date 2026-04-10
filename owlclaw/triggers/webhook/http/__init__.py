"""HTTP gateway for webhook trigger."""

from owlclaw.triggers.webhook.http.app import HttpGatewayConfig, create_webhook_app

__all__ = ["HttpGatewayConfig", "create_webhook_app"]
