# Phase 3c: Connectivity Check + Graceful Degradation + Debuggability

> **依赖**: Phase 3b（flush + MCP 真实调用就绪，才能测 degradation 行为）
> **被依赖**: Phase 3d（集成测试需要 connectivity 端点和 debug 端点来验证行为）
> **父文档**: [Phase 3 总览](../plans/2026-06-13-phase-3-bible-integration.md)

**交付 Command**: 无新 command。connectivity 信息通过 `/daemon/health` 和 `/bible-cc:status`（Phase 5）呈现。

**预估: 1 天**

### 测试标注

3c.1（connectivity check）使用 FastAPI TestClient → `[Integration] [Post]`。3c.2（graceful degradation）和 3c.3（debug 端点）依赖 3b 的端点就绪 → `[Integration] [Post]`。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](../plans/2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## Scenario

### 连通性检查

> 用户运行 `/bible-cc:status`。首先要看到的是 BiBLE Atlas 是否可达。`/daemon/health` 扩展 `bible_connectivity` 字段——后台测 BiBLE `/health`，超时 3s 不阻塞 health check 返回。Verbose 模式输出 DNS → TCP → HTTP 三级连通性分解，精确定位断连原因。

### Graceful Degradation

> CLAUDE.md 硬性约束：BiBLE 断连时本地操作不中断。Flush 失败 → moments 保持 `flushed=0`（不丢数据）。MCP tools BiBLE 不可达 → structured error（不抛异常、不阻塞模型 turn）。不自动重试（mark, notify, move on）。用户在 `/bible-cc:status` 看到 `bible_connectivity.reachable=false` 即可知情。

### Debuggability

> Phase 3 首次引入外部网络通信（BiBLE Atlas）。网络问题是排查频率最高的故障类型。每个 BiBLE API 调用的请求/响应必须可追溯。Flush 是数据离开本地到达 BiBLE 的唯一通道——丢失 moment 是严重 bug，flush 的每一步都需要诊断信息。

---

## Feature 逐个讨论

### F3c.1 — BiBLE Connectivity Check

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 硬性约束——graceful degradation 要求用户随时知道 BiBLE 连通状态。`bible_connectivity: {reachable, latency_ms}` 是 `/daemon/health` 的必需字段。 |
| **优先级** | P0 |
| **依赖** | client.py（`check_health()`） |

**集成到 `/daemon/health`**：
```json
{
  "status": "ok",
  "bible_connectivity": {
    "reachable": true,
    "latency_ms": 15,
    "base_url": "http://localhost:5555"
  }
}
```

**Verbose 模式**（`check_health(verbose=True)`）：
```
BiBLE Connectivity Test (base_url=http://localhost:5555)
  DNS resolution... OK (5ms) → 127.0.0.1
  TCP connect... OK (2ms)
  HTTP GET /health... OK (15ms) → status=200
  Total: reachable (22ms)
```
失败时：
```
BiBLE Connectivity Test (base_url=http://bible.atlas.internal:5555)
  DNS resolution... FAIL → NXDOMAIN
  Total: unreachable — check base_url config
```

**关键行为**：
- 后台异步检测，超时 3s 不阻塞 health check 返回
- 超时后 `reachable=false, latency_ms=null, error="timeout"`
- `/daemon/health` 不依赖 BiBLE——即使 BiBLE 不可达也返回 200

### F3c.2 — Graceful Degradation

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 硬性约束。BiBLE 断连时 daemon 正常运行，health check 显示 reachable=false，stderr 可见超时日志。 |
| **优先级** | P0 |
| **依赖** | F3b.1（flush logic）、F3b.2（MCP tools） |

**行为规范**：
- Flush 失败 → moments 保持 `flushed=0`，`retry_count` 递增，日志输出错误原因
- MCP tools BiBLE 不可达 → structured error（不抛异常）
- 不自动重试（mark, notify, move on）
- `/daemon/health` 始终返回 200（即使 BiBLE 不可达）

### F3c.3 — Debug 端点

| 属性 | 说明 |
|------|------|
| **理由** | 跨网络通信需要可追溯。Flush 的每一步都需要诊断信息。BiBLE API 请求历史是排查问题的第一手资料。 |
| **优先级** | P0 |
| **依赖** | F3b.1（flush logic） |

**新增端点**：
- `GET /daemon/debug/flush-log?session_id=X` → 该 session 的 flush 历史：
  ```json
  {
    "session_id": "abc123",
    "flushes": [
      {
        "time": "2026-06-24T15:30:00Z",
        "moments_count": 3,
        "task_id": "xyz-456",
        "status": "ok",
        "latency_ms": 1200
      }
    ]
  }
  ```
- `GET /daemon/debug/bible-requests?limit=50` → 最近 50 个 BiBLE API 请求日志

**实现方式**：在 `client.py` 的每次 API 调用时，将请求元数据（method、URL、status、latency、result summary）写入内存 ring buffer（`collections.deque`，maxlen=200）。debug 端点返回 ring buffer 内容。

---

## 实现顺序

```
F3c.1 (connectivity) ──► F3c.2 (degradation) ──► F3c.3 (debug 端点)
```

| 顺序 | Feature | 理由 |
|------|---------|------|
| **1st** | F3c.1 connectivity | 独立——只依赖 client.py 的 `check_health()` |
| **2nd** | F3c.2 degradation | 依赖 F3b.1 + F3b.2 的 flush 和 MCP 行为就绪 |
| **3rd** | F3c.3 debug 端点 | 依赖 F3b.1 的 flush 链路 + client.py 的请求追踪 ring buffer |

---

## 验收标准

### Connectivity
- [ ] `GET /daemon/health` 返回 `bible_connectivity: {reachable, latency_ms}`
- [ ] `GET /daemon/health?verbose=true` 返回 DNS → TCP → HTTP 三级连通性分解
- [ ] BiBLE 不可达时 health check 仍返回 200（不 crash）
- [ ] BiBLE 不可达时 `bible_connectivity.reachable=false`

### Degradation
- [ ] Flush 失败 → moments 保持 `flushed=0`，`retry_count` 递增
- [ ] Flush 失败 → stderr 输出错误原因
- [ ] MCP tools BiBLE 不可达 → structured error（不抛异常）
- [ ] 不自动重试

### Debug 端点
- [ ] `GET /daemon/debug/flush-log?session_id=X` 返回 flush 历史
- [ ] `GET /daemon/debug/bible-requests?limit=50` 返回 BiBLE API 请求历史
- [ ] Ring buffer 容量 200，溢出时最旧记录被覆盖

---

## 产出文件

```
src/bible_cc_plugin/daemon/server.py     ← (修改: health 增强 + debug 端点)
src/bible_cc_plugin/daemon/client.py     ← (修改: request ring buffer)
src/bible_cc_plugin/daemon/buffer.py     ← (修改: retry_count 递增逻辑)
```
