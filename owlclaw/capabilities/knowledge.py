"""Knowledge injection for Agent prompts.

This module implements the Knowledge Injector component, which formats
Skills knowledge and injects it into Agent system prompts.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from owlclaw.capabilities.skills import Skill, SkillsLoader
from owlclaw.security.sanitizer import InputSanitizer


@dataclass(frozen=True)
class SkillsKnowledgeReport:
    """Structured report for injected skills knowledge token impact."""

    content: str
    selected_skill_names: list[str]
    dropped_skill_names: list[str]
    per_skill_tokens: dict[str, int]
    total_tokens: int


class KnowledgeInjector:
    """Formats and injects Skills knowledge into Agent prompts."""

    def __init__(self, skills_loader: SkillsLoader, *, token_limit: int = 4000):
        """Initialize the KnowledgeInjector."""
        if not isinstance(token_limit, int) or token_limit < 1:
            raise ValueError("token_limit must be a positive integer")
        self.skills_loader = skills_loader
        self.token_limit = token_limit
        self._sanitizer = InputSanitizer()

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token usage with a lightweight word-count heuristic."""
        if not text or not text.strip():
            return 0
        return len(text.split())

    @staticmethod
    def _matches_focus(skill: Skill, focus: str | None) -> bool:
        """Match focus with owlclaw.focus first, then metadata.tags fallback."""
        if not focus:
            return True
        target = focus.strip().lower()
        if not target:
            return True

        declared_focus = {item.strip().lower() for item in skill.focus if item.strip()}
        if declared_focus:
            return target in declared_focus

        tags = skill.metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        if isinstance(tags, list):
            normalized_tags = {str(tag).strip().lower() for tag in tags if str(tag).strip()}
            return target in normalized_tags
        return False

    def load_skills_metadata(self) -> list[dict[str, Any]]:
        """Load metadata summary for all scanned skills."""
        return [skill.to_dict() for skill in sorted(self.skills_loader.list_skills(), key=lambda s: s.name)]

    def select_skills(
        self,
        skill_names: list[str],
        context_filter: Callable[[Skill], bool] | None = None,
        max_tokens: int | None = None,
        *,
        focus: str | None = None,
        token_limit: int | None = None,
    ) -> list[str]:
        """Select relevant skill names with optional focus/filter and token budget."""
        if isinstance(token_limit, int) and token_limit > 0:
            budget = token_limit
        elif isinstance(max_tokens, int) and max_tokens > 0:
            budget = max_tokens
        else:
            budget = self.token_limit

        selected: list[str] = []
        seen: set[str] = set()
        used_tokens = 0

        for raw_name in skill_names:
            if not isinstance(raw_name, str):
                continue
            normalized_name = raw_name.strip().lower()
            if not normalized_name or normalized_name in seen:
                continue
            seen.add(normalized_name)

            skill = self.skills_loader.get_skill(normalized_name)
            if skill is None:
                continue
            if context_filter is not None and not context_filter(skill):
                continue
            if not self._matches_focus(skill, focus):
                continue

            content = skill.load_full_content()
            content_tokens = self._estimate_tokens(content)
            if selected and used_tokens + content_tokens > budget:
                continue
            if not selected and content_tokens > budget:
                selected.append(skill.name)
                break

            selected.append(skill.name)
            used_tokens += content_tokens

        return selected

    def reload_skills(self) -> list[Skill]:
        """Reload skills from disk and clear prior cached full contents."""
        for skill in self.skills_loader.list_skills():
            skill.clear_full_content_cache()
        return self.skills_loader.scan()

    def get_skills_knowledge(
        self,
        skill_names: list[str],
        context_filter: Callable[[Skill], bool] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Retrieve and format Skills knowledge for specified skills."""
        report = self.get_skills_knowledge_report(
            skill_names,
            context_filter=context_filter,
            max_tokens=max_tokens,
        )
        return report.content

    def get_skills_knowledge_report(
        self,
        skill_names: list[str],
        context_filter: Callable[[Skill], bool] | None = None,
        max_tokens: int | None = None,
        *,
        focus: str | None = None,
    ) -> SkillsKnowledgeReport:
        """Retrieve formatted knowledge and token impact report."""
        knowledge_parts: list[str] = []
        selected_names = self.select_skills(
            skill_names,
            context_filter=context_filter,
            max_tokens=max_tokens,
            focus=focus,
        )
        selected_set = set(selected_names)
        dropped_names: list[str] = []
        seen: set[str] = set()
        for raw_name in skill_names:
            if not isinstance(raw_name, str):
                continue
            normalized = raw_name.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            skill = self.skills_loader.get_skill(normalized)
            if skill is None:
                continue
            if skill.name not in selected_set:
                dropped_names.append(skill.name)

        per_skill_tokens: dict[str, int] = {}
        for selected_name in selected_names:
            skill = self.skills_loader.get_skill(selected_name)
            if skill is None:
                continue
            full_content = skill.load_full_content()
            sanitized_content = self._sanitizer.sanitize(
                full_content,
                source=f"skill:{skill.name}",
            ).sanitized
            per_skill_tokens[skill.name] = self._estimate_tokens(sanitized_content)
            knowledge_parts.append(
                f"## Skill: {skill.name}\n\n"
                f"**Description:** {skill.description}\n\n"
                f"{sanitized_content}\n"
            )

        if not knowledge_parts:
            return SkillsKnowledgeReport(
                content="",
                selected_skill_names=[],
                dropped_skill_names=dropped_names,
                per_skill_tokens={},
                total_tokens=0,
            )

        content = (
            "# Available Skills\n\n"
            "The following Skills describe your capabilities and "
            "when to use them:\n\n"
            + "\n---\n\n".join(knowledge_parts)
        )
        return SkillsKnowledgeReport(
            content=content,
            selected_skill_names=selected_names,
            dropped_skill_names=dropped_names,
            per_skill_tokens=per_skill_tokens,
            total_tokens=self._estimate_tokens(content),
        )

    def get_all_skills_summary(self) -> str:
        """Get a summary of all skills (metadata only, no full content)."""
        skills = self.skills_loader.list_skills()

        if not skills:
            return "No Skills available."

        summary_parts = [
            "# Available Skills Summary\n\n"
            "You have access to the following capabilities:\n"
        ]

        for skill in sorted(skills, key=lambda s: s.name):
            summary_parts.append(f"- **{skill.name}**: {skill.description}")

        return "\n".join(summary_parts)
