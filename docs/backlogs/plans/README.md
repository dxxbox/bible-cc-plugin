# bible-cc-plugin 分阶段开发交付计划 — 总览

> **For agentic workers:** 每个 Phase 有独立的 plan 文件，见下方索引。各 Phase 之间的依赖关系见 §3。

**Goal:** 将 bible-cc-plugin 从 skeleton 状态交付为完整的 Claude Code plugin，实现 context recall + session capture + agent tools。

**Architecture:** 四组件模型（Daemon / MCP Server / Hooks / Commands），Python 3.10+ with `uv`，FastAPI + SQLite + Anthropic SDK。

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, SQLite (stdlib), httpx, Anthropic SDK, MCP Python SDK, Pydantic

**设计依据:** `docs/sw-design/` 下 27 个 L1/L2/L3 文件 + feasibility report + command-priority-table（38 commands 已筛选）。

---

## 0. TDD & Continuous Delivery 原则

以下原则约束所有 Phase 的 feature 排序和交付节奏。

### TDD（测试驱动开发）

1. **测试先行**：每个 feature 必须先写失败测试，再写实现代码。Phase 内 feature 顺序反映这一点——测试 feature 排在实现 feature 之前。
2. **Red-Green-Refactor**：每个 feature 的验收标准包含三步：test FAIL → test PASS → refactor（保持 green）。
3. **契约测试（Contract Tests）**：跨组件接口（hook↔daemon HTTP、daemon↔BiBLE API、MCP↔BiBLE API、command→daemon）必须有独立的契约测试，验证请求/响应 schema、错误码、header。契约测试不依赖实现细节，只验证接口协议。
4. **结构断言优先**：LLM 相关输出不做精确文本断言，只断言 JSON schema、moment type、必要字段。
5. **测试金字塔**：Unit (70%) > Integration (25%) > E2E (5%)。每个 Phase 的测试比例遵循此原则。

### Continuous Delivery（持续交付）

1. **每个 Phase 产出可工作软件**：Phase 结束 = daemon 可启动 + 该 Phase 功能可用 + 所有测试 green + CI green。不积压 WIP 到下一 Phase。
2. **CI 从 Phase 0 开始**：`./scripts/dev.sh ci`（lint → unit test → integration test）从第一天就存在。每个 Phase 扩展 CI 覆盖范围，而不是到 Phase 6 才搭建。
3. **持续集成**：每个 feature commit 后跑完整 CI。不等到 Phase 结束。
4. **Trunk-based**：直接在 main branch 上开发，不使用 long-lived feature branch。每个 commit 必须 green。
5. **Walking Skeleton 优先**：Phase 0 就有一条可部署的瘦通路（daemon 启动 → health check → CI green）。后续 Phase 逐步充实。
6. **粗粒度提交**：每完成一个 feature 提交一次（≈ 1-3 commits/day），不是每个小改动都提交。

### CI 成熟度演进

| Phase | CI 覆盖 | 新增 |
|-------|---------|------|
| **0** | lint + unit test（config） | CI 骨架、ruff、pytest |
| **1** | lint + unit test（config, buffer, types） | buffer 单元测试、schema 契约测试 |
| **2** | + unit test（detector） | detector 单元测试、hook↔daemon 契约测试 |
| **3** | + integration test（client, flush, concurrency） | BiBLE test server 集成测试、daemon↔BiBLE 契约测试 |
| **4** | + integration test（MCP server, consult） | MCP 工具集成测试、MCP↔BiBLE 契约测试 |
| **5** | + integration test（commands, hook bridge） | command 输出契约测试 |
| **6** | + E2E test（full journeys） | E2E 完整链路、CI --debug mode |

---

## 1. Phase 总览

