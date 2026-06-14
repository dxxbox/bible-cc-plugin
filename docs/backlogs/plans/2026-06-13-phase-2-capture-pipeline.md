# Phase 2: Capture Pipeline（Moment Detection）

> **For agentic workers:** Phase 2 是 plugin 的核心价值——moment detection。完成后的 plugin 可以实时检测 key moments，去重，并通过 hint 通知用户。

**Goal:** Hook bridge 脚本补全、Phase 1 mid-session detection（LLM 异步）、Phase 2 retrospective detection（session end LLM 同步）、两层去重、hint system。

**Architecture:** Hook shell scripts → daemon HTTP API → async LLM worker (asyncio.Queue) → SQLite moments table → hook stdout hints。

**Tech Stack:** Anthropic SDK, asyncio, FastAPI lifespan/background tasks

**预估: 6.5 天**

---

## Sub-Phase 总览 + 依赖关系

```
Phase 1d ──► 2a ──► 2b ──► 2c ──► 2d ──► Phase 3
              │      │      │      │
              ▼      ▼      ▼      ▼
           sessions  (none)  review  diagnose
           context          (review)
```

| 子 Phase | 文件 | 内容 | 交付 Command | 依赖 | 预估 |
|----------|------|------|-------------|------|------|
| **2a** | [`2026-06-14-phase-2a-hook-bridge-config.md`](../Phase-2/2026-06-14-phase-2a-hook-bridge-config.md) | Hook bridge 四个 action 补全 + config capture/detection 域 + 消除 subprocess spawning 重复 + command 文件落地 | `/bible-cc:sessions`、`/bible-cc:context` | Phase 1d | 1.5d |
| **2b** | [`2026-06-14-phase-2b-phase1-detection.md`](../Phase-2/2026-06-14-phase-2b-phase1-detection.md) | Anthropic client wrapper + asyncio.Queue 后台 worker + Phase 1 mid-session detection + 阈值触发 + stub LLM test mode | 无新 command（detection 通过 hint 感知）| **2a** | 2d |
| **2c** | [`2026-06-14-phase-2c-phase2-retrospective.md`](../Phase-2/2026-06-14-phase-2c-phase2-retrospective.md) | Phase 2 retrospective detection + /session/end 增强 + /moments CRUD 端点 | `/bible-cc:review`（端点）| **2b** | 1.5d |
| **2d** | [`2026-06-14-phase-2d-hint-system.md`](../Phase-2/2026-06-14-phase-2d-hint-system.md) | Hint system 端到端（hook 读 GET /daemon/moments + format_hint + stdout + 四种 format + error hints）+ /review command 落地 + /diagnose 扩展 | `/bible-cc:review`（落地）、`/bible-cc:diagnose`（扩展）| **2b/2c** | 1.5d |

### 关键依赖链

| 依赖 | 为什么 |
|------|--------|
| Phase 1d → 2a | 2a 的 hook bridge 调用 Phase 1 的全部 HTTP 端点 |
| 2a → 2b | 2b 的 detection 需要 hook 喂数据进 buffer；需要 config capture 域判断 `capture.enabled` |
| 2b → 2c | 2c 的 Phase 2 retrospective 需要 Phase 1 已知 moments 列表做 prompt injection |
| 2b/2c → 2d | 2d 的 hint system 需要 detection 产生 moment；/review 需要 /moments CRUD 端点 |

---

## Phase 2 验收总览

- [ ] `./scripts/dev.sh ci` 通过（lint + unit test + contract test，detector 使用 stub LLM）
- [ ] Hook 脚本四个 action 均可正确调用 daemon 端点
- [ ] `tests/contract/test_hook_daemon.py` 通过：每个 action 验证 HTTP 交互 + graceful skip
- [ ] Hook 每步执行输出 stderr 追踪日志（action + endpoint + status + duration）
- [ ] SessionStart hook self-contained（daemon 不在时先 start → register → inject）
- [ ] UserPromptSubmit/PostToolUse hook daemon 不可达时静默跳过（exit code 0，stderr WARN）
- [ ] `capture.enabled=false` 时不触发任何检测（跳过阈值检查 + 不 queue worker）
- [ ] Phase 1 detection 异步执行，不阻塞 `/turn/*` 端点（return immediately, <100ms）
- [ ] asyncio.Queue worker 有 try/except + restart loop（worker 崩溃不挂 daemon）
- [ ] Detection 每步输出 stderr 日志（trigger reason, prompt stats, API latency, result, dedup）
- [ ] `GET /daemon/debug/detections?session_id=X` 返回 detection 历史
- [ ] `GET /daemon/debug/detections/stats` 返回累计统计
- [ ] 阈值触发正确（turn count 和 char count 先到达者触发）
- [ ] Content-hash dedup 生效（同一 moment 重复 INSERT 不报错也不产生重复行）
- [ ] Phase 2 prompt 包含 Phase 1 已知 moments，LLM 不重复报告
- [ ] Phase 2 detection LLM 调用失败时 session 仍然标记 completed（不阻塞）
- [ ] `/session/end` 异步处理 Phase 2 detection，立即返回（<100ms）
- [ ] Hint 四种 format 均输出到 hook stdout，内容正确
- [ ] Hook 通过 `GET /daemon/moments` 读取 detection 结果 + format_hint + stdout 完成端到端 hint 传递
- [ ] `/bible-cc:review` 可查看/编辑/删除 pending moments
- [ ] 所有 command markdown 文件落地（sessions.md, context.md, review.md）
- [ ] 单元测试全部通过（stub LLM）
- [ ] 阈值内存计数器在 `/clear` 和 `/compact` 时正确重置

---

## 产出文件

```
src/bible_cc_plugin/
├── daemon/
│   ├── detector.py             ← 2b (Anthropic client wrapper + Phase 1/2 detection)
│   ├── daemon_launcher.py       ← 2a (共享 ensure_daemon_started)
│   └── server.py               ← 修改: asyncio.Queue worker, /turn/* 增强, /session/end 异步增强,
│                                      GET /daemon/moments (2a), PUT/DELETE /daemon/moments (2c)
├── config.py                   ← 2a (调整已有 CaptureConfig/DetectionConfig)
scripts/
├── hook.py                     ← 2a (四个 action 全面补全 + GET /daemon/moments → hint stdout)
├── daemon.py                   ← 2a (改用 daemon_launcher.py 消除重复)
commands/
├── sessions.md                 ← 2a (从占位落地)
├── context.md                  ← 2a (从占位落地)
├── review.md                   ← 2d (新增)
├── diagnose.md                 ← 2d (扩展)
src/bible_cc_plugin/
├── hint_system.py               ← 2d (format_hint + format_error_hint，hook 和 daemon 共享)
tests/unit/
├── test_detector.py            ← 2b/2c (prompt construction, threshold, dedup, stub LLM)
├── test_hint_system.py         ← 2d (四种 hint_format, error hints)
tests/contract/
├── test_hook_daemon.py          ← 2a (hook↔daemon HTTP 交互 + graceful degradation)
```

---

## 设计依据

- `docs/sw-design/05-capture-pipeline.md` — 采集管线 L2 总览
- `docs/sw-design/05-capture/hook-flow.md` — hook → buffer 数据流
- `docs/sw-design/05-capture/detection.md` — Phase 1/2 detection 详细设计
- `docs/sw-design/08-operability/hint-system.md` — hint 通知系统
- `docs/sw-design/03-daemon/http-api.md` — daemon HTTP API spec
- `docs/bible-claude-code-plugin-feasibility-report.md`
- `CLAUDE.md` — Moment Detection Design, Dedup Strategy, Graceful Degradation
