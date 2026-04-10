"""Message parsers for queue-trigger payloads."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any


class ParseError(ValueError):
    """Raised when a queue message cannot be parsed with the selected parser."""


class MessageParser(ABC):
    """Base parser contract for queue message bodies."""

    @abstractmethod
    def parse(self, body: bytes) -> dict[str, Any] | str | bytes:
        """Parse raw bytes into typed payload."""


class JSONParser(MessageParser):
    """Parse UTF-8 JSON payloads into dictionaries."""

    def parse(self, body: bytes) -> dict[str, Any]:
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ParseError(f"Failed to parse JSON payload: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ParseError("JSON payload must decode to an object")
        return parsed


class TextParser(MessageParser):
    """Parse UTF-8 text payloads into strings."""

    def parse(self, body: bytes) -> str:
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ParseError(f"Failed to parse text payload: {exc}") from exc


class BinaryParser(MessageParser):
    """Return raw bytes payload."""

    def parse(self, body: bytes) -> bytes:
        return body
