# bible-cc-plugin Command Priority

**Date**: 2026-06-12 | 总计 **38** 个接受命令/工具

---

## 🚀 MVP（7 个）

最小可跑通路：operability + connectivity + debuggability。

| # | Command | 理由 |
|---|---------|------|
| 2 | `/bible-cc:status` | operability：daemon 状态、buffer 统计、SQLite 完整性 |
| 7 | `/bible-cc:check-bible` | connectivity：BiBLE Atlas 心跳 |
| 39 | `/bible-cc:help` | discoverability：可用命令列表 |
| 15 | `/bible-cc:config` | debug：查看当前配置 |
| 38 | `/bible-cc:version` | debug：当前版本 |
| 14 | `/bible-cc:context` | debug：上次注入了什么 |
| 12 | `/bible-cc:sessions` | debug：active sessions |

---

## 高优先级（11 个）

| # | Command | 类型 | 场景 |
|---|---------|------|------|
| 2 | `/bible-cc:status` | command | daemon 状态、BiBLE 连通性、buffer 统计、SQLite 完整性、schema 版本 | 🚀 MVP |
| 7 | `/bible-cc:check-bible` | command | BiBLE Atlas 心跳检查，展示延迟和状态 | 🚀 MVP |
| 9 | `/bible-cc:push` | command | 立即把当前 session 的 moments 推送到 BiBLE Atlas |
| 10 | `/bible-cc:consult` | command | 用户主动向 BiBLE Atlas 跨域查询（memory + knowledge + skill），pull 上下文 |
| 11 | `/bible-cc:review` | command | 浏览/编辑/删除 pending moments，管理自己数据的主权 |
| 39 | `/bible-cc:help` | command | 列出所有可用命令 | 🚀 MVP |
| 54 | `/bible-cc:memory-duplicates` | command | 扫描并合并 BiBLE 中的重复记忆 |
| 57 | `/bible-cc:memory-merge` | command | "这两条说的是同一件事，合并" |
| 67 | `/bible-cc:team-search` | command | 在团队共享记忆中搜索 |
| 68 | `/bible-cc:share-memory` | command | 将个人记忆分享到团队空间 |

---

## 中优先级（17 个）

| # | Command | 类型 | 场景 |
|---|---------|------|------|
| 14 | `/bible-cc:context` | command | 调试：上次注入到模型的 `<relevant-memories>` 内容 | 🚀 MVP |
| 15 | `/bible-cc:config` | command | 查看当前配置（BiBLE URL、检测模型、端口等） | 🚀 MVP |
| 16 | `/bible-cc:config-set` | command | 修改配置项（无需手改 JSON） |
| 22 | `/bible-cc:capture-pause` | command | "下面聊的内容是私人的，暂停记录" |
| 23 | `/bible-cc:capture-resume` | command | "可以继续记录了" |
| 27 | `/bible-cc:recover` | command | 手动触发 crash recovery |
| 42 | `/bible-cc:token-usage` | command | 当前 session token 消费统计（含 injection 开销） |
| 51 | `/bible-cc:memory-graph` | command | 可视化记忆网络，展示记忆之间的关联 |
| 55 | `/bible-cc:memory-tag` | command | "给这条记忆打标签" |
| 56 | `/bible-cc:memory-tags` | command | "我有哪些标签？" 标签浏览 |
| 66 | `/bible-cc:team-activity` | command | "团队成员最近在做什么？" |
| 69 | `/bible-cc:handoff` | command | 把当前 session 上下文打包给同事 |
| 78 | `/bible-cc:privacy-audit` | command | 扫描记忆中的敏感信息 |
| 79 | `/bible-cc:redact` | command | "把这条记忆中的密码/API key 删掉" |
| 80 | `/bible-cc:forget-project` | command | "忘掉这个项目的所有记忆" |
| 81 | `/bible-cc:forget-session` | command | "忘掉这个 session" |
| 92 | `/bible-cc:upgrade` | command | 检查并升级 bible-cc-plugin |
| 93 | `/bible-cc:changelog` | command | 查看版本更新日志 |
| 94 | `/bible-cc:uninstall` | command | 完整卸载（清空本地数据、停 daemon） |

---

## 低优先级（17 个）

