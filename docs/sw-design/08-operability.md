# 08 — Operability

> L2 | 领域总览 | 定义了通知机制（hint system）、诊断命令（status/check-bible/context）、故障场景与恢复路径。目标是让用户永远知道 plugin 在做什么、出了什么问题、怎么修。

---

## 1. 定位

Operability 覆盖三个维度：

| 维度 | 说明 | 对应 L3 |
|------|------|---------|
| **通知** | moment hint + error hint：用户如何感知 plugin 状态变化 | `hint-system.md` |
| **诊断** | status / check-bible / context 命令：用户如何检查 plugin 和 BiBLE 状态 | `status.md` |
| **恢复** | 故障场景 → 诊断路径 → 恢复操作 | `failure-paths.md` |

核心原则：**用户永远不需要翻 daemon 日志来排障。** hint + status 命令能覆盖所有常见故障。

---

## 2. 全局约束

1. **通知走 hook stdout**：唯一可用通道。显示位置是 conversation transcript inline。`inject: true` 同时进入 system prompt。
2. **错误不可静默**：daemon 起不来、BiBLE 断连等必须产生用户可见 hint。只有 hook 调用 daemon 失败时可以静默跳过。
3. **状态命令零依赖**：`/bible-cc:status` 不依赖 BiBLE 可达（显示连通性状态，但不因此报错）。`/bible-cc:check-bible` 是唯一明确测试 BiBLE 连通性的命令。
4. **恢复操作幂等**：`recover`、`retry-push` 等恢复命令可安全重复执行。

---

## 3. 子模块

| 文件 | 内容 | 状态 |
|------|------|------|
| `08-operability/hint-system.md` | 通知机制：moment hint、error hint、format、inject、消息模板 | ✅ 完成 |
| `08-operability/status.md` | status / check-bible / context 命令的诊断逻辑和输出格式 | ✅ 完成 |
| `08-operability/failure-paths.md` | 故障场景 → 诊断路径 → 恢复操作的完整映射 | ✅ 完成 |

---

## 4. 参考文档

- [`02-interfaces.md`](02-interfaces.md) — Hook stdout、error hint 机制、`/daemon/health`
- [`03-daemon.md`](03-daemon.md) — 启动序列、端口冲突、错误处理策略
- [`../../CLAUDE.md`](../../CLAUDE.md) — Hint notification、Graceful degradation
