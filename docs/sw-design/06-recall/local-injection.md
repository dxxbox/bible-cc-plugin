# 06-recall/local-injection.md — SessionStart 本地注入（L3）

> `/context/inject` 在三种场景下的分支逻辑：全新 session、`/clear`/compact、crash recovery。

---

## 1. 三场景分支

```
POST /context/inject {session_id, user_message}
  │
  ├─ session 不在 sessions 表？
  │   ├─ 有 prior unclosed sessions？
  │   │   → 场景 C: crash recovery → prior moments + turns → 注入
  │   └─ 无 prior unclosed sessions
  │       → 场景 A: 全新 session → 注入空
  │
  └─ session 在 sessions 表
      → 场景 B: /clear 或 compact → 当前 session turns + moments → 注入
```

---

## 2. 场景 A: 全新 session

```
1. sessions 表中无此 session_id → is_new = true
2. crash recovery: 扫描其他 unclosed sessions（如有，队列异步 Phase 2 + flush）
3. 本地 buffer: 空
4. 返回: {context: "", sources: {turns: 0, moments: 0, crash_recovery: 0}}
```

模型冷启动，后续通过 MCP tool on-demand pull。

---

## 3. 场景 B: `/clear` 或 compact

```
1. sessions 表中已有此 session_id → 幂等返回
2. 本地 buffer:
   → turns: 从 turns 表取最近 turns 摘要
   → moments: 取 unflushed moments（flushed=0）
3. 构建 <relevant-memories> → 截断到 token_budget（默认 1200）
4. 返回: {context: "...", sources: {turns: N, moments: M, crash_recovery: 0}}
```

内容优先级：turns 摘要 > unflushed moments。超 token_budget 时从最早的 moment 开始截断。

---

## 4. 场景 C: 新 session + crash recovery

```
1. sessions 表中无此 session_id
2. crash recovery 快路:
   → 读 prior unclosed session 的 unflushed moments + turns（SQLite，毫秒级）
3. crash recovery 慢路（异步）:
   → 队列 Phase 2 retrospective + flush
4. 构建 <relevant-memories>:
   → "Previous session (recovered):" + turns 摘要 + moments
5. 返回: {context: "...", sources: {turns: 0, moments: 0, crash_recovery: K}}
```

---

## 5. 注入内容格式

```xml
<relevant-memories>
  <recent-context>
    Turn 3-5: User discussed auth module design...
  </recent-context>

  <key-moments>
    <moment type="decision" detected_at="turn 4">
      <title>PostgreSQL for auth storage</title>
      <narrative>Team decided to use PostgreSQL...</narrative>
    </moment>
  </key-moments>

  <crash-recovery>
    Previous session ended abruptly. Recovered moments:
    <moment type="accomplishment">...</moment>
  </crash-recovery>
</relevant-memories>
```

---

## 6. 配置关联

| 配置项 | 影响 |
|--------|------|
| `injection.enabled` | false → 跳过所有场景，返回空 |
| `injection.token_budget` | 截断上限 |
| `injection.include_turns_summary` | false → 跳过 turns 摘要 |
| `injection.include_moments` | false → 跳过 moments |
| `injection.crash_recovery_moments` | false → 场景 C 跳过 prior session moments |
| `injection.inject_fallback` | "skip": buffer 空时返回空; "empty": 返回空 `<relevant-memories>` 块 |

---

## 7. 参考文档

- [`../../02-interfaces.md`](../../02-interfaces.md) — `/context/inject` 端点 spec
- [`../../03-daemon.md`](../../03-daemon.md) — SQLite tables、crash recovery
- [`../../04-config.md`](../../04-config.md) — injection config
- [`../01-architecture-overview.md`](../01-architecture-overview.md) — 三种 SessionStart 场景
