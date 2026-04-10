# 示例 1：创建监控 Skill（health-check）

```bash
owlclaw skill init --template monitoring/health-check \
  --param "skill_name=api-monitor,skill_description=Monitor API health,endpoints=/health,/ready" \
  --output capabilities
```

