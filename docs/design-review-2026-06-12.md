# bible-cc-plugin Design Review

**Date**: 2026-06-12
**Review scope**: `CLAUDE.md` + `docs/bible-claude-code-plugin-feasibility-report.md`
**Baseline**: 10 findings (2 critical, 4 major, 4 minor)

---

## Finding 1 (Critical): 可行报告与 CLAUDE.md 存在语言矛盾

**场景**：一个新开发者加入项目，先读了 `docs/bible-claude-code-plugin-feasibility-report.md`（Q3 写的是 TypeScript + Bun），然后开始用 `bun install` 搭建环境。接着发现 `pyproject.toml`、`uv sync`、Python 测试文件，完全不知所措。

**问题**：可行报告的 Q3 仍然写的是 **TypeScript (Bun runtime)**，但 CLAUDE.md 明确说 "Q3 is superseded — final decision is Python + uv"。两份权威文档互相矛盾。两套不同的包结构、不同的依赖、不同的脚本也都列在报告的 Package Structure 章节里。这会导致实现时出现严重偏差。

**影响范围**：Package Structure 章节、`.mcp.json` 示例、全部脚本命令、依赖列表。

---

## Finding 2 (Critical → Resolved): SIGTERM 杀死 daemon 时缓冲数据丢失

**场景**：用户工作了一整天，session 中有 15 个 turns 和 3 个已检测但未 flush 的 key moments。下班时用户关机，系统发送 SIGTERM 给所有进程。daemon 被杀死，Stop hook 没有机会触发（hook 本身也依赖 daemon 响应 `/session/end`）。第二天开机，daemon 重新启动，但用户换了一个项目工作（没有触发 bible-cc-plugin 的 SessionStart），昨天的缓冲数据就永远留在 SQLite 里，不会被 flush。

**讨论结论**：
- 如果用户再也不回这个项目 → 数据无意义，不 flush 也无所谓。
- 如果用户回来 → SessionStart 触发 → daemon 检测 unclosed session → flush 恢复。SQLite 持久化保证了数据不丢，只是延迟 flush。
- 真正的问题是 **Setup 和 SessionStart 的时序 gap**（daemon 可能还没启动 SessionStart 就到了），而非 SIGTERM 本身。

**解决方案**：SessionStart 改为自给自足模式（参考 claude-mem），hook 脚本内部先 idempotent 启动 daemon，再调 `/session/start` + `/context/inject`。Timeout 提升到 60s 覆盖冷启动。已更新可行报告和 CLAUDE.md。

**状态**：✅ Resolved

---

## Finding 3 (Major → Resolved): 中段检测和回顾检测的去重缺失

**场景**：用户在 session 中做了一个重要决策 "用 Redis 做 session store"。Phase 1 中段检测在 turn 5-7 的窗口中检测到这个决策，保存为 moment（flushed=0）。Session 正常结束，Phase 2 回顾检测扫描全部 turns 又检测到了同一个决策，再次生成一条 moment。最终同一个决策被 import 到 BiBLE 两次。

**讨论结论**：不仅 Phase 1→2 有重复风险，Phase 1 自身的滑动窗口重叠也会产生自重复。采用两层去重：

| Layer | 机制 | 覆盖范围 |
|---|---|---|
| Prompt injection | Phase 2 prompt 注入已检测 moments，要求 LLM 不重复标记 | Phase 1→2 |
| Content-hash | `SHA-256(session_id + title + narrative)` UNIQUE 约束 + INSERT OR IGNORE | 所有来源（含 Phase 1 自重复） |

**解决方案**：SQLite schema 增加 `content_hash TEXT UNIQUE NOT NULL`，Phase 2 prompt 注入已知 moments 列表，insert 前计算 hash。已更新可行报告和 CLAUDE.md。

**状态**：✅ Resolved

---

## Finding 4 (Major → Resolved): 多 Session 并发时的 SQLite 写冲突

**场景**：用户同时开了两个 Claude Code 终端窗口（session A 和 session B）。两个 session 的 PostToolUse hook 几乎同时触发 `/turn/tool`，daemon 的 FastAPI server 用多个 worker 处理请求，同时对 `turns` 表做 INSERT。Python stdlib `sqlite3` 默认 `journal_mode=DELETE`，并发写直接抛 `SQLITE_BUSY`，写入静默失败。

