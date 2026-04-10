"""Capabilities module for OwlClaw.

This module provides Skills loading, capability registration, and knowledge
injection for Agent prompts.
"""

from owlclaw.capabilities.knowledge import KnowledgeInjector
from owlclaw.capabilities.registry import CapabilityRegistry
from owlclaw.capabilities.skills import Skill, SkillsLoader, SkillsWatcher

__all__ = [
    "Skill",
    "SkillsLoader",
    "SkillsWatcher",
    "CapabilityRegistry",
    "KnowledgeInjector",
]
