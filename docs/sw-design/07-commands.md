# 07 — Commands

> L2 | 领域总览 | 定义了所有用户 slash command 的设计约束和实现模式。完整 spec 见 L3 和 `docs/command-priority-table.md`。

---

## 1. 定位

Commands 是用户主动触发的 slash command（`/bible-cc:status` 等），操作 daemon 或 plugin 配置。与 MCP tools（模型自动调用）互补。

---

## 2. 全局约束

1. **操作 daemon**：除 consult 外，不直接调 BiBLE API。通过 daemon HTTP API。
2. **薄封装**：每个 command 是 shell one-liner（`curl` 或 `uv run python -m`）。无业务逻辑。
3. **幂等安全**：可安全重复执行。

---

## 3. 实现模式

```
curl -s http://127.0.0.1:9777/<endpoint> | python -m json.tool
uv run python -m bible_cc_plugin.scripts.hook <action>
```

---

## 4. 命令清单

完整表见 `docs/command-priority-table.md`。核心：

| 命令 | 端点 | 用途 |
|------|------|------|
| `/bible-cc:status` | `GET /daemon/health` | 健康检查 |
| `/bible-cc:check-bible` | `GET /health` | BiBLE 连通性 |
| `/bible-cc:push` | daemon flush logic (`flushed=0` moments → BiBLE import) | 立即 flush |
| `/bible-cc:consult` | `POST /daemon/consult` | 跨域搜索 |
| `/bible-cc:review` | `GET /daemon/moments` 等 | 管理 pending moments |
| `/bible-cc:help` | — | 命令列表 |
| `/bible-cc:config` | — | 查看配置 |
| `/bible-cc:version` | — | 查看版本 |

---

## 5. 子模块

| 文件 | 内容 | 状态 |
|------|------|------|
| `07-commands/specs.md` | 每个命令的完整 spec | ✅ 完成 |

---

## 6. 参考文档

- [`02-interfaces.md`](02-interfaces.md) — Daemon HTTP API
- [`../command-priority-table.md`](../command-priority-table.md) — 完整命令清单