**讨论结论**：claude-mem 用 Bun 的 `bun:sqlite` 默认开启 WAL 模式，bible-cc-plugin 用 Python stdlib 默认 DELETE 模式——同样的架构，Python 版本自动继承了更差的默认值。修复成本极低：daemon 启动时两行 PRAGMA。

**解决方案**：Daemon 启动阶段增加 `PRAGMA journal_mode=WAL;` + `PRAGMA busy_timeout=5000;`。已更新可行报告（新增 Daemon Startup 章节）和 CLAUDE.md。

**状态**：✅ Resolved

---

## Finding 5 (Major → Resolved): SessionStart 触发 context injection 的频率过高

**场景**：用户在一个长 session 中工作，上下文窗口快满了，执行 `/clear`。Claude Code 触发 SessionStart hook → daemon 再次调用 `/context/inject`，对 BiBLE Atlas 发起一轮新的 speculative search。但用户只是清空上下文继续同一个任务，并不需要重新搜索 BiBLE——尤其是在 30 秒前刚搜过一次。

**讨论结论**：问题本质不是"要不要注入"，而是"从哪里恢复"。模型在 `/clear` 后确实需要重新注入（丢了上下文），但不应该每次都 speculative 搜索远程 BiBLE Atlas。

**根本方案 — pull 模型取代 push 模型**：
- **SessionStart `/context/inject`**：纯本地 buffer（turns + moments），毫秒级，零网络成本。恢复模型丢失的上下文。
- **Mid-session on-demand**：模型在对话中发现需要更多上下文时，自己调用 `bible_memory_search` / `bible_knowledge_search` MCP 工具。搜索由用户实时输入驱动，相关性远高于 speculative pre-fetching。
- BiBLE Atlas 的价值体现为跨 session 的知识发现——经年累月积累的 memories、knowledge bases、specifications、lessons learned，在对话需要时精准出现。

**解决方案**：Q7 重写为 pull 模型，recall config 分离为 `injection`（本地参数）和 `search`（MCP 默认参数），`/context/inject` 不再调 BiBLE Atlas，graceful degradation 规则更新。已更新可行报告和 CLAUDE.md。

**状态**：✅ Resolved

### 场景逐例确认

**场景 1：冷启动，全新 session，无 crash 遗留**

```
用户打开 Claude Code，开始一个新项目。
session_id 不在 sessions 表中。

SessionStart hook 触发
  → hook 脚本确保 daemon 运行
  → POST /session/start → 无 unclosed session → 创建新 session 行
  → POST /context/inject → 查本地 buffer → 空（无 turns、无 moments）
  → 注入空 <relevant-memories>
  → 模型冷启动，无历史上下文
```

**关键结论：**
1. 此时用户还没说话，不知道要聊什么——push 模型的 speculative search 在此刻是纯粹浪费
2. 注入空是正确行为，模型空手开局
3. 何时需要历史记忆，**选择权完全在模型手里**——用户在对话中提到具体 topic 时，模型自行调用 `bible_memory_search`
4. 不存在"先本地后 BiBLE"的两级检索——MCP 工具直接查 BiBLE Atlas。本地 buffer 只在 SessionStart 注入时一次性使用

**场景 2：`/clear` / compact（同 session 恢复）**

```
/clear 或 compact → SessionStart hook 触发
  → POST /session/start → sessions 表已有此 session_id → 幂等返回，无状态变更
  → POST /context/inject → 本地 buffer 有数据
     → 注入 turns 摘要 + unflushed moments（如有）
  → 模型恢复上下文，继续对话
  → 全程零 BiBLE 调用
```

**关键结论：**
1. `/session/start` 对已存在 session 幂等返回
2. `/context/inject` 以本地 buffer 为唯一 source，毫秒级恢复
3. 与全新 session（场景 1）的区别仅在于 buffer 是否有数据
4. 不需要区分 `/clear` 和 compact——行为完全一致

**场景 3：冷启动，有 crash 遗留**

```
昨天：session "abc-123" 异常终止（SIGTERM / daemon crash）
  → 15 turns + 3 个 Phase 1 moments (flushed=0) 留在 SQLite
  → sessions 表 status 仍为 'active'

今天：用户回到项目，打开 Claude Code（新 session_id: "def-456"）

SessionStart hook 触发
  → hook 脚本确保 daemon 运行
  → POST /session/start {session_id: "def-456"}
     → daemon 扫描 sessions 表 → 发现 unclosed session "abc-123"
     → 同步（快路径）：从 SQLite 读取 "abc-123" 的 unflushed moments + turns 摘要
     → 异步（慢路径）：队列 Phase 2 回顾检测 + flush BiBLE，完成后给用户 hint
     → 标记 "abc-123" 为 closed，创建新 session "def-456"
  → POST /context/inject {session_id: "def-456"}
     → 注入 "abc-123" 的 crash recovery moments + turns 摘要
     → 模型获得了上次中断前的上下文
```

