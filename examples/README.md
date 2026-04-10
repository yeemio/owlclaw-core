# OwlClaw Examples

示例应用，展示如何用 OwlClaw 让成熟业务系统获得 AI 自主能力。

## 示例列表

| 示例 | 场景 | 复杂度 |
|------|------|--------|
| `cron/` | Cron 触发器完整示例（focus/治理/重试） | 中 |
| `quick_start/` | Quick Start 最小可运行示例（Lite Mode 零依赖） | 低 |
| `complete_workflow/` | 完整库存管理端到端示例（4 能力 + 治理） | 中 |
| `langchain/` | LangChain runnable 集成（注册/重试/流式/追踪） | 中 |
| `binding-http/` | Declarative Binding 示例（active/shadow/shell） | 中 |
| `binding-openapi-e2e/` | OpenAPI 到 binding SKILL.md 的端到端流程 | 中 |
| `capabilities/` | 技能能力目录示例（entry-monitor/morning-decision） | 低 |
| `owlhub_skills/` | OwlHub 示例技能（analytics/monitoring/workflow） | 低 |
| `mionyee-trading/` | mionyee 三任务端到端示例（entry/morning/feedback） | 中 |
| `skill_templates/` | 模板渲染与参数化示例（Python 脚本） | 低 |
| `skill-templates/` | 模板工作流操作步骤示例（Markdown） | 低 |
| `integrations_llm/` | LLM 集成调用与函数调用示例 | 中 |

## 单文件示例

- `basic_usage.py`
- `agent_runtime_flow.py`
- `agent_tools_demo.py`
- `hatchet_basic_task.py`
- `hatchet_cron_task.py`
- `hatchet_durable_sleep.py`
- `hatchet_self_schedule.py`

## 快速开始

```bash
cd examples/cron
poetry run python nightly_data_cleanup.py
```
