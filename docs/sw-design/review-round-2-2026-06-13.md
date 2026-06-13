# SW Design Review — Round 2 (2026-06-13)

> 在 Round 1 修复 26 个 findings 后的第二次全面审查。
> 3 个 agent 并行扫描：L1/L2 一致性、L3 内部一致性、supporting docs 对齐。

---

## CRITICAL（实现前必须修复）

### R2-C1. `hook-flow.md` 引用不存在的列名

`05-capture/hook-flow.md` 伪 SQL 引用了 `timestamp` 和 `tool_calls` 列，但 `sqlite-schema.md` 的实际列名是 `created_at`、`tool_name`、`tool_arguments`、`tool_output`。

- Line 22: `INSERT INTO turns (session_id, seq, role, content, timestamp)`
- Line 82: `INSERT INTO turns (session_id, seq, role, content, tool_calls, timestamp)`

**影响**: 按 hook-flow.md 写 SQL 会运行时报错。

---

### R2-C2. `http-api.md` 仍用 `role='tool'`，违反 schema 约束

Round 1 C2 将 `sqlite-schema.md` 的 role 从 `'user' | 'tool'` 改为 `'user' | 'assistant'`，但 `03-daemon/http-api.md` §4.2 `/turn/tool` 内部流程中仍写 `role='tool'`。

三文件状态：
- `sqlite-schema.md`: `'user' | 'assistant'` ✅
- `hook-flow.md`: `"assistant"` ✅
- `http-api.md`: `'tool'` ❌

---

### R2-C3. 27 个 L3 交叉引用路径错误

所有 L3 文档用 `../../X.md` 引用同层 L2 文档，正确路径应为 `../X.md`。

例：`03-daemon/sqlite-schema.md` 写 `../../02-interfaces.md`，解析到 `docs/02-interfaces.md`（不存在），正确应为 `../02-interfaces.md`。

影响 13 个 L3 文件，共 27 个链接。

---

### R2-C4. `02-interfaces.md` health response 仍缺 `pid`/`port`

Round 1 C7 在 `http-api.md`（L3）中修复，但 `02-interfaces.md`（L1）§1.1 的 health response schema 仍未包含 `pid` 和 `port`。

---

### R2-C5. `port-conflict.md` 引用错误的故障编号

`03-daemon/port-conflict.md` line 144: 引用 `F2 端口冲突诊断路径`，但 `failure-paths.md` 中端口冲突是 **F1**，F2 是 "daemon 中途 crash"。

---

### R2-C6. `metrics` 表不在 sqlite-schema.md migration 中

`09-monitoring/data-collection.md` 定义了 `metrics` 表，`11-testing/unit.md` line 73 期待 "Migration creates monitoring tables"，但 `sqlite-schema.md` 的 MIGRATIONS 数组中无此表。

---

## HIGH（实现时会造成混淆）

### R2-H1. `04-config/schema.md` 状态标记为 "待编写" 但文件已存在

`04-config.md` line 144 标记 `04-config/schema.md` 为 "待编写"，实际文件已完整。应改为 "已完成"。

---

### R2-H2. `detection.md` Phase 2 prompt 措辞不准确

Phase 2 prompt line 113: `"Key moment types (same as mid-session)"`，但 Phase 2 只列了 DECISION + ACCOMPLISHMENT（不含 SESSION_START）。应为 "subset of mid-session types" 或明确注明排除项。

---

### R2-H3. `hook-flow.md` seq 分配方式自相矛盾

- Lines 22-27: step 2-3 用 `SELECT MAX(seq)+1`（SQL 层面）
- Lines 33-48 §1.3: 用内存计数器 `_threshold_state`
- `http-api.md` line 327: 用内存计数器 `session_seq[session_id] += 1`

应统一为内存计数器方式（与 http-api.md 和 startup.md 一致）。

---

### R2-H4. `upgrade.md` daemon stop timeout 5s vs `http-api.md` 10s

`upgrade.md` line 47: "等待优雅关闭（<=5s）"，但 `http-api.md` §7 写 `POST /daemon/stop` 最大延迟 ~10s。

---

## MEDIUM（支持文档与 SW design 不一致）

### R2-M1. 可行性报告 MCP 工具计数过时（7 vs 6+2）

可行性报告列出 7 个活跃工具（含 `bible_knowledge_list`），SW design 是 6 活跃 + 2 postponed。

### R2-M2. 可行性报告端点名 `/turn/assistant` vs `/turn/tool`

SW design 用的是 `/turn/tool`，可行性报告是 `/turn/assistant`。

### R2-M3. `command-priority-table.md` 有 4 个命令不在 specs.md 中

`memory-duplicates`, `memory-merge`, `team-search`, `share-memory` 在优先级表中但 specs.md 无定义。

### R2-M4. `command-priority-table.md` 内部计数错误

"低优先级（10 个）" 实际列出 17 项。

### R2-M5. CLAUDE.md 引用已删除的 `/daemon/notify`

Line 247: "The feasibility report's proposed POST /daemon/notify is not needed" — 该提议已从可行性报告中移除。

---

## LOW

### R2-L1. `01-architecture-overview.md` 组件图缺少 `help` 命令
### R2-L2. 可行性报告缺少 6 个新配置字段
### R2-L3. `status.md` 描述 `total_turns` 为 "当前 session" 但 SQL 是全局 `COUNT(*)`
### R2-L4. Phase 1 prompt 用 UPPER_CASE，stored values 用 lower_case

---

## 总结

| 级别 | Round 1 遗留 | Round 2 新发现 | 合计 |
|------|------------|--------------|------|
| CRITICAL | 0 | 6 | 6 |
| HIGH | 1 | 4 | 5 |
| MEDIUM | 0 | 5 | 5 |
| LOW | 4 | 4 | 8 |
