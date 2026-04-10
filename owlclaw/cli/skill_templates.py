"""Template helper for conversational skill creation."""

from __future__ import annotations

from pathlib import Path

import typer

_BUILTIN_TEMPLATES: dict[str, str] = {
    "inventory-monitor": """---
name: inventory-monitor
description: 每天检查库存并在低于安全线时提醒
---
# Inventory Monitor

## Business Rules
- Trigger: 每天早上 9 点检查库存
- 当库存低于安全库存线 120% 时提醒
- 周末不执行
""",
    "order-processor": """---
name: order-processor
description: 处理新订单并同步状态
---
# Order Processor

## Business Rules
- Trigger: 当收到新订单时执行
- 校验订单完整性并写入订单系统
- 异常时发送告警
""",
    "report-generator": """---
name: report-generator
description: 周期性生成业务报表
---
# Report Generator

## Business Rules
- Trigger: 每周一 9 点生成上周报表
- 输出关键指标摘要和异常项
- 发送给运营负责人
""",
}


def _template_dir() -> Path:
    return Path.home() / ".owlclaw" / "templates"


def ensure_local_templates() -> Path:
    root = _template_dir()
    root.mkdir(parents=True, exist_ok=True)
    for name, content in _BUILTIN_TEMPLATES.items():
        file_path = root / f"{name}.md"
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")
    return root


def list_templates_command() -> None:
    root = ensure_local_templates()
    templates = sorted(root.glob("*.md"))
    if not templates:
        typer.echo("No templates found.")
        return
    for item in templates:
        typer.echo(item.stem)


def load_template(name: str) -> str:
    root = ensure_local_templates()
    candidate = root / f"{name}.md"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(f"template not found: {name}")
