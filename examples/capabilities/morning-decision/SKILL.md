---
name: morning-decision
description: Morning routine — decide priorities and first actions for the day
metadata:
  author: owlclaw-examples
  version: "1.0"
owlclaw:
  spec_version: "1.0"
  task_type: planning
  trigger: cron("0 9 * * 1-5")
---

# Morning Decision — Usage

This Skill guides the Agent's morning routine: what to check first, which state to query, and how to prioritize the day's tasks.

## When to use

- At a scheduled morning time (e.g. 09:00 on weekdays).
- When the user or system triggers a "start of day" flow.

## What the handler does

The `morning-decision` handler can aggregate market state, calendar, or other context and return a suggested plan. The Agent uses this Skill's instructions to decide when to run it and how to present results.