**关键结论：**
1. Crash recovery 分快慢两路：快路（已有 SQLite 数据，毫秒级）不阻塞用户；慢路（Phase 2 LLM + flush BiBLE）异步后台跑
2. 异步完成后给用户 hint，告知检测结果
3. 如果 Phase 2 发现新 moments，单独 flush 到 BiBLE，后续对话通过 `bible_memory_search` 命中
4. 三个场景的注入 source 统一为"本地 buffer 有什么就注入什么"——无非是 buffer 内容不同

---

## Finding 6 (Major → Resolved): `.mcp.json` 内容与实际实现不匹配

**场景**：开发者完成 Python MCP server 实现后，部署 `.mcp.json`，照着可行报告的旧版内容写成 `{"command": "bun", "args": ["run", "src/mcp/server.ts"]}`。MCP server 启动失败，因为实际入口是 `uv run python -m bible_cc_plugin.mcp.server`。

**讨论结论**：
1. **TS→Python 的修复**已在 Finding 1 中完成——`.mcp.json` 同步改为 `"command": "uv"`, `"args": ["run", "python", "-m", "bible_cc_plugin.mcp.server"]`
2. **Server name**: 从 `"bible-atlas"` 改为 `"bible-cc"`——这是 bible-cc-plugin 的 MCP server，不是 BiBLE Atlas 本身
3. **PATH 依赖**: `uv` 在 PATH 上是前提条件，Setup hook 负责确保已安装。与 claude-mem 依赖 `node` 同理，不需要在 `.mcp.json` 里做路径发现

**状态**：✅ Resolved

---

## Finding 7 (Minor → Resolved): 中段检测的异步语义与 hook timeout 矛盾

**问题**：设计说检测是 async non-blocking，但 hooks.json 里 UserPromptSubmit 的 timeout 写 5000ms，暗示同步等待。hook 只做 HTTP POST + 排队，检测是后台异步跑的，5000ms 纯浪费等待时间。

**讨论结论**：timeout 降到 3000ms（保守覆盖 HTTP 往返 + daemon 接收），注释标注 "non-blocking — detection runs async, hook returns immediately"。

**状态**：✅ Resolved

---

## Finding 8 (Minor → Resolved): 250 字符 tool result 截断过于激进

**问题**：机械截断前 250 字符，关键信息常在中后段被丢弃，moment detector 只能从残缺信息中判断。

**讨论结论**：不要机械截断——完整 tool output 存入 turns 表，moment detector 的 LLM 在判断 key moment 的同时从完整 output 中提取 ≤250 字符精华。不需要额外 LLM 调用，semantic 不变（`tool_result_max_chars` 默认仍是 250，但含义从"截断上限"变为"摘要上限"）。

**状态**：✅ Resolved

---

## Finding 9 (Minor → Expanded): `/bible-cc:review` 端点到完整 Command 盘点

**原始 issue**：CLAUDE.md 定义了 `GET|DELETE|PUT /daemon/moments` 三个 review 端点，但可行报告的 HTTP API 章节缺失。

**展开讨论**：与其修一个漏，不如完整梳理 bible-cc-plugin 的 command surface。参考了 BiBLE Atlas API、claude-mem 的 skill 体系、以及可预见的用户场景，共列举了 **82 个可能的命令/操作**，覆盖 19 个域。以下是全部清单，待后续筛减。

### A. Daemon 生命周期

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 1 | `/bible-cc:setup` | 首次安装。配 URL、写 config、启动 daemon | ✅ P0 |
| 2 | `/bible-cc:status` | daemon 跑着没？BiBLE 通不通？buffer 里多少数据？ | ✅ P0 |
| 3 | `/bible-cc:start` | daemon 意外挂了，手动拉起来 | ❌ P3 |
| 4 | `/bible-cc:stop` | 调试时想停 daemon | ❌ P3 |
| 5 | `/bible-cc:restart` | 改完 config 想让它生效 | ❌ P3 |
| 6 | `/bible-cc:logs` | "刚才发生了什么？hook 调成功了吗？" | ⚠️ P2 |
| 7 | `/bible-cc:ping` | 快速确认 daemon 活着（比 status 轻量） | ❌ P3 |
| 8 | `/bible-cc:doctor` | 全面自检：config、daemon、BiBLE、SQLite、schema 版本 | ⚠️ P1 |

