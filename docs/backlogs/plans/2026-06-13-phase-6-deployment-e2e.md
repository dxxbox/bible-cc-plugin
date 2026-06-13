# Phase 6: E2E Validation + Documentation + Ship

> **For agentic workers:** Phase 6 是发布就绪阶段。Setup/install 已在 Phase 0 完成——Phase 6 聚焦于端到端验证、CI 成熟化、文档完善。

**Goal:** E2E 测试覆盖 4 个完整用户旅程、CI 流水线成熟化（E2E + debug mode）、监控数据采集、README 故障排查文档。

**Architecture:** E2E tests (pytest + BiBLE test server) + dev.sh maturation + data collection (background, non-blocking)。

**Tech Stack:** Shell, Python, pytest

**预估: 4-5 天**

---

## Feature 逐个讨论

### F6.1 — dev.sh 成熟化 + 升级/卸载完善

| 属性 | 说明 |
|------|------|
| **理由** | `dev.sh` 在 Phase 0 创建了骨架（init/test/lint/ci），经过 Phase 1-5 逐步扩展。Phase 6 将其成熟化——增加 `reload`、`restart` 便利命令 + CI `--debug` mode + CI HTML report。升级/卸载的基础能力在 Phase 0（setup.py, daemon.py stop），Phase 6 增加 `/bible-cc:upgrade`、`/bible-cc:changelog` 等面向终端用户的命令。 |
| **优先级** | P1 — 开发体验 + 用户运维 |
| **依赖** | 所有组件就绪、Phase 0 daemon lifecycle |

```bash
./scripts/dev.sh reload     # stop daemon → 下次 SessionStart 自动重启
./scripts/dev.sh restart    # stop daemon → 立即 restart
./scripts/dev.sh test       # uv run pytest (unchanged from Phase 0)
./scripts/dev.sh lint       # uv run ruff check (unchanged from Phase 0)
./scripts/dev.sh ci         # 完整 CI: lint → unit → contract → integration → E2E
./scripts/dev.sh ci --debug # 失败时自动 dump 每阶段诊断信息
```

升级/卸载命令（在 Phase 5 commands 中实现，此处仅确认就绪）：
- `/bible-cc:upgrade`: git pull → uv sync → prompt reload
- `/bible-cc:changelog`: 版本更新日志
- `/bible-cc:uninstall`: stop daemon → rm ~/.bible-cc/ → prompt plugin uninstall（daemon stop 在 Phase 0 已实现）

### F6.2 — 监控数据采集

| 属性 | 说明 |
|------|------|
| **理由** | Token 用量和性能数据是 server 侧 dashboard 的数据源。采集是后台行为——不阻塞任何用户操作。随 push 上报到 BiBLE Atlas。 |
| **优先级** | P2 — 后台功能 |
| **依赖** | buffer.py（存储）、flush logic（上报，Phase 3） |

采集指标：token（session total, injection token, detection LLM token）、performance（daemon API p50/p95/p99, SQLite query time）、health（uptime, crash count, flush success/fail ratio）。本地保留 30 天，由 `/bible-cc:gc` 清理过期数据。

### F6.3 — E2E Tests

| 属性 | 说明 |
|------|------|
| **理由** | 验证完整用户工作流，贯穿所有四组件。E2E 数量少但必须稳定、可复现。每个 case 都覆盖 sunny path + rainy path。 |
| **优先级** | P1 — 发布前最后防线 |
| **依赖** | 所有组件就绪、BiBLE test server |

4 个关键旅程：
- `test_install_status_flow.py`: setup → daemon start → status → check-bible（验证 1st call 完整链路。Setup wizard 在 Phase 0，E2E 在此验证整条链路仍然 green）
- `test_session_capture_flow.py`: session-start → user turns (≥10) → tool turns → session-end → verify moments flushed to BiBLE（验证完整采集 → flush 链路）
- `test_clear_recovery_flow.py`: /clear 触发 SessionStart → context injection → crash recovery（验证 pull model 两条路径）
- `test_review_push_flow.py`: detect moments → review pending → edit → push → verify at BiBLE（验证用户数据主权链路）

### F6.4 — Documentation Finalization

