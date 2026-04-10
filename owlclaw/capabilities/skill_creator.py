"""Conversational skill creation helper for business users."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SkillConversationState:
    """State collected from multi-turn creation conversation."""

    core_intent: str = ""
    trigger_intent: str = ""
    notification: str = ""
    special_rules: list[str] = field(default_factory=list)
    raw_messages: list[str] = field(default_factory=list)


class SkillCreatorAgent:
    """Simple conversation orchestrator for SKILL.md generation."""

    MAX_ROUNDS = 10

    def __init__(self, available_capabilities: list[str] | None = None):
        self.available_capabilities = sorted({item.strip() for item in (available_capabilities or []) if item.strip()})

    def build_system_prompt(self) -> str:
        capability_block = ", ".join(self.available_capabilities) if self.available_capabilities else "none"
        return (
            "You are OwlClaw Skill Creator. Help users produce SKILL.md with clear intent, trigger, and rules. "
            f"Available capabilities: {capability_block}. Ask concise clarifying questions before generating output."
        )

    def update_state_from_user_input(self, state: SkillConversationState, message: str) -> SkillConversationState:
        text = message.strip()
        if not text:
            return state
        state.raw_messages.append(text)
        lowered = text.lower()

        if not state.core_intent:
            state.core_intent = text
        if any(token in text for token in ("每天", "每周", "每月", "当")) or any(
            token in lowered for token in ("daily", "weekly", "monthly", "every", "when")
        ):
            state.trigger_intent = text
        if any(token in text for token in ("通知", "提醒", "邮件", "短信")) or any(
            token in lowered for token in ("notify", "alert", "email", "slack", "message")
        ):
            state.notification = text
        if text not in state.special_rules:
            state.special_rules.append(text)
        return state

    def missing_fields(self, state: SkillConversationState) -> list[str]:
        missing: list[str] = []
        if not state.core_intent:
            missing.append("core_intent")
        if not state.trigger_intent:
            missing.append("trigger_intent")
        return missing

    def next_question(self, state: SkillConversationState) -> str | None:
        missing = self.missing_fields(state)
        if not missing:
            return None
        if "core_intent" in missing:
            return "请描述你希望 Agent 做什么（核心目标）？"
        if "trigger_intent" in missing:
            return "这个 Skill 什么时候触发？例如“每天早上9点”或“当有新订单时”。"
        return None

    def is_complete(self, state: SkillConversationState) -> bool:
        return not self.missing_fields(state)

    @staticmethod
    def _normalize_name(text: str) -> str:
        asciiish = re.sub(r"[^a-zA-Z0-9\s_-]", " ", text)
        asciiish = re.sub(r"[_\s]+", "-", asciiish.strip().lower())
        asciiish = re.sub(r"-{2,}", "-", asciiish).strip("-")
        return asciiish or "generated-skill"

    def generate_skill_markdown(self, state: SkillConversationState) -> str:
        name = self._normalize_name(state.core_intent)
        description = state.core_intent or "Auto-generated skill"
        lines = [
            "---",
            f"name: {name}",
            f"description: {description}",
            "---",
            "",
            f"# {description}",
            "",
            "## Business Rules",
            f"- Trigger: {state.trigger_intent or '每天 0 点'}",
        ]
        if state.notification:
            lines.append(f"- Notify: {state.notification}")
        extra_rules = [rule for rule in state.special_rules if rule not in {state.core_intent, state.trigger_intent, state.notification}]
        for rule in extra_rules[:8]:
            lines.append(f"- {rule}")
        return "\n".join(lines).strip() + "\n"

    def recommend_capabilities(self, state: SkillConversationState) -> list[str]:
        if not self.available_capabilities:
            return []
        context = " ".join(state.raw_messages).lower()
        matched = [name for name in self.available_capabilities if any(token in context for token in name.lower().split("-"))]
        return matched[:5]
