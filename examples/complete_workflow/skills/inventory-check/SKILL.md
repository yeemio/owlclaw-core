---
name: inventory-check
description: Check stock levels and identify low-stock SKUs.
metadata:
  author: owlclaw-example
  version: "1.0.0"
  tags: [inventory, monitoring]
owlclaw:
  spec_version: "1.0"
  task_type: monitor
  constraints:
    cooldown_seconds: 60
---

# Inventory Check

Use this skill to identify products that need immediate replenishment review.