| Phase | 文件 | 目标 | 预估 | 可演示 |
|-------|------|------|------|--------|
| **0** | [`2026-06-13-phase-0-scaffold.md`](2026-06-13-phase-0-scaffold.md) | **1st Call：Install & Run** — Setup wizard、daemon lifecycle（start/stop/status/restart）、health check、config 系统、CI 骨架 | 2-3d | 安装 → 配置 → daemon 启动 → health green → 停止/重启 |
| **1** | [`2026-06-13-phase-1-daemon-core.md`](2026-06-13-phase-1-daemon-core.md) | Daemon 数据层：SQLite schema（WAL）、完整 HTTP API（15 端点）、session/turn 管理、context injection | 5-7d | Session 创建、turn 缓冲、本地 context 注入，全链路 request-id 追踪 |
| **2** | [`2026-06-13-phase-2-capture-pipeline.md`](2026-06-13-phase-2-capture-pipeline.md) | 采集管线：Hook bridge、Phase 1/2 moment detection、dedup、hint system | 5-7d | Key moment 自动检测、去重、用户可见 hint，每步可追溯 |
| **3** | [`2026-06-13-phase-3-bible-integration.md`](2026-06-13-phase-3-bible-integration.md) | BiBLE 集成：HTTP client、flush 链路、graceful degradation | 4-5d | Moment 推送到 BiBLE Atlas，断连不 crash，请求全追踪 |
| **4** | [`2026-06-13-phase-4-recall-pipeline.md`](2026-06-13-phase-4-recall-pipeline.md) | 回忆管线：MCP server（6 tools）、consult 跨域搜索 | 4-5d | 模型通过 MCP 搜索 BiBLE，用户跨域查询 |
| **5** | [`2026-06-13-phase-5-commands-operability.md`](2026-06-13-phase-5-commands-operability.md) | **5a** 核心命令（MVP + 高优先级 10 个）+ **5b** 运维诊断（中优先级 8 个 + diagnose + log-level + 集成 sanity check） | 3-4d + 2-3d | 完整命令集、一键全链路诊断、四组件集成验证 |
| **6** | [`2026-06-13-phase-6-deployment-e2e.md`](2026-06-13-phase-6-deployment-e2e.md) | E2E 验证 + Ship：E2E 测试（4 旅程）、CI 成熟化、监控数据采集、README 故障排查 | 4-5d | E2E 全绿，CI 完整流水线，README 完备 |

**总计预估: 29-39 天**

---

## 2. 阶段依赖图

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5 ──► Phase 6
                                    │                         │
                                    └──► Phase 4 (可并行) ◄───┘
                                         (Phase 2 完成后可与 Phase 3 并行)
```

**严格串行**: Phase 0 → 1 → 2（types → config → SQLite → HTTP API → hooks → detection，每一层都依赖前一层）

**可部分并行**: Phase 3（BiBLE client + flush）和 Phase 4（MCP + consult）都依赖 Phase 2 完成后的 buffer + client，但彼此不直接依赖。Phase 3 完成后 Phase 4 的 MCP 工具才能端到端验证（需要 BiBLE 里有数据）。

---

## 3. 关键依赖链

| 依赖链 | 为什么不可并行 |
|--------|---------------|
| `types.py` → 所有组件 | 没有类型定义，所有组件不知道数据结构 |
| `config.py` → daemon, MCP | daemon 和 MCP 都需要读配置才能启动 |
| SQLite schema → HTTP API | HTTP API 每个端点都读写 SQLite |
| HTTP API → hook bridge | Hook 脚本通过 HTTP 调 daemon 端点 |
| hook bridge → detection | Detection 需要 hook 喂数据进 buffer |
| buffer (turns) → detection | Detection 从 buffer 读 turns |
| `client.py` → flush, MCP tools | Flush 和 MCP 都通过 client 调 BiBLE |
| detection → hints | Hint 显示检测到的 moment |
| HTTP API → commands | 所有命令通过 HTTP 调 daemon |
| 全部 → E2E | E2E 贯穿完整链路 |

---

## 4. 各 Phase 交付后能力

### Phase 0 交付后 — 1st Call 可用

```
用户已经可以：
  ✅ 通过 Claude Code marketplace（或 git clone）安装 bible-cc-plugin
  ✅ 运行 setup wizard 交互式配置 BiBLE Atlas URL
  ✅ 启动 daemon（daemon start）
  ✅ 查看 daemon 状态（daemon status → running/not running + pid + port + uptime）
  ✅ 停止 daemon（daemon stop）
  ✅ 重启 daemon（daemon restart）
  ✅ Daemon 响应 health check（GET /daemon/health → 真实 pid/port/uptime）
  ✅ 查看每个配置项的来源（config debug trace）
  ✅ CI pipeline green（lint + unit test + contract test）

  还不能做的事：
  ❌ 追踪 Claude Code session（Phase 1）
  ❌ 检测 key moments（Phase 2）
  ❌ 推送数据到 BiBLE Atlas（Phase 3）
  ❌ MCP 搜索工具（Phase 4）
  ❌ 用户 slash commands（Phase 5）
