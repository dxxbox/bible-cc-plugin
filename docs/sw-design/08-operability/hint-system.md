# 08-operability/hint-system.md — 通知机制（L3）

> moment hint + error hint 的完整设计：触发条件、消息模板、format 选项、delivery 路径。

---

## 1. Delivery 路径

hint 通过 hook script 的 stdout 输出。两个到达位置：

| 到达位置 | 机制 | 用途 |
|----------|------|------|
| **conversation transcript** | `suppressOutput: false`（默认）→ stdout 内联显示在对话中 | 用户可见 |
| **system prompt** | `inject: true`（SessionStart hook）→ stdout 注入 prompt | 模型感知 |

SessionStart hook 的 error hint 双通道都有（transcript + system prompt），确保用户和模型同时知道 daemon 状态。

UserPromptSubmit/PostToolUse hooks 不设置 `inject: true`，hint 只出现在 transcript。

---

## 2. Moment Hint（Phase 1 检测通知）

### 2.1 触发条件

Phase 1 detection 检测到 key moment（非 "none"）且 content-hash 去重通过。

### 2.2 消息模板

4 种 format，由 `capture.hint_format` 控制：

| format | 模板 | 示例 |
|--------|------|------|
| `quote_with_command` | `⎿ ⏳ Captured: "{quote}" — {type}. /bible-cc:review to see pending moments.` | `⎿ ⏳ Captured: "PostgreSQL for auth" — Decision. /bible-cc:review` |
| `quote_only` | `⎿ ⏳ Captured: "{quote}" — {type}.` | `⎿ ⏳ Captured: "PostgreSQL for auth" — Decision.` |
| `command_only` | `⎿ ⏳ Key moment captured (turn {turn}). /bible-cc:review to see pending moments.` | `⎿ ⏳ Key moment captured (turn 5). /bible-cc:review` |
| `narrative` | `⎿ ⏳ Captured {type}: {narrative}` | `⎿ ⏳ Captured decision: PostgreSQL chosen for auth storage.` |

### 2.3 时序

hint 到达时间 = **检测完成时间**，不是 turn 发生的时间。由于 detection 是异步的，hint 通常在下一个 turn 出现。

---

## 3. Error Hint（故障通知）

### 3.1 触发条件

| 场景 | 触发点 | 级别 |
|------|--------|------|
| daemon 端口被占，启动失败 | SessionStart hook 脚本检测到 daemon 无响应 | ❌ error |
| daemon 中途 crash（mid-session） | UserPromptSubmit/PostToolUse hook 调 daemon 失败 + cooldown 标记文件不存在 | ⚠️ warning |
| BiBLE Atlas 不可达 | `/bible-cc:check-bible` 或 daemon health check 发现连通性失败 | ⚠️ warning |
| flush 失败（连续 N 次） | daemon 检测到连续 flush 失败 | ⚠️ warning |
| Phase 1/2 LLM 调用失败 | daemon 内部 log + 不产生用户 hint | —（内部日志） |

### 3.2 消息模板

```
⎿ ❌ bible-cc daemon failed to start on port 9777 (address in use).
    Run /bible-cc:status for details.

⎿ ⚠️ bible-cc daemon unreachable. Local capture paused.
    Run /bible-cc:status for details.

⎿ ⚠️ BiBLE Atlas unreachable (http://localhost:5555). Moments stay local until restored.
    Run /bible-cc:check-bible to verify.

⎿ ⚠️ bible-cc: flush to BiBLE Atlas failed 3 times. Moments accumulate locally.
    Run /bible-cc:sync-status for details, /bible-cc:retry-push to retry.
```

### 3.3 设计原则

1. **emoji 前缀区分级别**：`❌` = error，`⚠️` = warning，`✅` = success
2. **提供下一步命令**：每个 hint 包含一个可执行的 command
3. **不重复**：同一 session 内同一错误只 hint 一次（cooldown 300s）

---

## 4. 升级通知

```
⎿ ✅ bible-cc upgraded to v1.2.0 (schema v2). /bible-cc:changelog for details.
```

---

## 5. Hint 去重

```python
_hint_tracker: dict[str, float] = {}  # hint_type → last_hint_time

def should_hint(hint_type: str, cooldown_seconds: int = 300) -> bool:
    now = time.time()
    if hint_type not in _hint_tracker or now - _hint_tracker[hint_type] > cooldown_seconds:
        _hint_tracker[hint_type] = now
        return True
    return False
```

error 级别 hint cooldown 可缩短（用户需要尽快知道）。

> ⚠️ `_hint_tracker` 是内存 dict，daemon 重启后丢失——重启后首轮 hint 不享受去重。这是已知行为：daemon 持久运行，重启罕见；重启后如有重复 hint 属小概率事件，不引入文件持久化复杂度。

---

## 6. 参考文档

- [`../../02-interfaces.md`](../02-interfaces.md) — Hook stdout、`suppressOutput`、`inject`
- [`../08-operability.md`](../08-operability.md) — 全局约束