| 属性 | 说明 |
|------|------|
| **理由** | README 是用户第一眼看到的东西。CLAUDE.md 需要与实际实现一致。SW design docs 中的 "状态" 标记需要更新。 |
| **优先级** | P1 |
| **依赖** | 全部 Phase 完成 |

更新：README.md（安装/使用/故障排除，含 5 个常见场景的诊断步骤）、CLAUDE.md（状态更新为 "implemented"）、SW design docs（状态标记更新）。

### F6.5 — CI Pipeline 成熟化：E2E + --debug Mode + HTML Report

| 属性 | 说明 |
|------|------|
| **理由** | Phase 6 是 CI 的最终形态——E2E 测试接入 CI pipeline，完整覆盖从 setup 到 session-end 的用户旅程。CI `--debug` mode 让 CI 失败时自动 dump 诊断信息。CI 生成 HTML report 供团队查看。 |
| **优先级** | P0 — CD 最终验证 |
| **依赖** | Phase 5 CI、F6.3（E2E tests） |

实现：
- `dev.sh ci` 最终形态：lint → unit test → contract test → integration test → E2E test
- `dev.sh ci --debug`：每个阶段失败时自动 dump 该阶段的诊断信息
- CI 生成 HTML 报告（pytest-html）包含 test results + daemon diagnostics
- E2E tests 使用 dynamic port + temp HOME，不污染开发环境

### F6.6 — Debuggability：E2E 诊断 + CI 日志 + 故障排查

| 属性 | 说明 |
|------|------|
| **理由** | E2E 测试失败时必须能快速定位。CI 失败时需要一眼看到哪个 step 失败。README 中的故障排查章节是用户自助诊断的第一站。 |
| **优先级** | P0 — E2E 调试 + 用户自助 |
| **依赖** | 所有 Phase 完成、E2E tests、dev.sh |

- `scripts/diagnose-e2e.sh`：解析 pytest 输出 → 提取 daemon pid → dump health → dump debug endpoints → 输出诊断摘要
- `scripts/setup.py --debug`：输出每步诊断（Phase 0 已有基础，Phase 6 补充 BiBLE connectivity 分解）
- E2E conftest.py：E2E 失败时自动 dump daemon 状态
- README 故障排查章节：5 个场景（daemon 启动失败、BiBLE 连不上、detection 不工作、flush 失败、MCP tools 不可用），每个提供症状 → 诊断命令 → 常见原因 → 修复步骤

---

## Phase 6 验收标准

- [ ] `./scripts/dev.sh ci` 通过（完整流水线：lint → unit → contract → integration → E2E）
- [ ] `./scripts/dev.sh ci --debug` 失败时自动 dump 每阶段诊断信息
- [ ] CI 生成 HTML report（pytest-html）含 test results + diagnostics
- [ ] E2E 测试全部通过（4 个关键用户旅程，每个含 sunny + rainy path）
- [ ] E2E `test_install_status_flow.py` 验证 Phase 0 的 1st call 链路仍然 green
- [ ] E2E 失败时 conftest 自动 dump daemon diagnostics（health, logs, detections）
- [ ] `scripts/diagnose-e2e.sh` 可运行并输出诊断摘要
- [ ] 升级/卸载流程可操作（手动测试通过）
- [ ] README 包含故障排查章节（至少 5 个常见场景的诊断步骤）
- [ ] Token 数据采集正确，随 push 上报到 BiBLE Atlas
- [ ] `uv run ruff check` 零 warning

---

## Phase 6 产出文件

```
scripts/
├── diagnose-e2e.sh            ← F6.6 (E2E 失败诊断脚本)
src/bible_cc_plugin/
├── daemon/
│   └── server.py              ← (修改: monitoring data collection)
tests/
└── e2e/
    ├── __init__.py
    ├── conftest.py             ← F6.6 (E2E fixtures: daemon 诊断 dump)
    ├── test_install_status_flow.py   ← F6.3
    ├── test_session_capture_flow.py  ← F6.3
    ├── test_clear_recovery_flow.py   ← F6.3
    └── test_review_push_flow.py      ← F6.3
reports/                       ← F6.5 (CI 产出: JUnit XML, HTML report)
README.md                      ← F6.4, F6.6 (updated + 故障排查章节)
```
