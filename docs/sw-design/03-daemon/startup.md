# 03-daemon/startup.md — Startup Sequence（L3）

> Daemon 启动全流程：配置加载 → SQLite 初始化 → WAL PRAGMA → schema migration → crash recovery → uvicorn。本文不重复 L2 全局约束，仅补充实现细节。

---

## 1. 启动序列

```
                    ┌─────────────────┐
                    │ 1. Load Config  │
                    └───────┬─────────┘
                            │
                    ┌───────▼─────────┐
                    │ 2. Resolve Port │
                    └───────┬─────────┘
                            │
                    ┌───────▼──────────┐
                    │ 3. Open SQLite   │
                    │    + PRAGMA      │
                    └───────┬──────────┘
                            │
                    ┌───────▼──────────┐
                    │ 4. Schema        │
                    │    Migration     │
                    └───────┬──────────┘
                            │
                    ┌───────▼──────────┐
                    │ 5. Crash         │
                    │    Recovery Scan │
                    └───────┬──────────┘
                            │
                    ┌───────▼──────────┐
                    │ 6. Start uvicorn │
                    └──────────────────┘
```

### 1.1 步骤细节

**Step 1 — Load Config**

```python
config = load_config()  # see 04-config/schema.md §4
```

从 config.json + env var overlay 加载完整配置。加载失败时使用 built-in defaults（Pydantic defaults）。不得因配置非法而阻止启动。

**Step 2 — Resolve Port**

```python
port = config.daemon.port  # default 9777
if config.daemon.port_auto_fallback:
    port = find_available_port(port)  # see port-conflict.md
```

- 不在此步绑定端口——uvicorn 启动时才绑定。
- `port_auto_fallback` 逻辑见 [`port-conflict.md`](port-conflict.md)。

**Step 3 — Open SQLite + PRAGMA**

```python
db_path = Path(config.daemon.db_path).expanduser()
db_path.parent.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(str(db_path))
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA busy_timeout=5000;")
conn.row_factory = sqlite3.Row
```

- `~` 展开为 `Path.home()`。
- 父目录不存在时自动创建（`mkdir(parents=True)`）。
- **WAL 和 busy_timeout 必须在任何读写之前执行**。违反此顺序是 bug。
- 此步同步完成。

**Step 4 — Schema Migration**

见 [`sqlite-schema.md`](sqlite-schema.md) §4。核心逻辑：

```python
current_version = get_schema_version(conn)  # 0 if no schema_version table
for migration in MIGRATIONS[current_version:]:
    conn.executescript(migration.sql)
    set_schema_version(conn, migration.version)
conn.commit()
```

- 幂等：所有 DDL 使用 `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`。
- migration script 按版本号顺序执行。
- 此步同步完成。

**Step 5 — Crash Recovery Scan**

```python
unclosed = conn.execute(
    "SELECT * FROM sessions WHERE status = 'active'"
).fetchall()

if unclosed:
    # Fast path (sync):
    recovery_moments = conn.execute(
        "SELECT * FROM moments WHERE session_id IN (...) AND flushed IN (0, -1)"
    ).fetchall()
    recovery_turns = conn.execute(
        "SELECT * FROM turns WHERE session_id IN (...) ORDER BY seq"
    ).fetchall()

    # Slow path (async):
    for session in unclosed:
        asyncio.create_task(retrospective_and_flush(session))
```

