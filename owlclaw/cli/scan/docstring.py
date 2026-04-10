"""Docstring parsing utilities for cli-scan."""

from __future__ import annotations

import re
import textwrap

from owlclaw.cli.scan.models import DocstringStyle, ParsedDocstring


class DocstringParser:
    """Parse docstrings into a structured representation."""

    _GOOGLE_SECTION_RE = re.compile(r"^(Args|Arguments|Returns|Raises|Examples):\s*$")
    _NUMPY_SECTION_RE = re.compile(r"^(Parameters|Returns|Raises|Examples)\s*$")

    def parse(self, docstring: str | None) -> ParsedDocstring:
        if not docstring:
            return ParsedDocstring()

        style = self.detect_style(docstring)
        parsed = ParsedDocstring(style=style, raw=docstring)

        normalized = textwrap.dedent(docstring).strip("\n")
        if not normalized.strip():
            return parsed

        lines = normalized.splitlines()
        parsed.summary = self._extract_summary(lines)
        parsed.description = self._extract_description(lines)
        parsed.examples = self._extract_examples(lines)

        if style is DocstringStyle.GOOGLE:
            params, returns, raises = self._parse_google(lines)
            parsed.parameters = params
            parsed.returns = returns
            parsed.raises = raises
        elif style is DocstringStyle.NUMPY:
            params, returns, raises = self._parse_numpy(lines)
            parsed.parameters = params
            parsed.returns = returns
            parsed.raises = raises
        elif style is DocstringStyle.RESTRUCTUREDTEXT:
            params, returns, raises = self._parse_restructuredtext(normalized)
            parsed.parameters = params
            parsed.returns = returns
            parsed.raises = raises

        return parsed

    def detect_style(self, docstring: str | None) -> DocstringStyle:
        if not docstring:
            return DocstringStyle.UNKNOWN

        normalized = textwrap.dedent(docstring).strip("\n")
        if re.search(r"(?m)^\s*:param\s+\w+\s*:", normalized) or re.search(r"(?m)^\s*:returns?:\s*", normalized):
            return DocstringStyle.RESTRUCTUREDTEXT
        if re.search(r"(?m)^\s*(Args|Arguments|Returns|Raises):\s*$", normalized):
            return DocstringStyle.GOOGLE
        if re.search(r"(?m)^\s*(Parameters|Returns|Raises)\s*\n\s*-{3,}\s*$", normalized):
            return DocstringStyle.NUMPY
        return DocstringStyle.UNKNOWN

    def _extract_summary(self, lines: list[str]) -> str:
        for line in lines:
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    def _extract_description(self, lines: list[str]) -> str:
        summary_seen = False
        description_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not summary_seen:
                if stripped:
                    summary_seen = True
                continue
            if self._GOOGLE_SECTION_RE.match(stripped) or self._NUMPY_SECTION_RE.match(stripped) or stripped.startswith(":param"):
                break
            description_lines.append(line)
        return "\n".join(description_lines).strip()

    def _extract_examples(self, lines: list[str]) -> list[str]:
        examples: list[str] = []
        fence_buffer: list[str] = []
        in_fence = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                if in_fence:
                    fence_buffer.append(line)
                    examples.append("\n".join(fence_buffer).strip())
                    fence_buffer = []
                    in_fence = False
                else:
                    in_fence = True
                    fence_buffer = [line]
                continue

            if in_fence:
                fence_buffer.append(line)
                continue

            if stripped.startswith(">>>"):
                examples.append(line.rstrip())

        if in_fence and fence_buffer:
            examples.append("\n".join(fence_buffer).strip())
        return examples

    def _parse_google(self, lines: list[str]) -> tuple[dict[str, str], str | None, dict[str, str]]:
        parameters: dict[str, str] = {}
        raises: dict[str, str] = {}
        returns: str | None = None
        section = ""
        current_key: str | None = None

        for raw_line in lines:
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            if self._GOOGLE_SECTION_RE.match(stripped):
                section = stripped[:-1].lower()
                current_key = None
                continue

            if section in {"args", "arguments"}:
                match = re.match(r"^([*]{0,2}[A-Za-z_]\w*)\s*(\([^)]+\))?\s*:\s*(.+)$", stripped)
                if match:
                    current_key = match.group(1)
                    parameters[current_key] = match.group(3).strip()
                    continue
                if current_key and raw_line.startswith(" "):
                    parameters[current_key] = f"{parameters[current_key]} {stripped}".strip()
            elif section == "returns":
                returns = f"{returns} {stripped}".strip() if returns else stripped
            elif section == "raises":
                match = re.match(r"^([A-Za-z_][\w.]*)\s*:\s*(.+)$", stripped)
                if match:
                    current_key = match.group(1)
                    raises[current_key] = match.group(2).strip()
                    continue
                if current_key and raw_line.startswith(" "):
                    raises[current_key] = f"{raises[current_key]} {stripped}".strip()

        return parameters, returns, raises

    def _parse_numpy(self, lines: list[str]) -> tuple[dict[str, str], str | None, dict[str, str]]:
        parameters: dict[str, str] = {}
        raises: dict[str, str] = {}
        returns: str | None = None
        section = ""
        current_key: str | None = None

        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if (
                self._NUMPY_SECTION_RE.match(stripped)
                and i + 1 < len(lines)
                and re.match(r"^\s*-{3,}\s*$", lines[i + 1])
            ):
                section = stripped.lower()
                current_key = None
                i += 2
                continue

            if not stripped:
                i += 1
                continue

            if section == "parameters":
                match = re.match(r"^([*]{0,2}[A-Za-z_]\w*)\s*:\s*(.+)$", stripped)
                if match:
                    current_key = match.group(1)
                    parameters[current_key] = ""
                    i += 1
                    while i < len(lines) and (not lines[i].strip() or lines[i].startswith("    ")):
                        line = lines[i].strip()
                        if line:
                            parameters[current_key] = f"{parameters[current_key]} {line}".strip()
                        i += 1
                    continue
            elif section == "returns":
                returns = stripped if returns is None else f"{returns} {stripped}".strip()
            elif section == "raises":
                if current_key is None:
                    current_key = stripped
                    raises[current_key] = ""
                else:
                    if stripped and not lines[i].startswith("    "):
                        current_key = stripped
                        raises[current_key] = ""
                    else:
                        raises[current_key] = f"{raises[current_key]} {stripped}".strip()
            i += 1

        return parameters, returns, raises

    def _parse_restructuredtext(self, docstring: str) -> tuple[dict[str, str], str | None, dict[str, str]]:
        parameters = {
            match.group("name"): match.group("desc").strip()
            for match in re.finditer(r"(?m)^\s*:param\s+(?P<name>\w+)\s*:\s*(?P<desc>.+)$", docstring)
        }
        returns_match = re.search(r"(?m)^\s*:returns?:\s*(?P<desc>.+)$", docstring)
        raises = {
            match.group("exc"): match.group("desc").strip()
            for match in re.finditer(r"(?m)^\s*:raises?\s+(?P<exc>[\w.]+)\s*:\s*(?P<desc>.+)$", docstring)
        }
        returns = returns_match.group("desc").strip() if returns_match else None
        return parameters, returns, raises
