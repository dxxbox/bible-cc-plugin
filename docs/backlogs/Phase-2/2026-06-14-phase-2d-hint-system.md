# Phase 2d: Hint System 端到端 + Commands 落地

> **依赖**: Phase 2b（Phase 1 detection 产生 moment → 生成 hint）、Phase 2c（/moments CRUD 端点就绪）
> **被依赖**: Phase 3（flush 到 BiBLE 时 hint 通知用户 flush 结果）
> **父文档**: [Phase 2 总览](../plans/2026-06-13-phase-2-capture-pipeline.md)

**交付 Command**: `/bible-cc:review`（command markdown 落地）、`/bible-cc:diagnose`（扩展）

**预估: 1.5 天**

### 测试标注

2d.1-2d.2（hint 传递 + 四种 format）默认 `[Unit] [Pre]`。2d.3（error hints）默认 `[Unit] [Pre]`。2d.4（command 文件）默认 `[Contract] [Post]`。

---

## 4. Sub-Phase 2d: Hint System + Commands（1.5d）

### Scenario

> 用户在对话中——几分钟前的一段对话中，Phase 1 detection 在后台检测到了一个 key moment（"PostgreSQL for auth storage" — Decision）。Moment 已存入 SQLite moments 表。
>
> 现在用户输入新消息 → UserPromptSubmit hook 触发 → `POST /turn/user` → hook 调 `GET /daemon/moments?session_id=X` → 发现新 moment → `format_hint()` → print 到 stdout → Claude Code 捕获 stdout → 在 transcript 中展示 `"⎿ ⏳ Captured: \"PostgreSQL for auth storage\" — Decision. /bible-cc:review"`。
>
> 用户看到了 hint，意识到这个 moment 被捕获了。输入 `/bible-cc:review` → 看到 pending moments 列表 → 可以编辑或删除。输入 `/bible-cc:diagnose` → 看到 daemon 健康状态 + detection 统计。

### 实现顺序

```
2d.2 (format_hint) → 2d.1 (hook 集成 _print_hints)
2d.3 (error hints)──────────────────────────────┘
                        ↓
                   2d.4 (command 文件落地)
```

| 顺序 | Feature | 理由 | 可并行 |
|------|---------|------|--------|
| **1st** | 2d.2 format_hint + 2d.3 error hints | 纯函数——`bible_cc_plugin/hint_system.py`，无外部依赖 | ✅ 两者可并行 |
| **2nd** | 2d.1 hook 集成 | 修改 2a.1 的 turn handler，插入 `_print_hints()`。依赖 2d.2（format_hint） | — |
| **3rd** | 2d.4 command 文件 | 独立——markdown 文件 + contract test。依赖 2c.3（PUT/DELETE 端点）和 1d（diagnose 基础） | ✅ 可与 2d.1 并行 |

---

### Feature 2d.1: Hint 传递机制（hook 读 GET /daemon/moments → format_hint → stdout）

**Scenario**: 修改 2a.1 的 `_handle_turn_user()` 和 `_handle_turn_tool()`——在 POST /turn 成功后，插入 `_print_hints()` 调用：`GET /daemon/moments?session_id=X` → 对每个 moment 调 `format_hint()` → print 到 stdout。

| 属性 | 说明 |
|------|------|
| **理由** | Async detection 的结果持久化在 SQLite moments 表。Hook 是每 turn 执行一次的独立进程——在 turn 写入后主动查询 moments 表获取新的 detection 结果并输出 hint。 |
| **优先级** | P0 — 用户感知 detection 的唯一通道 |
| **依赖** | 2a.4（GET /daemon/moments）、2a.1（hook.py `_handle_turn_user`/`_handle_turn_tool` 已完成）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `08-operability/hint-system.md` | §2 通知通道 | hook stdout 是唯一通知通道，出现在 transcript inline |
| `CLAUDE.md` | Hint Notification | "hint arrives on a subsequent turn because detection is async" |

**Function-Level Steps**（按实现顺序）:

```python
# scripts/hook.py — turn-user/tool 处理中增加 hint 拉取

def _print_hints(session_id: str, base_url: str) -> None:
    """调 GET /daemon/moments?session_id=X → format_hint → stdout。"""
    try:
        r = _local_client().get(f"{base_url}/daemon/moments", params={"session_id": session_id})
        r.raise_for_status()
        moments = r.json().get("moments", [])
        for m in moments:
            hint = format_hint(m, _config.capture.hint_format)  # 2d.2 实现
            print(hint, flush=True)  # stdout → CC transcript
    except Exception:
        pass  # best-effort——hint 失败不影响 turn flow
```

**交付标准**:

- [ ] Hook turn-user/tool 后调 `GET /daemon/moments` 获取 moments
- [ ] 对 moments 调用 `format_hint()` 后 print 到 stdout
- [ ] Hint 是 best-effort——失败不阻塞 turn flow
- [ ] 特殊字符 sanitization

---

### Feature 2d.2: 四种 Hint Format

**Scenario**: `format_hint(moment, format_mode)` 根据 config 的 `capture.hint_format` 生成 hint 字符串。

| 属性 | 说明 |
|------|------|
| **理由** | 不同用户偏好不同信息密度。开发者喜欢 `narrative`，产品经理喜欢 `quote_with_command`。 |
| **优先级** | P1 |
| **依赖** | 2a.2（config capture.hint_format）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `CLAUDE.md` | Moment Detection Design §Hint | 四种 format 定义和示例 |
| `08-operability/hint-system.md` | §4 | hint format 模板 |

**Function-Level Steps**（按实现顺序）:

```python
# bible_cc_plugin/hint_system.py — 新建模块（与 config.py 同级，hook.py 和 daemon 都可 import）

def format_hint(moment: MomentCandidate, format_mode: str) -> str:
    """四种 hint format。
    quote_with_command: '⎿ ⏳ Captured: "PostgreSQL for auth" — Decision. /bible-cc:review'
    quote_only:         '⎿ ⏳ Captured: "PostgreSQL for auth" — Decision.'
    command_only:       '⎿ ⏳ Key moment captured (Decision). /bible-cc:review'
    narrative:          '⎿ ⏳ Captured decision: PostgreSQL for auth. Postgres chosen over SQLite for concurrent writes.'
    """
    prefix = "⎿ ⏳"
    label = {"decision": "Decision", "accomplishment": "Accomplishment",
             "session_start": "Session Start"}.get(moment.type, moment.type)
    
    if format_mode == "quote_with_command":
        return f'{prefix} Captured: "{moment.title}" — {label}. /bible-cc:review'
    elif format_mode == "quote_only":
        return f'{prefix} Captured: "{moment.title}" — {label}.'
    elif format_mode == "command_only":
        return f'{prefix} Key moment captured ({label}). /bible-cc:review'
    elif format_mode == "narrative":
        summary = moment.narrative[:200]
        return f'{prefix} Captured {moment.type}: {moment.title}. {summary}'
    return f'{prefix} Moment captured: {moment.title}'
```

**交付标准**:

- [ ] 四种 format 全部实现，输出与 CLAUDE.md 示例一致
- [ ] `narrative` format 限制 ≤ 200 chars
- [ ] 特殊字符 escape
- [ ] Unit tests 每种 format 一个

**测试用例**（先于实现编写）:

- [ ] `[Unit] [Pre]` `test_format_hint_quote_with_command` — 含 quote + command
- [ ] `[Unit] [Pre]` `test_format_hint_quote_only` — 含 quote，无 command
- [ ] `[Unit] [Pre]` `test_format_hint_command_only` — 含 command，无 quote
- [ ] `[Unit] [Pre]` `test_format_hint_narrative` — 含 narrative，≤ 200 chars
- [ ] `[Unit] [Pre]` `test_format_hint_sanitizes_special_chars` — title 含 `"` → escape

*意图测试*:
- [ ] `[Unit] [Pre]` `test_hint_always_includes_enough_context` — **意图: 自包含**。所有 format 至少含 type + title。

---

### Feature 2d.3: Error Hints

**Scenario**: 故障通知分两条路径：

| 故障 | 发生位置 | 通知路径 |
|------|---------|---------|
| 端口冲突 | `daemon_launcher.py`，daemon 启动失败 | `ensure_daemon_started()` 返回 False → hook.py 调 `format_error_hint()` → stderr。hook 侧自行处理，不依赖 daemon |
| LLM 调用失败 | `detector.py` worker 中 | 写入 `daemon.log`。Detection 是异步的，hook 无法实时感知。用户通过 `GET /daemon/debug/detections/stats` 查看失败率 |

`format_error_hint()` 是 hook 侧工具函数，与 `format_hint()` 放在同一模块（`bible_cc_plugin/hint_system.py`）。

| 属性 | 说明 |
|------|------|
| **理由** | Phase 0 教训 #1（silent failure is worst failure）。故障必须可见。 |
| **优先级** | P1 |
| **依赖** | 2d.2（format_hint 同模块）、1d.1（端口冲突检测）|

**Function-Level Steps**:

```python
# bible_cc_plugin/hint_system.py

def format_error_hint(error_type: str, detail: str) -> str:
    """构建 error hint（hook 侧调用）。
    端口冲突: '❌ bible-cc daemon failed to start on port 9777 (occupied by pid 1234 / python3.12).'
    """
```

**交付标准**:

- [ ] 端口冲突 → hook 侧 `format_error_hint()` → stderr（含 pid + process name）
- [ ] LLM 调用失败 → `daemon.log` WARNING（不改 hook 行为）
- [ ] Error hints 使用 `❌` 前缀（区别于 moment `⎿ ⏳`）

---

### Feature 2d.4: `/review` Command 落地 + `/diagnose` 扩展

**Scenario**: Command markdown 文件落地为可工作的 slash command。

| 属性 | 说明 |
|------|------|
| **理由** | Phase 2c 提供了端点但用户入口不存在。Diagnose 扩展让用户一览 detection 健康状态。 |
| **优先级** | P1 |
| **依赖** | 2c.3（/moments 端点）、1d（diagnose 基础）|

**Function-Level Steps**:

```markdown
# commands/review.md
/bible-cc:review — 查看 pending moments。
调 GET /daemon/moments?session_id={current} → type/title/detected_at 表格。
```

```markdown
# commands/diagnose.md（扩展 Phase 1d）
detection:  PASS  phase1=3, phase2=1, dedup_hits=1, avg_latency=1.2s
```

**交付标准**:

- [ ] `commands/review.md` 文件存在且可工作
- [ ] `commands/diagnose.md` 含 detection 统计
- [ ] Review 输出 pending moments 列表（type, title, detected_at）

**测试用例**（实现后编写——Contract tests）:

- [ ] `[Contract] [Post]` `test_review_command_lists_pending_moments` — 2 pending moments → 输出含两者
- [ ] `[Contract] [Post]` `test_diagnose_command_shows_detection_stats` — detection 历史 → 输出 phase1_count

---

## 2d 验收标准

- [ ] Hook 调 GET /daemon/moments → format_hint → stdout 端到端可追踪
- [ ] 四种 hint_format 全部正确输出
- [ ] Error hints 端口冲突 + LLM 失败场景可用
- [ ] `commands/review.md` 落地为可工作 slash command
- [ ] `commands/diagnose.md` 含 detection 统计
- [ ] `tests/unit/test_hint_system.py` 全部通过
- [ ] Hint 不影响 turn flow（best-effort）