- **快路（同步）**：读 SQLite — 毫秒级，不阻塞启动。recovery moments 和 turns 注入当前 session 的 `/context/inject`。
- **慢路（异步）**：Phase 2 retrospective + flush — 在后台任务中执行，不阻塞 uvicorn 启动。完成后通过 hint 通知用户（见 [`08-operability/hint-system.md`](../08-operability/hint-system.md)）。
- 每个 unclosed session 独立处理，互不影响。
- **恢复 seq 计数器**：crash recovery 后，从 SQLite 恢复所有活跃 session 的 turn 序号，防止新 turn 的 seq 与已有数据冲突（`turns` 表有 `UNIQUE(session_id, seq)` 约束）：
  ```python
  rows = conn.execute(
      "SELECT session_id, MAX(seq) FROM turns GROUP BY session_id"
  ).fetchall()
  for session_id, max_seq in rows:
      if max_seq is not None:
          session_seq[session_id] = max_seq
  ```
  此步不仅覆盖 crash 遗留的 unclosed session，也覆盖 daemon 正常重启后仍活跃的 session。"max_seq is None" 分支在 session 存在但尚无 turn 时触发（极端情况），此时 seq 从 1 开始即可。

**Step 6 — Start uvicorn**

```python
uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
```

- 绑定 `127.0.0.1`（仅本地），不暴露到网络。
- 不设 `reload=True`（生产模式）。

---

## 2. 幂等性

`POST /daemon/start` 必须幂等——已运行时直接返回当前状态：

```python
if daemon_is_running(port):
    return {"pid": os.getpid(), "port": port, "status": "running"}
```

检测方式：尝试对 `http://127.0.0.1:{port}/daemon/health` 发 GET 请求。如返回 200，认为已在运行。

幂等的好处：SessionStart hook 总是调用 `POST /daemon/start`，无需判断是否已启动。hook 脚本逻辑简化。

---

## 3. DB 路径

| 来源 | 值 |
|------|---|
| Default | `~/.bible-cc/daemon.db` |
| Config file | `daemon.db_path` |
| Env override | `BIBLE_CC_DB_PATH` |

父目录（`~/.bible-cc/`）由 daemon 在 Step 3 自动创建。Setup hook 也可预先创建，但 Step 3 的 `mkdir(parents=True)` 确保在任何情况下目录都存在。

---

## 4. 启动失败模式

| 失败点 | 行为 |
|--------|------|
| Config 文件损坏（非法 JSON） | 使用 Pydantic defaults，启动继续。Log warning。 |
| SQLite 无法打开（权限/磁盘满） | 致命错误。daemon 无法启动。返回 error + hint。 |
| WAL PRAGMA 失败 | 致命错误。SQLite 返回 error。daemon 不启动。 |
| Migration 脚本错误 | 致命错误。回滚当前 migration，daemon 不启动。 |
| Crash recovery 快路失败 | Log error，启动继续。`/context/inject` 返回空。 |
| Crash recovery 慢路失败 | Log error，启动继续。仅影响 recovery session 的 flush。 |
| 端口被占（无 fallback） | 致命错误。SessionStart hook 输出 error hint。 |
| uvicorn 启动失败 | 致命错误。同端口被占处理。 |

致命错误时，SessionStart hook 的 stdout 会输出 error hint（inject: true → 同时进入 transcript 和 system prompt）。格式见 [`08-operability/hint-system.md`](../08-operability/hint-system.md)。

---

## 5. SessionStart hook 中的角色

SessionStart hook 调用 `POST /daemon/start` 作为其三步流程的第一步：

```
SessionStart hook
  → POST /daemon/start     ← 如果 daemon 已运行，立即返回
  → POST /session/start    ← register session + crash recovery fast path
  → POST /context/inject   ← local buffer injection
```

如果 `POST /daemon/start` 失败（daemon 无法启动），后续两步跳过，hook 输出 error hint。UserPromptSubmit 和 PostToolUse hooks 检测到 daemon 不可达后静默跳过。

---

## 6. 参考文档

- [`sqlite-schema.md`](sqlite-schema.md) — 表结构、migration 脚本
- [`port-conflict.md`](port-conflict.md) — 端口冲突处理
- [`http-api.md`](http-api.md) — `/daemon/start` 端点 spec
- [`../../04-config/schema.md`](../04-config/schema.md) — 配置加载器
- [`../08-operability/hint-system.md`](../08-operability/hint-system.md) — error hint 格式
- [`../08-operability/failure-paths.md`](../08-operability/failure-paths.md) — F1 daemon 启动失败
