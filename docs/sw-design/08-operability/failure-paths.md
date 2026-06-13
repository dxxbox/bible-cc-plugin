# 08-operability/failure-paths.md — 故障路径（L3）

> 完整映射：故障场景 → 用户感知 → 诊断命令 → 恢复操作。

---

## 1. 故障矩阵

| # | 场景 | 严重度 | 用户感知 | 诊断 | 恢复 |
|---|------|--------|---------|------|------|
| F1 | daemon 端口被占 | ❌ error | `⎿ ❌ daemon failed to start on port X` | `/bible-cc:status` | 释放端口 或 `port_auto_fallback: true` |
| F2 | daemon 中途 crash | ⚠️ warning | 每 session 首次失败 hint "daemon unreachable"，后续静默跳过 | `/bible-cc:status` | 下次 SessionStart 自动启动 daemon + crash recovery |
| F3 | BiBLE Atlas 不可达 | ⚠️ warning | `⎿ ⚠️ BiBLE Atlas unreachable` | `/bible-cc:check-bible` | 修复 BiBLE 连接后自动恢复 |
| F4 | Phase 1 LLM 调用失败 | —（内部） | 无 | 无 | 自动重试（下次 threshold 触发） |
| F5 | Phase 2 LLM 调用失败 | —（内部） | 无 | `/bible-cc:status` | 手动 `/bible-cc:push` |
| F6 | flush 失败 | ⚠️ warning | `⎿ ⚠️ flush failed N times` | `/bible-cc:sync-status` | `/bible-cc:retry-push` |
| F7 | SQLite I/O 错误 | ❌ error | status 显示 `integrity: error` | `/bible-cc:status` | 检查磁盘/权限；必要时重装 |
| F8 | config.json 损坏/缺失 | ❌ error | daemon 启动失败 | `/bible-cc:status` | 停止 daemon → `setup` 重建 config → 重启 |

---

## 2. 逐场景详细路径

### F1: daemon 端口被占

```
User sees:  ⎿ ❌ bible-cc daemon failed to start on port 9777 (address in use).
              Run /bible-cc:status for details.

User runs:  /bible-cc:status → daemon: not running

Options:    1. 释放 9777 端口（kill 占用进程）
            2. /bible-cc:config-set daemon.port 9778
            3. /bible-cc:config-set daemon.port_auto_fallback true

Recovery:   下次 SessionStart 自动重试启动。
```

### F2: daemon 中途 crash

```
User sees:  UserPromptSubmit/PostToolUse hook 尝试调 daemon 失败
            → hook 检查标记文件 /tmp/bible-cc-hint-{session_id}
            → 不存在: 输出 hint "⎿ ⚠️ bible-cc daemon unreachable. Local capture paused." 并创建标记文件
            → 存在: 静默跳过（同 session 已通知过，不刷屏）
            → 标记文件在 SessionStart 时清理。
            → 数据留在 SQLite，下次 SessionStart 自动 crash recovery。

User runs:  /bible-cc:status → daemon: not running

Recovery:   下次 SessionStart 自动触发 daemon 启动 + crash recovery。
            或手动: /bible-cc:recover
```

### F3: BiBLE Atlas 不可达

```
User sees:  ⎿ ⚠️ BiBLE Atlas unreachable (http://localhost:5555). Moments stay local.

User runs:  /bible-cc:check-bible → UNREACHABLE

Impact:     - MCP tools 返回 error，模型被告知
            - flush 暂停，moments 保持 flushed=0
            - 本地操作不受影响

Recovery:   BiBLE 恢复后 /bible-cc:push 或 /bible-cc:push-all 手动 flush。
```

### F4-F5: LLM 调用失败

```
F4 (Phase 1):  无感知。自动重试。连续失败 10 次（内部固定值，不可配置） → daemon log error。
F5 (Phase 2):  无感知。Phase 2 的 retrospective moments 丢失。
               Mitigation: 提高 Phase 1 频率（降低 commit_threshold）。
```

### F6: flush 失败

```
User sees:  ⎿ ⚠️ flush to BiBLE Atlas failed 3 times. Moments accumulate locally.

User runs:  /bible-cc:sync-status → N pending moments

Recovery:   /bible-cc:retry-push 或 /bible-cc:push-all
```

### F7: SQLite I/O 错误

```
User sees:  /bible-cc:status → sqlite.integrity: "error"

Recovery:   1. df -h ~/.bible-cc/（检查磁盘空间）
            2. ls -la ~/.bible-cc/daemon.db（检查权限）
            3. 如果损坏: mv daemon.db daemon.db.bak → 重启 daemon（自动创建新 DB）
            4. 极端: /bible-cc:uninstall → 重新安装
```

### F8: config.json 损坏/缺失

```
User sees:  daemon 启动失败（无 config 不可启动）

Recovery:   1. 确认 daemon 已停止（或 kill 残留进程）
            2. uv run python -m bible_cc_plugin.scripts.setup → 交互式重建 config
            3. SessionStart 自动启动 daemon（或手动 restart）
```


---

> **Content-hash 碰撞（F9）已从故障矩阵移除。** 这是正常行为——INSERT OR IGNORE 静默跳过重复 moment。`/bible-cc:review` 可显示 "N duplicates suppressed" 供用户了解。

## 3. 恢复命令

| 命令 | 功能 | 适用场景 |
|------|------|---------|
| `/bible-cc:recover` | 手动触发 crash recovery | F2 |
| `/bible-cc:retry-push` | 重试当前 session 的 flush | F6 |
| `/bible-cc:push-all` | 跨 session 全局 flush | F6 |
| `/bible-cc:check-bible` | 测试 BiBLE 连通性 | F3 |
| `/bible-cc:status` | 完整健康检查 | 所有 |

---

## 4. 参考文档

- [`hint-system.md`](hint-system.md) — 通知模板和触发逻辑
- [`status.md`](status.md) — 诊断命令输出格式
- [`../../03-daemon.md`](../03-daemon.md) — 错误处理策略、crash recovery
