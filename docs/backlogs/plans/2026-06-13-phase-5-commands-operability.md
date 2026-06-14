# Phase 5: Commands + Operability

> **Phase 0 复盘调整**: 原计划将所有 slash commands 集中在 Phase 5，导致 Phase 1-4 期间用户没有命令可用。现已将大部分命令分散到对应 feature phase：Phase 1a→status/help/version/config, 1b→sessions, 1c→context, 2c→review, 3→push/check-bible, 4→consult。Phase 5 缩小为 operability polish + 剩余 medium-priority commands + 集成 sanity check。

**Goal:** 一键诊断 + 运行时日志控制 + 故障路径覆盖 + 恢复操作 + 4-component sanity check。

**Architecture:** Shell scripts / Markdown command files → daemon HTTP API → formatted output。

**Tech Stack:** Shell (curl), Python (uv run), Markdown

**预估: 5-7 天**（5a: 3-4d, 5b: 2-3d）

> **/orchestrate 提示**: Phase 5 拆分为两个独立的 `/orchestrate custom` 调用——5a 和 5b。5b 依赖 5a 的 MVP command 骨架（status/check-bible/config），但 debug 命令（diagnose/log-level/logs）可独立于 5a 开发。

---

## Phase 5a — Core Commands（3-4 天）

### F5a.1 — MVP Commands（7 个）

| 属性 | 说明 |
|------|------|
| **理由** | command-priority-table 定义的 7 个 MVP 命令是最小可诊断/可用集合。它们覆盖 operability（status, check-bible）、discoverability（help）、debug（config, version, context, sessions）。没有这些命令，用户无法知道 plugin 是否工作、BiBLE 是否连通、刚才注入了什么。 |
| **优先级** | P0 — MVP |
| **依赖** | daemon HTTP API（全部端点就绪）、client.py（check-bible） |

| # | 命令 | 理由 | 实现 |
|---|------|------|------|
| 1 | `/bible-cc:status` | operability：daemon 状态、BiBLE 连通性、buffer 统计、SQLite 完整性 | `GET /daemon/health` → 格式化表格输出 |
| 2 | `/bible-cc:check-bible` | connectivity：BiBLE Atlas 心跳检查 | `GET /health`（via client.py）→ latency + status |
| 3 | `/bible-cc:help` | discoverability：列出所有可用命令 | 静态 markdown 渲染 |
| 4 | `/bible-cc:config` | debug：查看当前生效配置（含 env override 来源标注） | 读 config.py 输出 → 标注来源 |
| 5 | `/bible-cc:version` | debug：当前 plugin 版本和依赖版本 | 读 pyproject.toml + importlib.metadata |
| 6 | `/bible-cc:context` | debug：上次注入到模型的 `<relevant-memories>` 内容 | 读 daemon 缓存的 last injection |
| 7 | `/bible-cc:sessions` | debug：活跃 session 列表 | `GET /daemon/health` sessions field → 详细列表 |

### F5a.2 — 高优先级 Commands（3 个）

| 属性 | 说明 |
|------|------|
| **理由** | push、review、consult 是用户日常使用频率最高的三个操作命令。review 赋予用户对自己数据的主权——在 moment flush 到 BiBLE 之前可以编辑、删除。push 满足"我现在就想把 moments 发出去"的需求。 |
| **优先级** | P0 — 核心用户操作 |
| **依赖** | daemon flush logic、moments CRUD 端点、consult 端点 |

| # | 命令 | 理由 | 实现 |
|---|------|------|------|
| `/bible-cc:push` | 手动 flush pending moments | `POST /daemon/session/flush` |
| `/bible-cc:consult` | 用户主动跨域搜索 | `POST /daemon/consult` |
| `/bible-cc:review` | 管理 pending moments：查看列表、编辑 title/abstract、删除、force-flush | `GET/PUT/DELETE /daemon/moments` |

### F5a.3 — Review Command 详细行为

| 属性 | 说明 |
|------|------|
| **理由** | 数据主权是 BiBLE 的核心设计原则——用户的 moment 在推送前完全可控。 |
| **优先级** | P0 |
| **依赖** | `GET/PUT/DELETE /daemon/moments`、`POST /daemon/session/flush` |

### F5a.4 — Command Spec 文件（commands/*.md）

| 属性 | 说明 |
|------|------|
| **理由** | Claude Code command 是 markdown 文件。每个 command 是薄封装——调用 daemon HTTP API 或运行 shell 命令。 |
| **优先级** | P0 |
| **依赖** | 对应 daemon 端点就绪 |

### F5a.5 — CI Pipeline 扩展 + Contract Tests

| 属性 | 说明 |
|------|------|
| **理由** | CI 验证每个 command 的输出格式正确 + 错误 case 不 crash。Contract tests 验证输出格式符合 `07-commands/specs.md`。 |
| **优先级** | P0 |
| **依赖** | Phase 4 CI、F5a.1-F5a.2 |

- `dev.sh ci` 扩展包含 command integration/contract tests
- `tests/contract/test_command_output.py`：status/check-bible/config/version/context/sessions/push/consult/review 输出格式验证

---

## Phase 5b — Operability + Diagnostics（2-3 天）

### F5b.1 — 中优先级 Commands（择取 8 个）

| 属性 | 说明 |
|------|------|
| **理由** | 增强日常使用体验，但不阻塞核心链路。时间允许时实现。 |
| **优先级** | P1 — 增强体验 |

