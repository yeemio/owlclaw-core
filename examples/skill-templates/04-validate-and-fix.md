# 示例 4：验证并修复 SKILL.md

```bash
# 验证目录下所有 SKILL.md
owlclaw skill validate capabilities/

# 严格模式（warning 也会失败）
owlclaw skill validate capabilities/ --strict
```

常见修复点：

- `name` 改为 kebab-case（如 `my-skill`）
- `description` 保持非空字符串
- body 至少包含一个 Markdown 标题

