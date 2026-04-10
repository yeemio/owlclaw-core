"""Run local MCP spike demo and print latency summary for HTTP and stdio."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from starlette.testclient import TestClient

from owlclaw import OwlClaw
from owlclaw.mcp import McpProtocolServer, create_mcp_http_app


def _build_demo_app(root: Path) -> OwlClaw:
    skill_dir = root / "capabilities" / "ops" / "sum-tool"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: sum-tool
description: Sum two integers
metadata:
  version: "1.0.0"
owlclaw:
  task_type: calculation
---
# Sum Tool
""",
        encoding="utf-8",
    )
    app = OwlClaw("mcp-spike-demo")
    app.mount_skills(str(root / "capabilities"))

    @app.handler("sum-tool")
    async def sum_tool(a: int, b: int) -> dict[str, int]:
        return {"total": a + b}

    return app


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    idx = max(int(len(ordered) * 0.95) - 1, 0)
    return ordered[idx]


def run_spike(iterations: int) -> dict[str, float]:
    with TemporaryDirectory() as tmp:
        app = _build_demo_app(Path(tmp))
        server = McpProtocolServer.from_app(app)
        http_app = create_mcp_http_app(server=server, agent_card_url="http://127.0.0.1:8080")

        http_samples: list[float] = []
        with TestClient(http_app) as client:
            for i in range(iterations):
                started = perf_counter()
                response = client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": i,
                        "method": "tools/call",
                        "params": {"name": "sum-tool", "arguments": {"a": i, "b": 1}},
                    },
                )
                _ = response.json()
                http_samples.append((perf_counter() - started) * 1000)

        stdio_samples: list[float] = []
        for i in range(iterations):
            started = perf_counter()
            line = _run_async_line(
                server,
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "method": "tools/call",
                    "params": {"name": "sum-tool", "arguments": {"a": i, "b": 1}},
                },
            )
            _ = json.loads(line)
            stdio_samples.append((perf_counter() - started) * 1000)

    return {
        "http_p95_ms": round(_p95(http_samples), 2),
        "stdio_p95_ms": round(_p95(stdio_samples), 2),
    }


def _run_async_line(server: McpProtocolServer, payload: dict[str, object]) -> str:
    import asyncio

    return asyncio.run(server.process_stdio_line(json.dumps(payload)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MCP spike demo benchmark.")
    parser.add_argument("--iterations", type=int, default=120)
    args = parser.parse_args()

    result = run_spike(args.iterations)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
