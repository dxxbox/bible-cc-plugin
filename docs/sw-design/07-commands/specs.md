# 07-commands/specs.md — 命令完整 Spec（L3）

> 每个命令的概要：触发、参数、daemon 端点、返回、错误处理。完整优先级和场景说明见 `docs/command-priority-table.md`。

---

## MVP（7 个）

| 命令 | 类型 | 端点 | 说明 |
|------|------|------|------|
| `/bible-cc:status` | curl | `GET /daemon/health` | 展示 daemon、sessions、buffer、bible、sqlite 五域状态 |
| `/bible-cc:check-bible` | curl | `GET {bible_base_url}/health` | 轻量心跳。reachable→延迟；unreachable→原因+当前 URL |
| `/bible-cc:help` | 本地 | — | 列出所有可用命令+简要说明 |
| `/bible-cc:config` | curl/本地 | 读 `~/.bible-cc/config.json` | 展示配置内容（token 脱敏） |
| `/bible-cc:version` | 本地 | 读 `pyproject.toml` | 版本号 |
| `/bible-cc:context` | curl | daemon 缓存 | 上次注入的 sources + token 估算 + preview |
| `/bible-cc:sessions` | curl | daemon 内存 | active sessions 列表 |

---

## 高优先级（3 个）

| 命令 | 类型 | 端点 | 说明 |
|------|------|------|------|
| `/bible-cc:push [--title] [--abstract]` | `uv run` | `POST /daemon/session/flush` | 立即检测+flush 当前 session moments |
| `/bible-cc:consult [query]` | curl | `POST /daemon/consult` | 跨域搜索。有 query→直接搜；无→LLM 归纳→并行三域 |
| `/bible-cc:review` | curl | `GET/PUT/DELETE /daemon/moments` | 浏览/编辑/删除 pending moments |

---

## 中优先级（17 个）

| 命令 | 类型 | 端点 | 说明 |
|------|------|------|------|
| `/bible-cc:config-set <key> <value>` | `uv run` | 写 config.json | 修改配置。非法 key→提示 |
| `/bible-cc:capture-pause` | curl | daemon | 暂停采集 |
| `/bible-cc:capture-resume` | curl | daemon | 恢复采集 |
| `/bible-cc:recover` | curl | daemon | 手动 crash recovery |
| `/bible-cc:token-usage` | curl | daemon | 当前 session token 概要 |
| `/bible-cc:memory-graph` | curl | daemon→BiBLE | 记忆关联图 |
| `/bible-cc:memory-tag <id> <tag>` | curl | daemon→BiBLE | 打标签 |
| `/bible-cc:memory-tags` | curl | daemon→BiBLE | 标签列表 |
| `/bible-cc:team-activity` | curl | daemon→BiBLE | 团队动态 |
| `/bible-cc:handoff` | curl | daemon→BiBLE | 打包上下文给同事 |
| `/bible-cc:privacy-audit` | curl | daemon→BiBLE | 扫描敏感信息 |
| `/bible-cc:redact <id>` | curl | daemon→BiBLE | 脱敏 |
| `/bible-cc:forget-project` | curl | daemon→BiBLE | 删除项目记忆（需确认） |
| `/bible-cc:forget-session` | curl | daemon→BiBLE | 删除 session 数据（需确认） |
| `/bible-cc:upgrade` | `uv run` | — | 检查→拉取→uv sync→restart |
| `/bible-cc:changelog` | 本地 | `CHANGELOG.md` | 变更内容 |
| `/bible-cc:uninstall` | `uv run` | stop daemon + rm -rf | 卸载流程 |

---

## 低优先级（16 个）

| 命令 | 端点 | 说明 |
|------|------|------|
| `/bible-cc:buffer` | daemon | 查看 buffer 内容 |
| `/bible-cc:sync-status` | daemon | pending moments 数量 |
| `/bible-cc:push-all` | daemon | 全局 flush |
| `/bible-cc:capture-mode` | daemon | 切换采集模式 |
| `/bible-cc:bypass-add` | daemon | 添加 bypass pattern |
| `/bible-cc:retry-push` | daemon | 重试失败 flush |
| `/bible-cc:memory-timeline` | daemon→BiBLE | 时间线浏览 |
| `/bible-cc:memory-fork` | daemon→BiBLE | 分支新话题 |
| `/bible-cc:team-overlap` | daemon→BiBLE | 工作重叠检测 |
| `/bible-cc:team-notes` | daemon→BiBLE | 团队笔记 |
| `/bible-cc:data-request` | daemon | 导出个人数据 |
| `/bible-cc:gc` | daemon | 清理过期数据 |
| `/bible-cc:project-switch` | daemon | 切换项目上下文 |
| `/bible-cc:project-context` | daemon | 项目概况 |
| `bible_memory_delete` | MCP tool | postponed |

> **端点标记说明**：部分中/低优先级命令的 daemon 端点尚未在 `02-interfaces.md` 中定义（标记为 "daemon" 或 "daemon→BiBLE"）。这些端点将在实现时补充到 `03-daemon/http-api.md`。标记为 "curl" 或 "uv run" 的命令已有明确端点。

完整清单见 `docs/command-priority-table.md`。

---

## 参考文档

- [`../07-commands.md`](../07-commands.md) — L2 总览
- [`../../02-interfaces.md`](../../02-interfaces.md) — HTTP API 端点
- [`../../command-priority-table.md`](../../command-priority-table.md) — 完整清单