### B. Session 管理

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 9 | `/bible-cc:save` | 刚做了重要决策，立刻 flush | ✅ P0 |
| 10 | `/bible-cc:recall` | "我刚才在聊什么来着？" 手动注入 buffer | ⚠️ P1 |
| 11 | `/bible-cc:review` | 浏览/编辑/删除 pending moments | ✅ P0 |
| 12 | `/bible-cc:sessions` | daemon 里有哪些 session 还活着？ | ⚠️ P2 |
| 13 | `/bible-cc:session-info` | 当前 session 的详细统计 | ❌ P3 |
| 14 | `/bible-cc:context` | 调试：上次注入到模型的是什么东西？ | ⚠️ P2 |

### C. 配置管理

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 15 | `/bible-cc:config` | "BiBLE URL 配的什么？模型用的哪个？" | ⚠️ P2 |
| 16 | `/bible-cc:config-set` | "换个 BiBLE 地址" 无需手改 JSON | ⚠️ P2 |
| 17 | `/bible-cc:config-reset` | 改乱了想回到默认 | ❌ P3 |

### D. 数据缓冲管理

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 18 | `/bible-cc:buffer` | "buffer 里现在有什么？" 显示当前 session 的 turns | ⚠️ P2 |
| 19 | `/bible-cc:buffer-clear` | "刚才聊的太乱了，清掉 buffer 重新开始" | ❌ P3 |
| 20 | `/bible-cc:sync-status` | "还有多少 moments 没 flush 到 BiBLE？" | ⚠️ P2 |
| 21 | `/bible-cc:sync-force` | 全局 flush 所有 pending moments | ⚠️ P2 |

### E. 采集控制

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 22 | `/bible-cc:capture-pause` | "下面聊的内容私人，别记录" | ⚠️ P1 |
| 23 | `/bible-cc:capture-resume` | "可以继续记录了" | ⚠️ P1 |
| 24 | `/bible-cc:capture-mode` | 切换采集模式（key_moments / full） | ❌ P3 |
| 25 | `/bible-cc:bypass-add` | "这个项目永远不记录" | ⚠️ P2 |
| 26 | `/bible-cc:bypass-list` | "哪些项目被跳过了？" | ❌ P3 |

### F. 数据修复与恢复

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 27 | `/bible-cc:recover` | 手动触发 crash recovery | ⚠️ P1 |
| 28 | `/bible-cc:retry-flush` | "上次 flush 失败了，重试" | ⚠️ P2 |
| 29 | `/bible-cc:repair-db` | SQLite 损坏时尝试修复 | ❌ P3 |

### G. 数据可移植性

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 30 | `/bible-cc:db-backup` | 备份 daemon.db | ❌ P3 |
| 31 | `/bible-cc:db-stats` | "数据库多大了？多少行？" | ❌ P3 |
| 32 | `/bible-cc:migrate-from` | 从 claude-mem 或其他工具迁移数据 | ❌ P3 |
| 33 | `/bible-cc:export` | 把本地数据导出为可读格式 | ❌ P3 |

### H. BiBLE 数据管理（建议做 MCP tool 而非 command）

| # | Tool | 场景 | 结论 |
|---|------|------|------|
| 34 | `bible_memory_delete` | "这条记忆是错的，删掉" | ⚠️ P1 |
| 35 | `bible_memory_edit` | "这个 title 不够准确，改一下" | ⚠️ P2 |
| 36 | `bible_knowledge_delete` | 删除过时的知识 | ❌ P3 |
| 37 | `bible_knowledge_add` | 手动添加一条知识 | ❌ P3 |

### I. 调试与开发

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 38 | `/bible-cc:version` | "装的哪个版本？" | ⚠️ P2 |
| 39 | `/bible-cc:help` | "有哪些命令可用？" | ⚠️ P2 |
| 40 | `/bible-cc:test-connectivity` | "BiBLE 到底通不通？" | ⚠️ P2 |
| 41 | `/bible-cc:reset` | 核选项——清空所有本地状态 | ❌ P3 |

