"""Short-term memory manager — per-Run context (fixed zone + sliding window)."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Keep last N full rounds in sliding zone before compressing older ones
_SLIDING_KEEP_ROUNDS = 3
_CHARS_PER_TOKEN = 4


def _count_tokens(text: str) -> int:
    """Token count via tiktoken if available, else ~4 chars per token."""
    if not text:
        return 0
    try:
        import tiktoken  # noqa: PLC0415
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


class STMManager:
    """Per-Run STM: fixed zone (trigger + focus + injected) + sliding zone (function call history)."""

    def __init__(self, max_tokens: int = 2000) -> None:
        self._max_tokens = max_tokens
        self._fixed_zone: dict[str, Any] = {}
        self._sliding_zone: list[dict[str, Any]] = []  # each: {"call": {name, args, result}, "response": str}
        self._injected: list[str] = []
        self._token_count = 0

    def add_trigger(self, trigger_type: str, payload: dict, focus: str | None) -> None:
        """Add trigger event and optional focus to fixed zone."""
        old = self._fixed_zone.get("trigger_text", "")
        self._fixed_zone["trigger_type"] = trigger_type
        self._fixed_zone["trigger_payload"] = payload
        self._fixed_zone["focus"] = focus or ""
        trigger_text = f"Trigger: {trigger_type}\nFocus: {focus or '(none)'}\nPayload: {json.dumps(payload, default=str)[:500]}"
        self._fixed_zone["trigger_text"] = trigger_text
        self._token_count -= _count_tokens(old)
        self._token_count += _count_tokens(trigger_text)
        self._compress_if_needed()

    def add_function_call(self, name: str, args: dict, result: dict) -> None:
        """Append one function call round to sliding zone (call only; response can follow with add_llm_response)."""
        self._sliding_zone.append({
            "call": {"name": name, "args": args, "result": result},
            "response": "",
        })
        line = f"Call: {name} | args: {json.dumps(args, default=str)[:200]} | result: {json.dumps(result, default=str)[:300]}"
        self._token_count += _count_tokens(line)
        self._compress_if_needed()

    def add_llm_response(self, content: str) -> None:
        """Append LLM response to the last round in sliding zone."""
        if not self._sliding_zone:
            self._sliding_zone.append({"call": {"name": "", "args": {}, "result": {}}, "response": content})
        else:
            self._sliding_zone[-1]["response"] = content
        self._token_count += _count_tokens(content)
        self._compress_if_needed()

    def inject(self, instruction: str) -> None:
        """Inject a temporary instruction (e.g. from Signal)."""
        self._injected.append(instruction)
        self._token_count += _count_tokens(instruction)
        self._compress_if_needed()

    def _recompute_token_count(self) -> None:
        """Recompute _token_count from current fixed zone, injected, and sliding zone."""
        self._token_count = 0
        self._token_count += _count_tokens(self._fixed_zone.get("trigger_text", ""))
        for inst in self._injected:
            self._token_count += _count_tokens(inst)
        for round_ in self._sliding_zone:
            call = round_.get("call", {})
            resp = round_.get("response", "")
            self._token_count += _count_tokens(json.dumps(call, default=str))
            self._token_count += _count_tokens(resp)

    def _compress_if_needed(self) -> None:
        """When over max_tokens, compress sliding zone: keep last 3 rounds full, summarize/truncate the rest."""
        if self._token_count <= self._max_tokens:
            return
        if len(self._sliding_zone) <= _SLIDING_KEEP_ROUNDS:
            return
        to_compress = self._sliding_zone[:-_SLIDING_KEEP_ROUNDS]
        summary = f"[{len(to_compress)} earlier rounds summarized]"
        self._sliding_zone = [{"call": {"name": "_summary", "args": {}, "result": {"text": summary}}, "response": ""}] + self._sliding_zone[-_SLIDING_KEEP_ROUNDS:]
        self._recompute_token_count()

    def to_prompt_section(self) -> str:
        """Render STM as structured Markdown for system prompt."""
        parts = ["## Short-term context\n"]
        if self._fixed_zone.get("trigger_text"):
            parts.append("### Trigger\n")
            parts.append(self._fixed_zone["trigger_text"])
            parts.append("\n\n")
        if self._injected:
            parts.append("### Injected instructions\n")
            for inst in self._injected:
                parts.append(f"- {inst}\n")
            parts.append("\n")
        if self._sliding_zone:
            parts.append("### Recent turns\n")
            for round_ in self._sliding_zone:
                call = round_.get("call", {})
                resp = round_.get("response", "")
                name = call.get("name", "")
                if name == "_summary":
                    parts.append(f"- {call.get('result', {}).get('text', '')}\n")
                else:
                    parts.append(f"- **{name}**: args `{json.dumps(call.get('args', {}), default=str)[:150]}` → result `{json.dumps(call.get('result', {}), default=str)[:200]}`\n")
                if resp:
                    parts.append(f"  LLM: {resp[:300]}{'...' if len(resp) > 300 else ''}\n")
            parts.append("\n")
        out = "".join(parts).strip()
        if not out or out == "## Short-term context":
            return "## Short-term context\n(empty)"
        return out
