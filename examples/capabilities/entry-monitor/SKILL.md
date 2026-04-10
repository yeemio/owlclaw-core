---
name: entry-monitor
description: Check entry opportunities for held positions
metadata:
  author: owlclaw-examples
  version: "1.0"
owlclaw:
  spec_version: "1.0"
  task_type: trading_decision
  constraints:
    trading_hours_only: true
    cooldown_seconds: 300
  trigger: cron("*/60 * * * * *")
---

# Entry Monitor — Usage

This Skill describes when and how to check for entry opportunities on positions you already hold.

## When to use

- During trading hours.
- When no recent check was done (respect cooldown).
- When the Agent decides it is time to re-evaluate entries (AI decision).

## What the handler does

The `entry-monitor` handler receives a session context and returns signals or actions. The Agent uses this Skill's instructions to decide when to call the handler and how to interpret results.

## References

- See `references/` for trading rules (if present).
- See `scripts/` for helper scripts (e.g. signal checks).
