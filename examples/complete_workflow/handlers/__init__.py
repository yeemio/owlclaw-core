"""Handlers for the complete inventory workflow example."""

from examples.complete_workflow.handlers.alert import detect_anomalies
from examples.complete_workflow.handlers.inventory import check_inventory
from examples.complete_workflow.handlers.report import build_daily_report
from examples.complete_workflow.handlers.reorder import decide_reorder

__all__ = [
    "check_inventory",
    "decide_reorder",
    "detect_anomalies",
    "build_daily_report",
]
