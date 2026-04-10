---
name: order-shell-fallback
description: Shell-only skill with no binding and no handler.
---

# Instructions

When binding or handler is not ready, use shell tooling to call external API.

Example command:

```bash
curl -s http://127.0.0.1:8008/orders/A1001
```

Guidelines:

- Use this mode only for temporary integration fallback.
- Keep command output concise and summarize key fields to user.
- Migrate to declarative binding once API contract is stable.
