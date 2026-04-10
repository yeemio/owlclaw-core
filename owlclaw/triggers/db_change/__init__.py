"""Database change trigger module."""

from owlclaw.triggers.db_change.adapter import DBChangeAdapter, DBChangeEvent, DebeziumAdapter, PostgresNotifyAdapter
from owlclaw.triggers.db_change.aggregator import AggregationMode, EventAggregator
from owlclaw.triggers.db_change.api import DBChangeTriggerRegistration, db_change
from owlclaw.triggers.db_change.config import DBChangeTriggerConfig, DebeziumConfig
from owlclaw.triggers.db_change.manager import DBChangeTriggerManager

__all__ = [
    "AggregationMode",
    "DBChangeAdapter",
    "DBChangeTriggerRegistration",
    "DBChangeEvent",
    "DBChangeTriggerConfig",
    "DBChangeTriggerManager",
    "DebeziumAdapter",
    "DebeziumConfig",
    "EventAggregator",
    "PostgresNotifyAdapter",
    "db_change",
]
