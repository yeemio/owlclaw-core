---
name: anomaly-alert
description: Detect abnormal consumption patterns and raise alerts.
metadata:
  author: owlclaw-example
  version: "1.0.0"
  tags: [inventory, alert]
owlclaw:
  spec_version: "1.0"
  task_type: alert
  constraints:
    max_daily_calls: 20
---

# Anomaly Alert

Use this skill to summarize risk signals and notify operators.