```

### Phase 1 交付后 — 本地数据层可用

在 Phase 0 基础上新增：
```
  ✅ Session 生命周期管理（/session/start → 创建 session → /session/end → 标记完成）
  ✅ Turn 缓冲（用户的每条消息 + 模型的每个 tool call 都存入 SQLite）
  ✅ 本地 Context 注入（SessionStart 时从 SQLite 注入 turns + moments 摘要到 system prompt）
  ✅ 三种注入场景自动识别（新 session / /clear 或 compact / crash recovery）
  ✅ Crash recovery 扫描（daemon 启动时发现未关闭的 session 并恢复数据）
  ✅ SQLite WAL mode（并发写入不冲突）
  ✅ 完整的 daemon HTTP API（15 个端点，见 02-interfaces.md）
  ✅ 全链路 request-id 追踪（每个 HTTP 请求带 X-Request-ID）
  ✅ SQLite 内省 debug API（查看 schema、表内容、turns）
  ✅ 启动序列 6 步诊断日志（每步 + timing）
  ✅ 端口冲突时产生可见 error 信息

  还不能做的事：
  ❌ Moment detection（Phase 2）
  ❌ 任何 BiBLE Atlas 通信（Phase 3）
  ❌ 跨 session 知识检索（Phase 4）
```

### Phase 2 交付后 — 记忆采集可用

在 Phase 1 基础上新增：
```
  ✅ Hook bridge 就绪（SessionStart/UserPromptSubmit/PostToolUse/Stop → daemon）
  ✅ Phase 1 实时 moment detection（LLM 自动识别 decisions + accomplishments）
  ✅ 阈值触发（累计 8 turns 或 16000 chars → 自动触发检测，不浪费 API 调用）
  ✅ Phase 2 回顾式 detection（session 结束时用全量上下文做 synthesis + gap-fill）
  ✅ Content-hash 两层去重（同一 moment 不会重复存储）
  ✅ Hint 通知（moment 检测到时在 Claude Code transcript 中显示提示）
  ✅ 四种 hint format（quote_with_command / quote_only / command_only / narrative）
  ✅ Graceful degradation（daemon 不可达时 hook 静默跳过，Claude Code turn 不失败）
  ✅ Detection 全链路追踪（trigger reason → prompt stats → LLM latency → dedup result）
  ✅ Detection debug API（查看 detection 历史 + 累计统计）
  ✅ Hook 执行追踪（每个 hook action 的 endpoint + status + duration）

  还不能做的事：
  ❌ Moment 推送到 BiBLE Atlas——moments 只存本地 SQLite（Phase 3）
  ❌ MCP 工具搜索 BiBLE（Phase 4）
  ❌ review/push 命令——moments 只能通过 debug API 查看（Phase 5）
```

### Phase 3 交付后 — BiBLE 通信可用

在 Phase 2 基础上新增：
```
  ✅ Moment flush 到 BiBLE Atlas（本地 moments → bundle → POST import → update flushed=1）
  ✅ 自动 flush（session 结束时自动推送所有 moments）
  ✅ 手动 flush（/daemon/session/flush 端点，供 push 命令使用）
  ✅ Mid-session upload（config 开启时 Phase 1 检测到 moment 立即推送）
  ✅ BiBLE V4 API 完整封装（import memory、search memory/knowledge/skill、get memory/skill、task status）
  ✅ BiBLE 连通性检查（health check 中的 bible_connectivity: {reachable, latency_ms}）
  ✅ 连通性三级分解诊断（DNS → TCP → HTTP，精确定位断连原因）
  ✅ Graceful degradation（BiBLE 不可达时 flush 不丢数据，moments 保持 flushed=0）
  ✅ BiBLE API 请求全追踪（每个 request 的 method + URL + status + latency）
  ✅ Flush 诊断日志（每步：bundle → import → update）
  ✅ Debug API（flush 历史 + BiBLE request 历史）

  还不能做的事：
  ❌ 模型通过 MCP 主动搜索 BiBLE（Phase 4）
  ❌ 用户通过命令搜索 BiBLE（Phase 4 consult、Phase 5 命令）
  ❌ review 管理 pending moments（Phase 5）
```

### Phase 4 交付后 — 跨 Session 知识检索可用

在 Phase 3 基础上新增：
```
  ✅ 模型可通过 MCP 工具主动搜索 BiBLE Atlas
     - bible_memory_search("query") → 返回相关记忆
     - bible_memory_save(title, content) → 保存新记忆
     - bible_memory_get(id) → 获取完整记忆
     - bible_knowledge_search("query") → 返回相关知识
     - bible_skill_search("query") → 返回相关技能
     - bible_skill_get(id) → 获取完整技能定义
  ✅ 用户可通过 /daemon/consult 跨域搜索（memory + knowledge + skill 并行）
  ✅ Consult 自动 query 生成（query 为空时 LLM 归纳对话 → 生成搜索词）
  ✅ MCP 调用全追踪（tool name + args + result count + latency）
  ✅ Consult 查询分解日志（每域搜索耗时、合并结果）
  ✅ MCP server 启动诊断（注册工具列表 + BiBLE 连通性检查）

  还不能做的事：
  ❌ 用户友好的 slash commands（Phase 5）
  ❌ 一键全链路诊断（Phase 5）
  ❌ review/push 等管理命令（Phase 5）
