# OwlClaw Core

Open-source runtime for turning existing business systems into governed AI agents without rewriting the system itself.

`OwlClaw Core` focuses on the backend side of business AI:

- agent runtime
- trigger orchestration
- governance and audit controls
- skill loading
- declarative bindings
- MCP exposure for higher-level assistants such as OpenClaw

This public repository is a trimmed open-source release. It intentionally does not include the full private documentation and internal process assets from the development repository.

## Scope

OwlClaw is designed for teams that already have systems like ERP, CRM, MES, internal APIs, job queues, or databases and want to add:

- scheduled or event-driven AI execution
- controlled tool visibility
- budget and audit boundaries
- structured skill-based business logic
- MCP-compatible tool exposure

## What Is Included

- Python runtime package: `owlclaw/`
- MCP package: `owlclaw-mcp/`
- database migrations
- example apps and sample skills
- frontend source and built console assets needed by the package

## What Is Not Included

- most internal architecture and review documents
- internal workflow automation assets
- project-specific private experimentation material

## Quick Start

```bash
poetry install
poetry run owlclaw --help
```

Runtime baseline:

- Python 3.10 to 3.13
- Poetry-managed environment

Minimal example:

```python
from owlclaw import OwlClaw

app = OwlClaw("inventory-agent")

app.mount_skills("./capabilities")

@app.handler("inventory-monitor")
async def check_inventory(session) -> dict:
    return {"ok": True}

app.run()
```

## Main Capabilities

- Skills and handlers
- Cron, webhook, queue, API, signal, and DB-change triggers
- Governance filters and decision logging
- LLM routing through `litellm`
- Durable execution integration
- MCP server support

## Repository Notes

- Package name remains `owlclaw`
- MCP companion package remains `owlclaw-mcp`
- Public repo name is `owlclaw-core`

## License

Released under `Apache-2.0`.