| # | Command | 类型 | 场景 |
|---|---------|------|------|
| 12 | `/bible-cc:sessions` | command | daemon 里有哪些 session 还活着？排障用 | 🚀 MVP |
| 18 | `/bible-cc:buffer` | command | 查看当前 session buffer（turns 摘要） |
| 20 | `/bible-cc:sync-status` | command | "还有多少 moments 没 push 到 BiBLE？" |
| 21 | `/bible-cc:push-all` | command | 跨 session 全局 push 所有 pending moments |
| 24 | `/bible-cc:capture-mode` | command | 临时切换采集模式（key_moments / full） |
| 25 | `/bible-cc:bypass-add` | command | "这个项目永远不记录" |
| 28 | `/bible-cc:retry-push` | command | "上次 push 失败了，重试" |
| 38 | `/bible-cc:version` | command | "装的哪个版本？" | 🚀 MVP |
| 50 | `/bible-cc:memory-timeline` | command | "我这周做了什么？" 按时间线浏览记忆 |
| 58 | `/bible-cc:memory-fork` | command | "从这条记忆分支出一个新话题" |
| 70 | `/bible-cc:team-overlap` | command | "团队里有谁在做类似的事情？" |
| 71 | `/bible-cc:team-notes` | command | "给团队留一条笔记" |
| 82 | `/bible-cc:data-request` | command | "我的数据都在哪？" 导出全部个人数据（合规） |
| 87 | `/bible-cc:gc` | command | 清理过期临时数据，释放 SQLite 空间 |
| 89 | `/bible-cc:project-switch` | command | "切换到另一个项目的上下文" |
| 90 | `/bible-cc:project-context` | command | "这个项目的整体上下文是什么？" |
| 34 | `bible_memory_delete` | MCP tool | "这条记忆是错的，删掉"（已 flush 到 BiBLE 的） | ❌ postponed — 待服务端确认 |

---

---

---

## 🔧 Plugin 数据采集（daemon feature，非 command）

以下功能不作为独立命令，由 plugin daemon 负责采集数据并随 push 发送到 BiBLE Server，由 server 侧展示。

| # | 功能 | 说明 |
|---|------|------|
| #43-49 | Token 数据采集 | 会话 token 消费、injection 开销等，存本地 DB，push 时带 |
| #61 search-history | 搜索历史采集 | 用户搜索行为数据 → server analytics |
| #83-86 | 性能数据采集 | daemon 响应延迟、SQLite 慢查询、内存/磁盘占用 → server monitoring |

---

## 🔧 Server 侧能力（bible-cc 不实现）

以下功能为 BiBLE Server 侧的分析报告或管理能力，基于 plugin 采集的数据生成。不在 bible-cc-plugin 范畴内。

| # | 功能 | 说明 |
|---|------|------|
| 52 | memory-top | 高频话题排行 |
| 53 | memory-gaps | 知识盲区分析 |
| 64 | analyze | 工作模式分析 |
| 65 | trends | 话题热度趋势 |
| 72 | daily-digest | 日报 |
| 73 | weekly-report | 周报 |
| 74 | digest-schedule | 定时摘要任务 |
| 77 | follow-up | 未跟进事项检测 |
| 88 | projects | 项目概览 |
| 91 | project-compare | 项目对比分析 |
| 95 | personality | 工作风格画像 |
| 96 | wrapped | 年度回顾（Wrapper） |

---

### 图例

| 标记 | 含义 |
|------|------|
| ✅ 高/中/低 | bible-cc-plugin command，已确认优先级 |
| 🔧 数据采集 | plugin daemon 负责采集 → push 时带给 BiBLE Server → server dashboard/monitoring |
| 🔧 server 能力 | 纯 BiBLE Server 侧分析报告/仪表盘/自动化能力 |

---

## 统计

| 类型 | 数量 | 说明 |
|------|------|------|
| 🚀 MVP | 7 | 最小跑通版本：status, check-bible, help, config, version, context, sessions |
| ✅ bible-cc command（高） | 10 | plugin 高优先级命令（含 MVP） |
| ✅ bible-cc command（中） | 17 | plugin 中优先级命令（含 MVP） |
| ✅ bible-cc command（低） | 10 | plugin 低优先级命令（含 MVP） |
| ✅ bible-cc MCP tool | 0 |（全部 MCP tool 待服务端确认或已覆盖） |
| **plugin 采纳总计** | **37** |（含 2 postponed） |
| 🔧 plugin 数据采集 | 3（多 # 归组） | daemon 采集 → server 展示 |
| 🔧 server 侧能力 | 12 | 纯 server 侧分析报告 |
| **候选总计** | **53** |（原 98 个，已删除 29 个真正不需要 + 16 个合并/改名/重分类） |
