"""Capability semantic matcher for natural-language skill parsing."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from owlclaw.agent.memory.embedder_tfidf import TFIDFEmbedder
from owlclaw.integrations.llm import acompletion


_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")
_SEMANTIC_ALIASES: dict[str, set[str]] = {
    "inventory": {"inventory", "stock", "库存"},
    "email": {"email", "mail", "邮件"},
    "notify": {"notify", "notification", "alert", "提醒", "通知"},
    "order": {"order", "订单"},
    "report": {"report", "报表", "报告"},
    "check": {"check", "scan", "monitor", "检查", "巡检"},
    "send": {"send", "push", "发送"},
}


def parse_available_tools(raw: str | None = None) -> list[str]:
    """Parse available tools from env-style comma list."""
    source = raw if raw is not None else os.environ.get("OWLCLAW_AVAILABLE_TOOLS", "")
    tools = [item.strip() for item in source.split(",") if item.strip()]
    return sorted(dict.fromkeys(tools))


def extract_tool_intents(*, frontmatter: dict[str, Any], body: str) -> list[str]:
    """Extract potential tool intents from SKILL.md content."""
    intents: list[str] = []
    description = frontmatter.get("description")
    if isinstance(description, str) and description.strip():
        intents.append(description.strip())
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        intents.append(stripped)
    return intents


@dataclass(frozen=True)
class ToolMatch:
    intent: str
    tool_name: str
    score: float
    method: str


class CapabilityMatcher:
    """Resolve tool intents to available capabilities."""

    def __init__(
        self,
        *,
        embedding_threshold: float = 0.8,
        enable_llm_confirmation: bool | None = None,
    ) -> None:
        self.embedding_threshold = embedding_threshold
        if enable_llm_confirmation is None:
            enable_llm_confirmation = os.environ.get("OWLCLAW_TOOL_MATCH_LLM_CONFIRM", "").strip() == "1"
        self.enable_llm_confirmation = bool(enable_llm_confirmation)
        self._embedder = TFIDFEmbedder(dimensions=64)

    def resolve(self, *, tool_intents: list[str], available_tools: list[str]) -> list[ToolMatch]:
        """Resolve tool intents by exact match, embedding similarity, then optional LLM confirmation."""
        normalized_tools = sorted(dict.fromkeys(tool for tool in available_tools if isinstance(tool, str) and tool.strip()))
        if not normalized_tools:
            return []
        matches: list[ToolMatch] = []
        for intent in tool_intents:
            if not isinstance(intent, str) or not intent.strip():
                continue
            intent_text = intent.strip()
            exact = self._exact_match(intent_text, normalized_tools)
            if exact is not None:
                matches.append(ToolMatch(intent=intent_text, tool_name=exact, score=1.0, method="exact"))
                continue
            candidate, score = self._embedding_best_match(intent_text, normalized_tools)
            if candidate is None or score < self.embedding_threshold:
                continue
            selected = candidate
            method = "embedding"
            if self.enable_llm_confirmation:
                confirmed = self._confirm_with_llm(intent_text, candidate, score, normalized_tools)
                if confirmed is None:
                    continue
                selected = confirmed
                method = "llm_function_call"
            matches.append(ToolMatch(intent=intent_text, tool_name=selected, score=score, method=method))
        deduped: dict[str, ToolMatch] = {}
        for item in sorted(matches, key=lambda m: m.score, reverse=True):
            deduped.setdefault(item.tool_name, item)
        return list(deduped.values())

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"[\s_-]+", " ", text.strip().lower())

    def _exact_match(self, intent: str, tools: list[str]) -> str | None:
        normalized_intent = self._normalize_text(intent)
        intent_tokens = self._canonical_tokens(intent)
        if not normalized_intent:
            return None
        token_best: tuple[str | None, int] = (None, 0)
        for tool in tools:
            normalized_tool = self._normalize_text(tool)
            tool_tokens = self._canonical_tokens(tool)
            if normalized_intent == normalized_tool:
                return tool
            if normalized_tool and normalized_tool in normalized_intent:
                return tool
            overlap = len(intent_tokens & tool_tokens) if intent_tokens and tool_tokens else 0
            if overlap > token_best[1]:
                token_best = (tool, overlap)
        if token_best[0] is not None and token_best[1] >= 2:
            return token_best[0]
        return None

    def _embedding_best_match(self, intent: str, tools: list[str]) -> tuple[str | None, float]:
        vectors = self._embed_texts([intent] + tools)
        if len(vectors) != len(tools) + 1:
            return None, 0.0
        intent_vec = vectors[0]
        best_tool: str | None = None
        best_score = 0.0
        for idx, tool in enumerate(tools, start=1):
            score = self._cosine_similarity(intent_vec, vectors[idx])
            if score > best_score:
                best_tool = tool
                best_score = score
        return best_tool, best_score

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        async def _run() -> list[list[float]]:
            return await self._embedder.embed_batch(texts)

        try:
            return asyncio.run(_run())
        except RuntimeError:
            return [self._token_vector(text) for text in texts]

    @staticmethod
    def _token_vector(text: str, *, size: int = 64) -> list[float]:
        vector = [0.0] * size
        for token in _TOKEN_PATTERN.findall(text.lower()):
            index = hash(token) % size
            vector[index] += 1.0
        return vector

    @staticmethod
    def _cosine_similarity(lhs: list[float], rhs: list[float]) -> float:
        if not lhs or not rhs or len(lhs) != len(rhs):
            return 0.0
        dot = sum(a * b for a, b in zip(lhs, rhs, strict=True))
        lhs_norm = sum(a * a for a in lhs) ** 0.5
        rhs_norm = sum(b * b for b in rhs) ** 0.5
        if lhs_norm == 0.0 or rhs_norm == 0.0:
            return 0.0
        return float(max(0.0, min(1.0, dot / (lhs_norm * rhs_norm))))

    @staticmethod
    def _canonical_tokens(text: str) -> set[str]:
        lowered = text.lower()
        raw_tokens = {token.lower() for token in _TOKEN_PATTERN.findall(lowered)}
        expanded: set[str] = set(raw_tokens)
        for canonical, variants in _SEMANTIC_ALIASES.items():
            if expanded & variants or any(variant in lowered for variant in variants):
                expanded.add(canonical)
                expanded.update(variants)
        return expanded

    def _confirm_with_llm(self, intent: str, candidate: str, score: float, tools: list[str]) -> str | None:
        async def _run() -> str | None:
            tool_schema = [
                {
                    "type": "function",
                    "function": {
                        "name": "confirm_tool_match",
                        "description": "Select the best tool name from provided candidates for a natural language intent.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "tool": {"type": "string"},
                            },
                            "required": ["tool"],
                        },
                    },
                }
            ]
            response = await acompletion(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "Choose the best tool from candidates. Return none when no confident match.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"intent": intent, "candidate": candidate, "score": score, "available_tools": tools},
                            ensure_ascii=False,
                        ),
                    },
                ],
                tools=tool_schema,
                tool_choice="auto",
                max_tokens=64,
                temperature=0.0,
            )
            choice = response.choices[0]
            message = choice.message
            tool_calls = getattr(message, "tool_calls", None) or []
            for call in tool_calls:
                function = getattr(call, "function", None)
                if getattr(function, "name", "") != "confirm_tool_match":
                    continue
                raw_arguments = getattr(function, "arguments", "{}")
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                if not isinstance(arguments, dict):
                    return None
                selected = arguments.get("tool")
                if isinstance(selected, str) and selected in tools:
                    return selected
            return candidate if candidate in tools else None

        try:
            return asyncio.run(_run())
        except Exception:
            return candidate if candidate in tools else None