### J. Token 统计

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 42 | `/bible-cc:token-usage` | "这个 session 用了多少 token？花了多少钱？" | ⚠️ P1 |
| 43 | `/bible-cc:token-injection` | "注入的 memories 块占了多少 token？" | ⚠️ P2 |
| 44 | `/bible-cc:token-cost` | "按当前模型价格，这个 session 花了多少钱？" | ⚠️ P1 |
| 45 | `/bible-cc:token-summary` | 按 turn 拆解 token 消费明细 | ⚠️ P2 |
| 46 | `/bible-cc:token-moment` | moment detection 的 LLM 调用花了多少 token？ | ⚠️ P2 |
| 47 | `/bible-cc:token-project` | 这个项目总共花了多少 token？跨 session 汇总 | ⚠️ P2 |
| 48 | `/bible-cc:token-peak` | 哪个 turn 的 token 消耗最高？ | ❌ P3 |
| 49 | `/bible-cc:token-alert` | 设置 token 预警阈值 | ❌ P3 |

### K. Memory 探索与导航

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 50 | `/bible-cc:memory-timeline` | "我这周做了什么？" 时间线浏览 | _待筛_ |
| 51 | `/bible-cc:memory-graph` | 可视化记忆网络 | _待筛_ |
| 52 | `/bible-cc:memory-top` | "我最近聊的最多的话题是什么？" | _待筛_ |
| 53 | `/bible-cc:memory-gaps` | "哪些话题很久没碰了？" 发现知识盲区 | _待筛_ |
| 54 | `/bible-cc:memory-duplicates` | "有没有重复的记忆？" | _待筛_ |
| 55 | `/bible-cc:memory-tag` | "给这条记忆打标签" | _待筛_ |
| 56 | `/bible-cc:memory-tags` | "我有哪些标签？" | _待筛_ |
| 57 | `/bible-cc:memory-merge` | "这两条其实说的是同一件事，合并" | _待筛_ |
| 58 | `/bible-cc:memory-fork` | "从一条记忆分支出一个新话题" | _待筛_ |
| 59 | `/bible-cc:memory-bookmark` | "这条很重要，收藏" | _待筛_ |
| 60 | `/bible-cc:memory-bookmarks` | "我收藏了哪些记忆？" | _待筛_ |

### L. 搜索与分析

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 61 | `/bible-cc:search-history` | "我之前搜过什么？" | _待筛_ |
| 62 | `/bible-cc:search-suggest` | 搜索建议 | _待筛_ |
| 63 | `/bible-cc:search-across` | 所有 domain 跨域搜索 | _待筛_ |
| 64 | `/bible-cc:analyze` | "分析我的工作模式" | _待筛_ |
| 65 | `/bible-cc:trends` | "这个月我在哪些话题上花的时间最多？" | _待筛_ |

### M. 协作与团队

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 66 | `/bible-cc:team-activity` | "团队成员最近在做什么？" | _待筛_ |
| 67 | `/bible-cc:team-search` | "在团队的记忆中搜索" | _待筛_ |
| 68 | `/bible-cc:share-memory` | "把这条记忆分享给团队成员" | _待筛_ |
| 69 | `/bible-cc:handoff` | "把这个 session 的上下文打包给同事" | _待筛_ |
| 70 | `/bible-cc:team-overlap` | "团队里有谁在做类似的事情？" | _待筛_ |
| 71 | `/bible-cc:team-notes` | "给团队留一条笔记" | _待筛_ |

### N. 自动化与调度

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 72 | `/bible-cc:daily-digest` | "生成今天的工作摘要" | _待筛_ |
| 73 | `/bible-cc:weekly-report` | "这周做了什么？导出报告" | _待筛_ |
| 74 | `/bible-cc:digest-schedule` | "每天早上 9 点自动生成昨日摘要" | _待筛_ |
| 75 | `/bible-cc:remind` | "下次聊到 X 话题时提醒我" | _待筛_ |
| 76 | `/bible-cc:reminders` | "我设了哪些提醒？" | _待筛_ |
| 77 | `/bible-cc:follow-up` | "上次提到的 X 还没跟进" | _待筛_ |

### O. 隐私与合规

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 78 | `/bible-cc:privacy-audit` | "有没有敏感信息被记录下来了？" | _待筛_ |
| 79 | `/bible-cc:redact` | "把这条记忆中的密码/API key 删掉" | _待筛_ |
| 80 | `/bible-cc:forget-project` | "忘掉这个项目的所有记忆" | _待筛_ |
| 81 | `/bible-cc:forget-session` | "忘掉这个 session" | _待筛_ |
| 82 | `/bible-cc:data-request` | "我的数据都在哪？导出全部个人数据" | _待筛_ |