```

### Phase 5 交付后 — 用户可自助操作

在 Phase 4 基础上新增：

**Phase 5a — 核心命令：**
```
  ✅ 7 个 MVP 命令（status, check-bible, help, config, version, context, sessions）
  ✅ 3 个高优先级命令（push, consult, review）
  ✅ review 命令完整行为：查看 pending moments 列表（带 turn 溯源）、编辑 title/abstract、删除、force-flush
```

**Phase 5b — 运维诊断：**
```
  ✅ 一键全链路诊断（/bible-cc:diagnose → 6 项检查，每项 PASS/FAIL + 诊断建议）
  ✅ 运行时日志级别切换（/bible-cc:log-level debug/info/warning）
  ✅ 日志查看（/bible-cc:logs --detections/--bible/--errors）
  ✅ /bible-cc:status --verbose（config sources + recent detections + recent BiBLE requests）
  ✅ /bible-cc:config --sources（每项标注来源）
  ✅ 所有故障场景有诊断路径 + 恢复操作（端口冲突、BiBLE 断连、hook 失败、crash）
  ✅ 四组件集成 sanity check（daemon + MCP + hooks + commands 联合验证，9 步全绿）
```

### Phase 6 交付后 — 生产就绪

在 Phase 5 基础上新增：
```
  ✅ CI 完整流水线（lint → unit → contract → integration → E2E → HTML report）
  ✅ CI --debug mode（失败自动 dump 诊断）
  ✅ E2E 测试覆盖 4 个关键用户旅程
  ✅ Token 用量 + 性能数据采集（随 push 上报到 BiBLE Atlas）
  ✅ README 故障排查章节（5 个常见场景的完整诊断步骤）
  ✅ 升级/卸载流程可操作
  ✅ 可发布到 Claude Code Marketplace
```

---

## 5. 里程碑

| 里程碑 | Phase | CI 状态 | 可演示功能 |
|--------|-------|---------|-----------|
| **M0 — 1st Call** | 0 | lint + unit test + contract test green | 安装 → setup wizard → daemon start → health green → stop → restart |
| **M1 — Data Layer** | 1 | + unit test green | Session 创建、turn 缓冲、本地 context 注入 |
| **M2 — Capture Works** | 2 | + detector unit test green | Moment 检测、去重、hint 通知 |
| **M3 — Data Flows to BiBLE** | 3 | + integration test green | Flush 到 BiBLE Atlas（**可与 M4 并行开发**） |
| **M4 — Recall Works** | 4 | + MCP integration test green | MCP 工具搜索 BiBLE（**可与 M3 并行开发**） |
| **M5a — User Commands** | 5a | + command integration test green | 10 个核心命令可用 |
| **M5b — Operability** | 5b | + sanity check green | 一键诊断、日志控制、四组件集成验证 |
| **M6 — Ship** | 6 | + E2E test green | E2E 全绿、CI 完整流水线、README 完备 |

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Anthropic SDK API 变更 | Phase 2 detection | SDK 版本 pin 在 pyproject.toml |
| MCP Python SDK 不成熟 | Phase 4 阻塞 | fallback：stdio JSON-RPC 直接实现 |
| BiBLE Atlas V4 API 变更 | Phase 3/4 | client.py 是单一适配层 |
| Claude Code plugin 机制变更 | 全 Phase | hook/command/mcp 是公开契约，backward compat 预期高 |
| 单人力开发 | 持续时间长 | Phase 3/4 可部分并行 |

## 7. 假设

1. 开发者有 Anthropic API key（moment detection LLM 调用）
2. 开发者有可用 BiBLE Atlas 实例（本地 test mode 或 team server）
3. Python 3.10+ 和 `uv` 已安装
4. Claude Code 版本支持 plugin 机制

---

## 8. 后续迭代（Post-V1）

以下功能设计已完成但不纳入 V1：

- **团队功能**: team-search, share-memory, team-activity, handoff
- **记忆管理**: memory-duplicates, memory-merge, memory-tag, memory-graph, memory-timeline
- **隐私合规**: privacy-audit, redact, forget-project, forget-session, data-request
- **高级采集**: bypass-add, capture-mode, project-switch, project-context
- **MCP postponed**: bible_memory_delete, bible_knowledge_list（待服务端确认）

详见 command-priority-table 中「中优先级」和「低优先级」部分。
