# scripts/ 说明

本目录存放仓库级辅助脚本。脚本分为两类：

- `CI 使用`：被 GitHub Actions 或发布流程直接调用
- `本地开发使用`：开发者本地验证、诊断或模板生成

| 脚本 | 用途 | 典型命令 | 分类 |
|---|---|---|---|
| `release_preflight.py` | 发布前门禁检查（版本/变更/工件前置条件） | `poetry run python scripts/release_preflight.py` | CI 使用 |
| `release_oidc_preflight.py` | OIDC 发布预检（workflow/分支保护/ruleset/403 阻塞诊断） | `poetry run python scripts/release_oidc_preflight.py --run-id <id>` | 本地开发使用 |
| `owlhub_release_gate.py` | OwlHub 发布闸门验证 | `poetry run python scripts/owlhub_release_gate.py --help` | CI 使用 |
| `owlhub_build_index.py` | 生成 OwlHub index 数据 | `poetry run python scripts/owlhub_build_index.py --help` | CI 使用 |
| `owlhub_generate_site.py` | 生成 OwlHub 静态站点内容 | `poetry run python scripts/owlhub_generate_site.py --help` | CI 使用 |
| `validate_examples.py` | 批量验证 `examples/` 可运行性 | `poetry run python scripts/validate_examples.py` | CI 使用 |
| `contract_diff.py` | 协议契约差异分级与门禁决策（warning/blocking） | `poetry run python scripts/contract_diff.py --help` | CI 使用 |
| `contract_diff/run_contract_diff.py` | 契约门禁包装入口（用于 PR/nightly 统一调用） | `poetry run python scripts/contract_diff/run_contract_diff.py --help` | CI 使用 |
| `contract_diff/contract_testing_drill.py` | 执行 contract-testing breaking 注入演练并产出报告 | `poetry run python scripts/contract_diff/contract_testing_drill.py` | CI 使用 |
| `protocol_governance_drill.py` | 执行 breaking 注入/豁免审计演练并产出证据报告 | `poetry run python scripts/protocol_governance_drill.py` | CI 使用 |
| `gateway_ops_gate.py` | 网关发布门禁决策与回滚执行辅助 | `poetry run python scripts/gateway_ops_gate.py` | CI 使用 |
| `gateway_ops_drill.py` | 执行 canary 回滚/全量成功演练并产出报告 | `poetry run python scripts/gateway_ops_drill.py` | CI 使用 |
| `test_queue_trigger.py` | 队列触发链路本地回归脚本 | `poetry run python scripts/test_queue_trigger.py` | 本地开发使用 |
| `regression-core.ps1` | 核心链路一键回归（触发器/MCP/Web API，含可选 integration） | `pwsh ./scripts/regression-core.ps1` | 本地开发使用 |
| `regression-failure-paths.ps1` | 失败注入与异常路径一键回归（超时/依赖故障/重试/脱敏） | `pwsh ./scripts/regression-failure-paths.ps1` | 本地开发使用 |
| `console-local-setup.ps1` | Console 真实浏览器验收环境启动脚本（可选 DB 初始化/迁移/E2E） | `pwsh ./scripts/console-local-setup.ps1 -SkipDbInit -Port 8000` | 本地开发使用 |
| `workflow_status.py` | 多 worktree 工作流巡检（脏工作区、待审分支、下一步动作建议） | `poetry run python scripts/workflow_status.py --help` | 本地开发使用 |
| `workflow_orchestrator.py` | 持续轮询并写入 `.kiro/runtime/` 的工作流执行器（供统筹/各 agent 消费） | `poetry run python scripts/workflow_orchestrator.py --once` | 本地开发使用 |
| `workflow_mailbox.py` | 读取 agent mailbox / 写入 ack 状态的轻量 CLI（文件信箱协议） | `poetry run python scripts/workflow_mailbox.py pull --agent review --json` | 本地开发使用 |
| `workflow_objects.py` | 闭环协议对象层：读写 finding/triage/assignment/delivery/verdict/merge/blocker，并维护索引/状态迁移/claim lease | `poetry run python scripts/workflow_objects.py summary --json` | 本地开发使用 |
| `workflow_assignments.py` | 解析 `WORKTREE_ASSIGNMENTS.md` 并校验 triage 生成的 assignment 是否越过人工分配边界 | 内部模块（由 executor 调用） | 本地开发使用 |
| `workflow_roles.py` | 定义 6 个窗口的岗位合同（role_title/role_contract/must_do/must_not_do），供 mailbox/dispatch/executor/terminal-control 重复注入 | 内部模块（由 orchestrator/executor/control 调用） | 本地开发使用 |
| `workflow_agent.py` | 半自动 agent consumer：轮询 mailbox、自动 `ack seen`、写 heartbeat 与 dispatch prompt | `poetry run python scripts/workflow_agent.py --agent review --once` | 本地开发使用 |
| `workflow_executor.py` | 真正执行 mailbox 动作：按角色调用 `codex exec` / `agent --print` / `claude -p` 在对应 worktree 中执行任务 | `poetry run python scripts/workflow_executor.py --agent review --once` | 本地开发使用 |
| `workflow_supervisor.py` | 从主仓统一拉起/停止/巡检 orchestrator + 各 worktree workflow_agent/workflow_executor 进程，写 PID、日志并检测 stalled workflow objects | `poetry run python scripts/workflow_supervisor.py start` | 本地开发使用 |
| `workflow-supervisor-console.ps1` | 打开一个可视监控终端，前台运行 `workflow_supervisor.py watch --ensure-running` | `pwsh ./scripts/workflow-supervisor-console.ps1` | 本地开发使用 |
| `workflow_terminal_control.py` | 终端观察/催办脚本：默认只记录 observe 状态；仅在显式 `--transport sendkeys` 时才向现有 CLI 窗口注入固定话术 | `poetry run python scripts/workflow_terminal_control.py --transport disabled --interval 15 --force` | 本地开发使用 |
| `workflow-terminal-control-console.ps1` | 前台终端观察控制台；默认 observe-only，不抢焦点/鼠标/剪贴板；仅在 `-EnableSendKeys` 时启用旧的窗口注入 | `pwsh ./scripts/workflow-terminal-control-console.ps1` | 本地开发使用 |
| `workflow_audit_state.py` | 审计窗口状态协议：更新/查看 `audit-a`、`audit-b` 的 runtime 状态，并以“只读代码审计”模式提交带代码证据的 structured finding | `poetry run python scripts/workflow_audit_state.py update --agent audit-a --status started` | 本地开发使用 |
| `workflow_audit_heartbeat.py` | 审计窗口状态心跳：持续刷新 `audit-a`、`audit-b` 的 runtime 状态时间 | `poetry run python scripts/workflow_audit_heartbeat.py --agent audit-a --once` | 本地开发使用 |
| `workflow_focus_window.ps1` | 激活某个 workflow 终端窗口，供人工接管 | `pwsh ./scripts/workflow_focus_window.ps1 -WindowTitle owlclaw-main` | 本地开发使用 |
| `workflow-launch.ps1` | 一键拉起 6 个独立工作窗口（默认 observer 视图，不抢焦点/剪贴板），自动按 `3x2` 平铺，并启动控制窗口 | `pwsh ./scripts/workflow-launch.ps1` | 本地开发使用 |
| `workflow_terminal_title.ps1` | 给当前已打开终端设置可驱动标题（如 `owlclaw-codex`、`owlclaw-audit-a`） | `pwsh ./scripts/workflow_terminal_title.ps1 -Name codex` | 本地开发使用 |
| `workflow_sendkeys.ps1` | 底层窗口驱动：激活指定窗口标题并粘贴/回车；仅在显式启用 sendkeys transport 时使用 | 内部脚本 | 本地开发使用 |
| `review_template.py` | 生成/检查审校模板 | `poetry run python scripts/review_template.py --help` | 本地开发使用 |
| `test_template.py` | 测试模板脚手架检查 | `poetry run python scripts/test_template.py --help` | 本地开发使用 |
| `workflow_agent.py` | 多 worktree 工作流入口（sync/status/test） | `poetry run python scripts/workflow_agent.py --agent codex` | 本地开发使用 |
| `completions/` | CLI 自动补全生成物 | 按 shell 类型加载 | 本地开发使用 |