| 命令 | 场景 | 依赖 |
|------|------|------|
| `/bible-cc:config-set` | 修改配置项 | config.py write |
| `/bible-cc:capture-pause` | 暂停记录 | daemon capture state toggle |
| `/bible-cc:capture-resume` | 恢复记录 | 同上 |
| `/bible-cc:recover` | 手动触发 crash recovery | crash recovery logic |
| `/bible-cc:token-usage` | 当前 session token 统计 | monitoring data |
| `/bible-cc:push-all` | 跨 session 全局 push | flush logic (all sessions) |
| `/bible-cc:retry-push` | 重试失败的 push | flush retry logic |
| `/bible-cc:buffer` | 查看 buffer 内容摘要 | buffer.py read |

### F5b.2 — Failure Paths + Recovery

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 硬性约束——用户永远不需要翻 daemon 日志来排障。 |
| **优先级** | P1 — 运维必备 |
| **依赖** | hint system、health check、所有端点 |

故障 → 诊断 → 恢复映射：端口冲突、BiBLE 断连、Hook 失败、Crash recovery。

### F5b.3 — Debuggability：诊断命令 + Log Level 控制

| 属性 | 说明 |
|------|------|
| **理由** | 用户遇到问题时需要深度诊断能力。一键诊断报告让用户或开发者一眼看清所有环节状态。 |
| **优先级** | P0 — 用户调试入口 |
| **依赖** | 所有前序 Phase debug endpoints、config.py、health endpoint |

- `/bible-cc:status --verbose`：config sources + recent detections + recent BiBLE requests
- `/bible-cc:diagnose`：一键 6-check 全链路诊断报告
- `/bible-cc:log-level [debug/info/warning]`：运行时切换 daemon 日志级别
- `/bible-cc:logs [--detections/--bible/--errors]`：查看 daemon 日志摘要
- `/bible-cc:config --sources`：每项标注来源

### F5b.4 — Integration Sanity Check（四组件集成验证）

| 属性 | 说明 |
|------|------|
| **理由** | Phase 3/4 的 integration tests 各自验证单组件与 BiBLE 的交互，但缺少一个验证四组件（daemon + MCP + hooks + commands）同时工作的轻量测试。这个 sanity check 不等同于 E2E（E2E 需要真实 Claude Code session），但比单组件 integration test 范围更宽——在进入 Phase 6 E2E 之前快速验证组件间协议一致性。 |
| **优先级** | P1 — 集成验证 |
| **依赖** | Phase 5a commands、Phase 4 MCP server、Phase 3 BiBLE client、daemon HTTP API |

实现：`tests/integration/test_sanity_four_components.py`
```
Test: Four-Component Integration Sanity
  1. Start daemon + BiBLE test server
  2. Simulate SessionStart hook → verify session created + context injected
  3. Simulate UserPromptSubmit + PostToolUse hooks → verify turns buffered
  4. Trigger Phase 1 detection (fast-path, stub LLM) → verify moment stored
  5. Call /bible-cc:review → verify moment visible
  6. Call /bible-cc:push → verify moment flushed to BiBLE
  7. Call MCP bible_memory_search → verify flushed moment searchable
  8. Call /bible-cc:diagnose → verify all 6 checks PASS
  9. Simulate SessionEnd hook → verify session completed
Expected: All 9 steps green, no component desync.
```

### F5b.5 — CI Pipeline 扩展

| 属性 | 说明 |
|------|------|
| **理由** | 将 5b 的诊断命令和 sanity check 接入 CI。 |
| **优先级** | P0 |
| **依赖** | Phase 5a CI、F5b.3-F5b.4 |

---

## Phase 5 验收标准

### 5a 验收

- [ ] `./scripts/dev.sh ci` 通过（含 command integration/contract tests）
- [ ] 7 个 MVP 命令全部可用，输出格式清晰
- [ ] 3 个高优先级命令（push, consult, review）可用
- [ ] review 命令支持 list、view detail、edit title/abstract、delete、force-flush
- [ ] `tests/contract/test_command_output.py` 通过

### 5b 验收

- [ ] `/bible-cc:diagnose` 一键 6 项全链路诊断
- [ ] `/bible-cc:log-level` 运行时切换生效
- [ ] `/bible-cc:logs` 支持 all/detections/bible/errors 过滤
- [ ] `/bible-cc:config --sources` 显示来源
- [ ] 所有故障场景有可见诊断信息和恢复路径（4+ 场景）
- [ ] `tests/integration/test_sanity_four_components.py` 通过（9 步全绿）
- [ ] 命令输出格式符合 `07-commands/specs.md`

---

## Phase 5 产出文件

```
commands/
├── status.md                   ← F5a.1, F5b.3 (--verbose)
├── check-bible.md              ← F5a.1
├── help.md                     ← F5a.1
├── config.md                   ← F5a.1, F5b.3 (--sources)
├── version.md                  ← F5a.1
├── context.md                  ← F5a.1
├── sessions.md                 ← F5a.1
├── push.md                     ← F5a.2
├── consult.md                  ← F5a.2
├── review.md                   ← F5a.3
├── diagnose.md                 ← F5b.3
├── log-level.md                ← F5b.3
├── logs.md                     ← F5b.3
├── config-set.md               ← F5b.1 (择取)
├── capture-pause.md            ← F5b.1 (择取)
├── capture-resume.md           ← F5b.1 (择取)
├── recover.md                  ← F5b.1 (择取)
├── token-usage.md              ← F5b.1 (择取)
└── ...                         ← F5b.1 (其余择取)
tests/
├── integration/
│   ├── test_commands.py        ← F5a.5
│   ├── test_hook_bridge.py     ← F5a.5
│   └── test_sanity_four_components.py ← F5b.4
└── contract/
    └── test_command_output.py  ← F5a.5
```
