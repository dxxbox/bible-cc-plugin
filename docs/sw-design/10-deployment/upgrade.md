# 10-deployment/upgrade.md — 升级生命周期（L3）

> 升级的完整设计：版本检测、依赖更新、schema migration、回滚、数据兼容性。

---

## 1. 升级触发路径

| 路径 | 触发者 | 流程 |
|------|--------|------|
| Marketplace | 用户执行 `plugins update` | 自动拉取新版本 → `uv sync` → `reload-plugin --force`（非交互式，不触发 setup wizard） |
| 手动 git pull | 开发者 | `git pull` → `uv sync` → `reload-plugin --force` |
| `/bible-cc:upgrade` | 用户命令 | 检查更新 → 提示 → 用户确认 → 执行升级 |

---

## 2. 版本检测

`/bible-cc:version` 读取当前版本号（来自 `pyproject.toml` 的 `version` 字段）。

`/bible-cc:upgrade` 命令：
1. 从 plugin registry 或 git remote 获取最新版本号
2. 对比当前版本号
3. 如果 current < latest → 提示升级
4. 用户确认 → 执行升级流程
5. 如果 current == latest → 告知已是最新

版本号格式遵循 [PEP 440](https://peps.python.org/pep-0440/)：`MAJOR.MINOR.PATCH`。

---

## 3. 升级流程

```
1. 拉取新代码
   → Marketplace: 下载新版本到 ~/.claude/plugins/cache/
   → 手动: git pull

2. 安装新依赖
   → uv sync
   → 仅安装 pyproject.toml 中声明的依赖
   → 不修改 ~/.bible-cc/ 下的用户数据

3. 停止旧 daemon
   → 升级脚本（或 `/bible-cc:upgrade` 命令）调用 POST /daemon/stop
   → 等待优雅关闭（≤5s）
   → 强制 kill 如果超时

4. 启动新 daemon（或等下次 SessionStart 自动启动）
   → POST /daemon/start
   → 执行启动序列（WAL → migration → crash recovery → uvicorn）

5. 验证
   → GET /daemon/health → 确认 status: "ok"
   → GET /daemon/health → 确认 schema_version 已更新
```

---

## 4. Schema Migration

所有 migration 必须满足：

| 规则 | 说明 |
|------|------|
| **幂等** | `CREATE TABLE IF NOT EXISTS`，重复执行无害 |
| **只加不删** | 只允许 `CREATE TABLE`、`ALTER TABLE ADD COLUMN`。不删表、不删列、不改类型 |
| **默认值** | 新增列必须有 `DEFAULT` 值，保证旧数据兼容 |
| **版本号** | `schema_versions` 表记录当前 schema 版本，启动时检查 |

Migration 伪代码：

```python
def migrate(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    current_version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_versions"
    ).fetchone()[0]

    if current_version < 1:
        # 完整 schema 定义见 03-daemon/sqlite-schema.md
        conn.execute("CREATE TABLE IF NOT EXISTS sessions ("
            "session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'active', topic_scope TEXT,"
            "turn_count INTEGER DEFAULT 0, buffered_chars INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS turns ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "session_id TEXT NOT NULL REFERENCES sessions(session_id),"
            "seq INTEGER NOT NULL, role TEXT NOT NULL,"
            "content TEXT NOT NULL, tool_calls TEXT, timestamp TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS moments ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "session_id TEXT NOT NULL REFERENCES sessions(session_id),"
            "moment_type TEXT NOT NULL, title TEXT NOT NULL,"
            "narrative TEXT NOT NULL, turn_range TEXT,"
            "detected_at TEXT NOT NULL, flushed INTEGER DEFAULT 0,"
            "content_hash TEXT UNIQUE NOT NULL)")
        conn.execute("INSERT INTO schema_versions (version) VALUES (1)")

    if current_version < 2:
        # v2 migration (future example)
        pass
```

---

## 5. 数据兼容性

| 场景 | 策略 |
|------|------|
| 旧版 SQLite DB + 新版 daemon | Migration 自动升级 schema。旧数据保留，新列填默认值。 |
| 新版 SQLite DB + 旧版 daemon | **不支持**。升级后不可降级使用旧版 daemon。 |
| flush 到 BiBLE 的数据 | 不受影响。升级只改 plugin 本地逻辑。 |
| config.json 格式变化 | 新版 config loader 必须向后兼容旧格式。未知键忽略，缺键用默认值。 |

---

## 6. 回滚

若升级后出现严重问题：

```bash
# 1. 停止新版 daemon
curl -X POST http://127.0.0.1:9777/daemon/stop

# 2. 恢复旧版代码
cd ~/.claude/plugins/bible-cc-plugin
git checkout <previous-version-tag>
uv sync

# 3. schema migration 只加不删 → 旧版代码安全忽略新增列/表

# 4. 下次 SessionStart 自动启动旧版 daemon
```

**原则**：Schema migration 只加不删的策略保证回滚到旧版代码时数据兼容。

---

## 7. 升级通知

升级完成后，daemon 向用户发送 hint：

```
⎿ ✅ bible-cc upgraded to v1.2.0 (schema v2). /bible-cc:changelog for details.
```

`/bible-cc:changelog` 命令读取 `CHANGELOG.md` 或 git tag message 展示变更内容。

---

## 8. 参考文档

- [`../10-deployment.md`](../10-deployment.md) — L2 部署总览
- [`../../02-interfaces.md`](../../02-interfaces.md) — `/daemon/start`, `/daemon/stop`, `/daemon/health`
- [`../../03-daemon.md`](../../03-daemon.md) — 启动序列、schema migration
