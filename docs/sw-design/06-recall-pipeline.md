# 06 — Recall Pipeline

> L2 | 领域总览 | 定义了回忆管线的两条路径：SessionStart 本地注入（local buffer）+ mid-session on-demand BiBLE pull（MCP tools + consult）。

---

## 1. 定位

```
SessionStart (hook-driven)              Mid-session (model/user-driven)
  │                                        │
  ▼                                        ▼
POST /context/inject                  MCP tools (model) / consult (user)
  → local SQLite ONLY                   → BiBLE V4 domain search endpoints
  → turns + moments                    → cross-session knowledge
```

**核心原则**: `/context/inject` 只看本地 buffer。跨 session 知识由模型通过 MCP 工具主动 pull。`/bible-cc:consult` 是用户手动版本。

---

## 2. 全局约束

1. **SessionStart 纯本地**: `/context/inject` 不调用 BiBLE API。
2. **三种场景分支**: 新 session / `/clear` (compact) / crash recovery——注入内容取决于 buffer 状态（见 `local-injection.md`）。
3. **MCP 工具无状态**: 纯 BiBLE API 封装，不依赖 daemon。
4. **consult 跨域并行**: 无 query 时 LLM 归纳对话→并行调三域 search→合并结果→注入。
5. **6 活跃 + 2 postponed**: MCP 工具中 6 个活跃，2 个 postponed（delete, list）。

---

## 3. 子模块

| 文件 | 内容 | 状态 |
|------|------|------|
| `06-recall/local-injection.md` | SessionStart 三个场景的注入逻辑 | ✅ 完成 |
| `06-recall/consult.md` | 用户主动跨域搜索 | ✅ 完成 |
| `06-recall/mcp-tools.md` | MCP 工具 schema、BiBLE V4 映射、错误处理 | ✅ 完成 |

---

## 4. 参考文档

- [`01-architecture-overview.md`](01-architecture-overview.md) — Pull model、三种 SessionStart 场景
- [`02-interfaces.md`](02-interfaces.md) — `/context/inject`, `/daemon/consult`, MCP tool schema, BiBLE V4 API
- [`04-config.md`](04-config.md) — injection / search config 域
- [`../../CLAUDE.md`](../../CLAUDE.md) — Context recall scenarios、Command ↔ MCP separation