### P. 性能与健康

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 83 | `/bible-cc:perf` | daemon 响应延迟监控 | _待筛_ |
| 84 | `/bible-cc:slow-queries` | SQLite 慢查询 | _待筛_ |
| 85 | `/bible-cc:memory-usage` | daemon 内存占用 | _待筛_ |
| 86 | `/bible-cc:disk-usage` | daemon.db 大小和增长速度 | _待筛_ |
| 87 | `/bible-cc:gc` | 清理 30 天前的临时数据 | _待筛_ |

### Q. 多项目 / 多工作空间

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 88 | `/bible-cc:projects` | "我在哪些项目里积累过记忆？" | _待筛_ |
| 89 | `/bible-cc:project-switch` | "切换到另一个项目的上下文" | _待筛_ |
| 90 | `/bible-cc:project-context` | "这个项目的整体上下文是什么？" | _待筛_ |
| 91 | `/bible-cc:project-compare` | "对比两个项目的记忆" | _待筛_ |

### R. 插件升级与维护

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 92 | `/bible-cc:upgrade` | "有新版本吗？升级" | _待筛_ |
| 93 | `/bible-cc:changelog` | "最近更新了什么？" | _待筛_ |
| 94 | `/bible-cc:uninstall` | "我不想用了，完整卸载" | _待筛_ |

### S. 实验 / 趣味

| # | Command | 场景 | 结论 |
|---|---------|------|------|
| 95 | `/bible-cc:personality` | "分析我的工作风格" | _待筛_ |
| 96 | `/bible-cc:wrapped` | "年度回顾——今年聊了什么" | _待筛_ |
| 97 | `/bible-cc:quiz` | "从我的记忆出题考考我" | _待筛_ |
| 98 | `/bible-cc:fortune` | "随机从历史记忆中抽一条给我看" | _待筛_ |

**总计：98 个候选命令/工具，覆盖 19 个域。** 其中 A-E、I、J 域已给出初步优先级（P0/P1/P2/P3），K-S 域待筛选。

**状态**：展开讨论中，待明天筛选落定。

---

## Finding 10 (Minor → Resolved): 缺少 daemon 端口冲突处理

**场景**：9777 端口被占，daemon 起不来。用户如何知道？

**讨论结论**：

1. **端口选择**：默认 9777（冷门端口，短期内够用），通过 config.json `daemon.port` 可配。不需要 UID-based 计算（多用户场景极少）。

2. **冲突处理**：默认行为是报错并通知用户。提供配置项 `daemon.port_auto_fallback`（默认 `false`），开启后 port+1 重试直到找到可用端口。

3. **故障通知**：复用 key moment detection 的 CLI hint 机制（hook stdout），但以错误样式高亮，例如：
   ```
   ⎿ ❌ bible-cc daemon failed to start on port 9777 (address in use).
       Run /bible-cc:status for details.
   ```
   SessionStart hook 脚本检测到 daemon 启动失败后输出这条 hint，用户在下个 turn 看到。后续所有 turn hooks 静默跳过（graceful degradation）。

**状态**：设计方案已定，通知实现方式待确认

> **TODO**: 确认 Claude Code 支持的故障通知方式（hook stdout hint 是否为最佳路径，是否有 status bar API、toast、notification 等替代方案），选择最合适的一种后再落地。

---

## Summary

| # | Finding | Priority | Status |
|---|---------|----------|--------|
| 1 | 可行报告(TS) vs CLAUDE.md(Python) 语言矛盾 | Critical | ✅ resolved |
| 2 | SIGTERM 导致缓冲数据丢失 | Critical | ✅ resolved |
| 3 | Phase 1/2 检测去重缺失 | Major | ✅ resolved |
| 4 | SQLite 多 session 并发写冲突 | Major | ✅ resolved |
| 5 | SessionStart 触发 injection 过频 | Major | ✅ resolved |
| 6 | `.mcp.json` 内容与实际不匹配 | Major | ✅ resolved |
| 7 | hook timeout 语义不清 | Minor | ✅ resolved |
| 8 | tool result 截断过于激进 | Minor | ✅ resolved |
| 9 | review 端点遗漏 | Minor | pending |
| 10 | daemon 端口冲突静默失败 | Minor | 设计方案已定，通知机制 TODO |
