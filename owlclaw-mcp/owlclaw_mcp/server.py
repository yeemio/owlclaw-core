"""CLI wrapper for OwlClaw MCP stdio server."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from owlclaw.app import OwlClaw
from owlclaw.mcp.server import McpProtocolServer


def _build_server(skills_dir: str) -> McpProtocolServer:
    app = OwlClaw("owlclaw-mcp")
    app.mount_skills(Path(skills_dir))
    return McpProtocolServer.from_app(app)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OwlClaw MCP server over stdio.")
    parser.add_argument("--skills-dir", default="capabilities", help="Directory that contains SKILL.md files.")
    args = parser.parse_args()

    server = _build_server(args.skills_dir)
    for line in sys.stdin:
        payload = line.strip()
        if not payload:
            continue
        response = asyncio.run(server.process_stdio_line(payload))
        print(response, flush=True)


if __name__ == "__main__":
    main()
