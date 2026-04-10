"""Extract SKILL.md drafts from business documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from owlclaw.capabilities.capability_matcher import CapabilityMatcher, extract_tool_intents, parse_available_tools
from owlclaw.capabilities.skill_creator import SkillConversationState, SkillCreatorAgent
from owlclaw.capabilities.trigger_resolver import resolve_trigger_intent


@dataclass(frozen=True)
class SkillDraft:
    """Intermediate skill draft extracted from one document section."""

    name: str
    description: str
    trigger_intent: str
    business_rules: list[str]
    resolved_tools: list[str]


class SkillDocExtractor:
    """Generate one or more SKILL.md files from markdown/text SOP documents."""

    SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}

    def __init__(self, *, available_tools: list[str] | None = None) -> None:
        self.available_tools = available_tools if available_tools is not None else parse_available_tools()
        self.matcher = CapabilityMatcher(enable_llm_confirmation=False)

    def read_document(self, path: Path | str) -> str:
        target = Path(path).expanduser()
        if target.suffix.lower() not in self.SUPPORTED_SUFFIXES:
            raise ValueError("only markdown/text documents are supported (.md/.markdown/.txt)")
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"document not found: {target}")
        return target.read_text(encoding="utf-8").lstrip("\ufeff")

    def extract_from_text(self, text: str) -> list[SkillDraft]:
        sections = self._split_sections(text)
        drafts: list[SkillDraft] = []
        for title, lines in sections:
            if not lines:
                continue
            body = "\n".join(lines).strip()
            if not body:
                continue
            first_line = next((line.strip() for line in lines if line.strip()), title or "generated skill")
            trigger_line = self._find_trigger_line(lines)
            if not trigger_line:
                trigger_line = "每天 0 点执行"
            name = self._to_kebab_case(title or first_line)
            if not name:
                continue
            intent_lines = extract_tool_intents(frontmatter={"description": first_line}, body=body)
            matches = self.matcher.resolve(tool_intents=intent_lines, available_tools=self.available_tools)
            drafts.append(
                SkillDraft(
                    name=name,
                    description=first_line,
                    trigger_intent=trigger_line,
                    business_rules=[line.strip() for line in lines if line.strip()],
                    resolved_tools=sorted({m.tool_name for m in matches}),
                )
            )
        return drafts

    def generate_markdown(self, draft: SkillDraft) -> str:
        state = SkillConversationState(core_intent=draft.description, trigger_intent=draft.trigger_intent, special_rules=draft.business_rules)
        agent = SkillCreatorAgent()
        rendered = agent.generate_skill_markdown(state)
        # Keep generated name deterministic from extracted section title.
        rendered = re.sub(r"^name:\s*.*$", f"name: {draft.name}", rendered, flags=re.MULTILINE)
        if draft.resolved_tools:
            tools_block = ["", "## Suggested Tools"]
            tools_block.extend(f"- `{tool}`" for tool in draft.resolved_tools)
            rendered = rendered.rstrip() + "\n" + "\n".join(tools_block) + "\n"
        resolved = resolve_trigger_intent(draft.trigger_intent)
        if resolved.confidence < 0.6:
            rendered = rendered.rstrip() + "\n\n> NOTE: trigger expression confidence is low, please review wording.\n"
        return rendered

    def generate_from_document(self, source: Path | str, output_dir: Path | str) -> list[Path]:
        text = self.read_document(source)
        drafts = self.extract_from_text(text)
        out_root = Path(output_dir).expanduser()
        out_root.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for draft in drafts:
            target = out_root / draft.name / "SKILL.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self.generate_markdown(draft), encoding="utf-8")
            written.append(target)
        return written

    @staticmethod
    def _split_sections(text: str) -> list[tuple[str, list[str]]]:
        sections: list[tuple[str, list[str]]] = []
        current_title = ""
        current_lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            heading = re.match(r"^#{1,3}\s+(.+)$", line)
            if heading:
                if current_lines:
                    sections.append((current_title, current_lines))
                current_title = heading.group(1).strip()
                current_lines = []
                continue
            current_lines.append(line)
        if current_lines:
            sections.append((current_title, current_lines))
        if not sections:
            return [("generated-skill", [text])]
        return sections

    @staticmethod
    def _find_trigger_line(lines: list[str]) -> str:
        hints = ("每天", "每周", "每月", "当", "daily", "weekly", "monthly", "every", "when", "on monday")
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            normalized = stripped.lower()
            if any(h in stripped or h in normalized for h in hints):
                return stripped
        return ""

    @staticmethod
    def _to_kebab_case(text: str) -> str:
        cleaned = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE).strip().lower()
        cleaned = re.sub(r"[_\s]+", "-", cleaned)
        cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
        return cleaned or "generated-skill"