## 约定

1. 所有脚本应支持 `--help`（或在文件头部给出使用说明）。
2. CI 关键脚本改动必须附带对应单元测试或集成验证。
3. 脚本内禁止硬编码密钥；凭证统一走环境变量。
4. `workflow_orchestrator.py` 负责生成 `.kiro/runtime/mailboxes/*.json`；各 agent 通过 `workflow_mailbox.py pull/ack` 与统筹交换状态。mailbox 现在不仅带 action/object refs，还会带岗位合同字段（`role_title` / `role_contract` / `must_do` / `must_not_do`）。
5. `workflow_agent.py` 用于挂在各 agent 会话里持续消费 mailbox，生成 `.kiro/runtime/dispatch/*.md` 和 `.kiro/runtime/heartbeats/*.json`。
6. `workflow_executor.py` 是执行层：它读取 mailbox 后按角色选择非交互 CLI（`main -> codex`，`coding -> agent`，`review -> claude`），结果写入 `.kiro/runtime/executions/` 并回写 ack。
7. `workflow_supervisor.py` 负责跨 worktree 启停 automation 进程；日志位于 `.kiro/runtime/supervisor/logs/`，PID manifest 位于 `.kiro/runtime/supervisor/pids/`。`status/watch` 还会汇总 stalled workflow objects（如 lease 过期、pending 太久）。
8. 如需一个长期可视监控终端，直接运行 `pwsh ./scripts/workflow-supervisor-console.ps1`；它会前台 watch，并在 worker 掉线时自动拉起。
9. 如需观察已经打开的 CLI 窗口，先在每个窗口运行 `workflow_terminal_title.ps1` 设标题，再运行 `workflow_terminal_control.py` 或 `workflow-terminal-control-console.ps1`；默认只写 observe 状态，不会再直接粘贴固定话术。只有显式传 `--transport sendkeys` 或 `-EnableSendKeys` 时，才会启用旧的窗口注入。
10. 现有窗口标题建议统一为：`owlclaw-main`、`owlclaw-review`、`owlclaw-codex`、`owlclaw-codex-gpt`、`owlclaw-audit-a`、`owlclaw-audit-b`；其中 `review` 窗口如果被 Claude CLI 覆盖标题，控制器会回退匹配 `claude`。新版启动器还会写出 `.kiro/runtime/terminal-windows.json`，人工 `takeover` 时会优先按 PID/HWND 聚焦窗口。
11. 如果不想手动一个个开窗口，直接运行 `pwsh ./scripts/workflow-launch.ps1`；它会按串行方式启动 6 个 observer 工作窗口，分别显示 main/review/coding/audit 的 mailbox、最近执行结果、最近 assistant 输出、最近 workflow 对象和日志，再开一个 `owlclaw-control` 控制窗口。默认控制窗口现在运行 `workflow-supervisor-console.ps1`；只有显式加 `-UseTerminalController` 时，才切到 `workflow-terminal-control-console.ps1`。
12. 如需禁用平铺，可追加 `-SkipLayout`；如需调整等待窗口出现后再布局的时间，可设置 `-LayoutDelaySeconds <n>`；如需减慢批量起窗速度避免丢窗，可设置 `-LaunchSpacingMilliseconds <n>`。
13. 控制窗口支持人工接管：`pause` 暂停自动发送，`resume` 恢复，`send <agent>` 立即发一次固定话术，`takeover <agent>` 激活目标窗口供人工操作，`status` 查看当前暂停状态。自动循环默认会参考 mailbox 指纹、`object_type/object_id`、ack、heartbeat 的更新时间，只在任务变化或 agent 停滞时催办，不再固定盲发。
14. `audit-a` / `audit-b` 也应维护自己的状态文件，使用 `workflow_audit_state.py update --agent audit-a|audit-b ...` 写入 `.kiro/runtime/audit-state/*.json`；控制器会据此判断是否需要催办。新版 `workflow-launch.ps1` 会在启动审计窗口时自动写入初始 `idle` 状态，并启动 `workflow_audit_heartbeat.py` 持续刷新状态时间。
15. 两个审计窗口都属于只读审计角色：必须读代码，不得只读文档，不得修改代码；合法产出只有 `workflow_audit_state.py finding` 写出的 structured finding。审计协议见 [AUDIT_WORKFLOW_PROTOCOL.md](/D:/ai/owlclaw/docs/AUDIT_WORKFLOW_PROTOCOL.md)。
