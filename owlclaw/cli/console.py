"""CLI command for opening OwlClaw Console."""

from __future__ import annotations

import webbrowser
from pathlib import Path


def console_command(*, port: int = 8000, open_browser: bool = True) -> str:
    """Open Console URL in browser and return URL string."""
    url = f"http://localhost:{port}/console/"
    static_index = Path(__file__).resolve().parents[1] / "web" / "static" / "index.html"

    if not static_index.exists():
        print("Console static files not found. Install extras: pip install owlclaw[console]")
        print(f"Expected: {static_index}")
        return url

    print(f"Console URL: {url}")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            # Browser open failures should not break CLI usage.
            pass
    return url

