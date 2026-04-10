---
name: inventory-check
description: Check stock level and decide whether to reorder.
metadata:
  author: owlclaw-example
  version: "1.0.0"
  tags: [inventory, operations]
owlclaw:
  spec_version: "1.0"
  task_type: operations
  constraints:
    cooldown_seconds: 60
---

# Inventory Check

Use this skill to evaluate stock risk and return a concrete replenishment action.
