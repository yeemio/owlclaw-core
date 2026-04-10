"""IdentityLoader — loads SOUL.md and IDENTITY.md for Agent system prompt."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CAPABILITY_HEADINGS = {"## my capabilities", "## 我的能力"}


class IdentityLoader:
    """Load and manage Agent identity from SOUL.md and IDENTITY.md.

    Both files must live in *app_dir*.  IDENTITY.md is expected to contain a
    ``## My Capabilities`` (or ``## 我的能力``) section whose content is
    extracted as the *capabilities summary* injected into the system prompt.
    SOUL.md and IDENTITY.md are both required.

    Usage::

        loader = IdentityLoader("/path/to/app")
        await loader.load()
        identity = loader.get_identity()
        # {"soul": "...", "capabilities_summary": "..."}
    """

    def __init__(self, app_dir: str) -> None:
        if not isinstance(app_dir, str) or not app_dir.strip():
            raise ValueError("app_dir must be a non-empty string")
        self.app_dir = Path(app_dir.strip())
        self.soul_path = self.app_dir / "SOUL.md"
        self.identity_path = self.app_dir / "IDENTITY.md"

        self._soul: str | None = None
        self._identity: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """Load identity files from disk.

        Raises:
            FileNotFoundError: If SOUL.md is missing.
        """
        if not self.soul_path.exists():
            raise FileNotFoundError(
                f"SOUL.md not found at {self.soul_path}. "
                "Create a SOUL.md in your application directory to give "
                "the Agent its role and principles."
            )
        if not self.identity_path.exists():
            raise FileNotFoundError(
                f"IDENTITY.md not found at {self.identity_path}. "
                "Create an IDENTITY.md in your application directory to "
                "define capabilities and constraints."
            )
        self._soul = self.soul_path.read_text(encoding="utf-8").lstrip("\ufeff")
        if not self._soul.strip():
            raise ValueError("SOUL.md must not be empty")
        logger.debug("Loaded SOUL.md from %s", self.soul_path)
        self._identity = self.identity_path.read_text(encoding="utf-8").lstrip("\ufeff")
        if not self._identity.strip():
            raise ValueError("IDENTITY.md must not be empty")
        logger.debug("Loaded IDENTITY.md from %s", self.identity_path)

    async def reload(self) -> None:
        """Hot-reload identity files from disk (re-reads both files)."""
        await self.load()

    def get_identity(self) -> dict[str, str]:
        """Return identity dict for system prompt construction.

        Returns:
            ``{"soul": str, "capabilities_summary": str}``

        Raises:
            RuntimeError: If :meth:`load` has not been called yet.
        """
        if self._soul is None:
            raise RuntimeError(
                "IdentityLoader.load() must be called before get_identity()"
            )
        return {
            "soul": self._soul,
            "capabilities_summary": self._extract_capabilities_summary(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_capabilities_summary(self) -> str:
        """Extract the ## My Capabilities section from IDENTITY.md."""
        if not self._identity:
            return ""

        lines = self._identity.splitlines()
        in_section = False
        result: list[str] = []

        for line in lines:
            stripped = line.rstrip()
            if stripped.strip().lower() in _CAPABILITY_HEADINGS:
                in_section = True
                continue
            if in_section:
                if stripped.startswith("##"):
                    break
                normalized = stripped.strip()
                if normalized.startswith("-"):
                    item = normalized[1:].strip()
                    result.append(f"- {item}" if item else "-")
                else:
                    result.append(stripped)

        return "\n".join(result).strip()
