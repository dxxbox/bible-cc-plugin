# Phase 1d: Operability Polish

> **依赖**: Phase 1c（完整 daemon HTTP API + SQLite 数据）
> **被依赖**: Phase 2（采集管线）
> **父文档**: [Phase 1 总览](2026-06-13-phase-1-daemon-core.md)

**交付 Command**: `/bible-cc:diagnose`（骨架，新增 `commands/diagnose.md`，调 `GET /daemon/health?verbose=true`）

**预估: 1 天**

### 测试标注

混合——hint 构建函数 `[Unit] [Pre]`，端口探测 + debug 端点 `[Integration] [Post]`，health verbose schema `[Contract] [Post]`。逐条标注。

> 标注约定详见 [Phase 1 总览 §测试环境标注约定](2026-06-13-phase-1-daemon-core.md#测试环境标注约定全文适用)。

---

## 4. Sub-Phase 1d: Operability Polish（1d）

### Scenario

> 用户尝试启动 daemon，但 9777 端口已被另一个进程占用。SessionStart hook 检测到 daemon 无法启动 → stdout 输出 error hint：`❌ bible-cc daemon failed to start on port 9777 (occupied by pid 1234 / python3.12)。Fix: free the port, or set daemon.port_auto_fallback: true`。用户看到了错误并修复了端口。
>
> Daemon 成功启动后，启动序列 6 步日志输出到 stderr，每步带 timing。Health check verbose mode (`GET /daemon/health?verbose=true`) 返回详细诊断信息——启动耗时、每张表的行数、WAL 文件大小。用户用 `./bible-cc status --verbose` 就能看到完整的健康状态。
>
> 所有 HTTP 请求带 `X-Request-ID` header，日志中可追溯单个请求的完整生命周期。用户遇到问题后执行 `/bible-cc:diagnose`，首先检查 daemon 是否可达、health 是否 ok。

**交付 Command**: `/bible-cc:diagnose`

Phase 1 骨架——仅检查 daemon 组件。用户输入 `/bible-cc:diagnose`，CC 执行 `commands/diagnose.md` → 调 `GET /daemon/health?verbose=true` → 逐项检查并展示 PASS/FAIL：

```
daemon:        PASS  127.0.0.1:9777  pid=12345  uptime=2h
sqlite:        PASS  integrity=ok  schema_version=1
ports:         PASS  9777 free
config:        PASS  5 sources traced
logs:          PASS  ~/.bible-cc/daemon.log
```

Phase 5 扩展为检查 4 组件（daemon + MCP + hooks + commands）的完整 diagnose。

---

### Feature 1d.1: 端口冲突检测 + error hint

**Scenario**: Daemon 启动 Step 6（uvicorn）发现端口被占 → 检测占用进程（`lsof -ti :{port}`）→ 构建 error hint → SessionStart hook stdout + inject:true → 用户和模型都能看到错误信息。

| 属性 | 说明 |
|------|------|
| **理由** | CLAUDE.md 硬性约束——端口被占不能静默，必须产生用户可见 error hint。默认行为（port_auto_fallback=false）是 fail-fast + notify，因为静默切换端口会导致 hook 脚本指向错误端口。 |
| **优先级** | P1 — 运维基础 |
| **依赖** | 1a.1（daemon 启动序列）、1a.5（config 加载）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/port-conflict.md` | §2 默认行为 | fail-fast + error hint 流程，`lsof -ti` 检测占用进程 |
| `03-daemon/port-conflict.md` | §3 auto_fallback | probe 最多 10 次 port+1，`SO_REUSEADDR` |
| `03-daemon/port-conflict.md` | §5 分离关注点 | Hook 不探测端口——daemon 负责 |
| `03-daemon/startup.md` | §1.1 Step 2-6 | port resolution + uvicorn 启动 |
| `08-operability.md` | §2 | "错误不可静默"，通知走 hook stdout |
| `08-operability/failure-paths.md` | §F1 | 端口被占的完整诊断→恢复路径 |
| `08-operability/hint-system.md` | （如已写）| error hint 格式模板 |
| `04-config/schema.md` | §2.2 | `daemon.port: 9777`, `port_auto_fallback: false` |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| 端口被占（无 fallback） | uvicorn `OSError [Errno 48]` → catch | SessionStart hook stdout: `❌ port N occupied by pid X (process_name)` | `daemon.log` 搜索 `port.*occupied` | ① `kill <pid>` 释放端口 ② 改 config `daemon.port` ③ 开启 `port_auto_fallback: true` |
| lsof 不可用（极端环境） | `FileNotFoundError` → 仅报端口被占 | hint 不含 pid 信息——`❌ port N occupied (cannot identify process)` | `daemon.log` 搜索 `lsof not available` | 安装 lsof，或手动 `netstat -an` 查找占用进程 |
| auto_fallback 全部 10 次尝试失败 | `PortExhaustedError` | hint: `❌ all ports N..N+9 occupied` | `daemon.log` 搜索 `port exhausted.*range` | 释放端口范围内的某个端口，或改 `daemon.port` 到远离冲突范围的端口 |
| auto_fallback 找到新端口 | probe 成功 → 在新端口启动 | 无感知——daemon health 返回实际端口。`/bible-cc:status` 显示新端口 | `daemon.log` 搜索 `port auto-selected` | 可选：更新 config.json 中 `daemon.port` 为新端口（但不会自动写入） |

**Function-Level Steps**（按实现顺序）:

```python
# server.py 或新文件 daemon/port_manager.py

def get_port_owner(port: int) -> tuple[int, str] | None:
    """用 lsof -ti :{port} 查找占用端口的进程 pid + name。
    返回 (pid, process_name) 或 None（无法检测时）。
    """

def find_available_port(start_port: int, max_attempts: int = 10) -> int:
    """从 start_port 开始递增探测可用端口（socket.bind + SO_REUSEADDR）。
    返回第一个可用端口，全部占用时抛 PortExhaustedError。
    """

def build_port_conflict_hint(port: int, owner: tuple[int, str] | None) -> str:
    """构建 port conflict error hint 文本。
    格式: ❌ bible-cc daemon cannot start on port {port}. Port is occupied by pid {pid} ({name})...
    """

# daemon 启动序列中:
# try:
#     uvicorn.run(app, host="127.0.0.1", port=config.daemon.port)
# except OSError as e:
#     if "Address already in use" in str(e):
#         owner = get_port_owner(config.daemon.port)
#         hint = build_port_conflict_hint(config.daemon.port, owner)
#         raise DaemonStartError(hint) from e
```

**交付标准**:

- [ ] 端口被占 → daemon 启动失败 + stderr 可见 "port X occupied by PID Y"
- [ ] `port_auto_fallback=false`（默认）→ 不自动切换端口
- [ ] `port_auto_fallback=true` → 自动探测可用端口（最多 10 次）
- [ ] lsof 不可用时仍能报告端口被占（不含 pid）

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `[Unit] [Pre]` `test_port_occupied_detected_with_lsof` — mock lsof 返回 (1234, "python") → get_port_owner 返回正确
- [ ] `[Unit] [Pre]` `test_find_available_port_returns_first_free` — mock socket.bind → 第二个成功 → 返回 start_port+1
- [ ] `[Unit] [Pre]` `test_find_available_port_exhausted_raises` — mock 全部 bind 失败 → PortExhaustedError
- [ ] `[Unit] [Pre]` `test_build_hint_includes_pid_and_name` — hint 文本含 pid + process_name
- [ ] `[Unit] [Pre]` `test_build_hint_without_lsof` — owner=None → hint 文本提示 "cannot identify process"

*意图测试*:
- [ ] `[Integration] [Post]` `test_port_conflict_never_silent` — **意图: 失败不静默**。端口冲突是致命错误（daemon 无法工作），不能只写一条 log 然后静默退出。验证方法：断言 daemon 启动失败时 exit code ≠ 0 + stderr 含 human-readable error message（不只是 traceback）。
- [ ] `[Integration] [Post]` `test_auto_fallback_does_not_update_config_file` — **意图: 不自动修改用户配置**。Auto_fallback 选到新端口后，config.json 中的 `daemon.port` 保持不变。如果自动写入，下次用户改回原端口反而会覆盖 fallback 结果。验证方法：auto_fallback 后检查 config.json 未被修改。

---

### Feature 1d.2: Debug 端点（schema/turns/tables 内省）

**Scenario**: 开发者遇到问题——"moments 表里到底有什么？"→ `curl http://127.0.0.1:9777/daemon/debug/schema` 返回三表 DDL。`curl .../debug/tables/moments?limit=20` 返回表前 20 行。`curl .../debug/turns?session_id=abc` 返回指定 session 的 turns。这些端点仅在 `--debug` 模式或 `log_level=DEBUG` 时注册，避免生产暴露。

| 属性 | 说明 |
|------|------|
| **理由** | SQLite 是黑盒——没有内省能力就不知道表里有什么。Debug 端点是开发阶段和故障排查的核心工具。必须限制访问——不在 debug 模式时 404。 |
| **优先级** | P1 — 调试基础 |
| **依赖** | 1a.1（表已创建）、1a.2（CRUD 函数）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| 原 Phase 1 plan | F1.11 Debuggability | debug 端点仅在 `--debug` 或 `log_level=DEBUG` 时注册 |
| `03-daemon/sqlite-schema.md` | §2 表结构 | 四表的 DDL（用于返回） |
| `03-daemon/http-api.md` | §5.1 turns 查询 | `SELECT * FROM turns WHERE session_id=? ORDER BY seq` |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| Debug 端点在非 debug 模式下可访问 | 安全审计 | 数据泄露风险 | — | 确保 middleware 拦截：`if not debug_mode: raise HTTPException(404)` |
| Debug 端点返回过多数据（turns 表百万行） | limit 参数缺失 | 响应超时/OOM | `daemon.log` 搜索 `debug.*query.*> 500ms` | 强制默认 limit=20，最大 limit=100 |
| Debug 端点返回敏感数据（tool arguments 中的 token） | 代码审查 | token 泄露 | — | 在 debug 输出中 redact `tool_arguments` 中的 `token`, `api_key`, `secret`, `password` 等字段 |

**Function-Level Steps**（按实现顺序）:

```python
# server.py

# 条件注册 debug routes
def register_debug_routes(app: FastAPI, debug_mode: bool):
    """仅在 debug_mode=True 时注册 /daemon/debug/* 路由。"""

# debug 路由（仅 debug mode）
async def debug_schema():
    """GET /daemon/debug/schema → 返回三表 DDL。"""

async def debug_table_rows(table: str, limit: int = 20):
    """GET /daemon/debug/tables/{name}?limit=20 → 返回表前 N 行 + total row count。"""
    if table not in ('sessions', 'turns', 'moments', 'metrics'):
        raise HTTPException(404)

async def debug_turns_by_session(session_id: str, limit: int = 50):
    """GET /daemon/debug/turns?session_id=X&limit=50 → 返回指定 session 的 turns。"""

# 数据 sanitization
def sanitize_debug_row(row: dict) -> dict:
    """Redact tool_arguments 中的敏感字段（token, api_key, secret, password）。"""
```

**交付标准**:

- [ ] `--debug` 模式 → debug 端点可访问
- [ ] 非 debug 模式 → debug 端点返回 404
- [ ] limit 默认 20，最大 100
- [ ] 敏感字段 redact

**测试用例**（混合——debug 端点 HTTP 测试需要 daemon 进程，sanitization 测试纯函数）:

*功能测试*:
- [ ] `[Integration] [Post]` `test_debug_schema_returns_ddl` — debug mode → GET /daemon/debug/schema → 返回 DDL 文本
- [ ] `[Integration] [Post]` `test_debug_tables_returns_rows` — debug mode → GET /daemon/debug/tables/sessions → 返回 row list + count
- [ ] `[Integration] [Post]` `test_debug_turns_by_session` — debug mode → 指定 session_id → 返回该 session 的 turns
- [ ] `[Integration] [Post]` `test_debug_endpoints_404_in_production` — 非 debug mode → debug 端点返回 404
- [ ] `[Integration] [Post]` `test_debug_table_limit_enforced` — limit=200 → 被 clamp 到 100
- [ ] `[Integration] [Post]` `test_debug_table_invalid_name_404` — table=users → 404

*意图测试*:
- [ ] `[Integration] [Post]` `test_debug_endpoints_redact_secrets` — **意图: 不泄露敏感信息**。插入 tool turn（arguments 含 `{"token": "sk-secret123"}`）→ debug turns 输出中 `sk-secret123` 不出现在结果中。
- [ ] `[Integration] [Post]` `test_debug_mode_not_confused_with_log_level` — **意图: 访问控制**。`log_level=DEBUG` + 非 `--debug` → debug 端点仍 404。

---

### Feature 1d.3: Health Check Verbose Mode

**Scenario**: `GET /daemon/health?verbose=true` 返回标准 health 字段 + 额外诊断信息——启动每步耗时（ms）、每张表的行数、WAL 文件大小、page count、config 来源追溯。用户执行 `./bible-cc status --verbose` 就能看到完整诊断，不需要翻日志。

| 属性 | 说明 |
|------|------|
| **理由** | 标准 health check 返回聚合状态（sessions.active, buffer.total_turns）。Verbose mode 提供细粒度诊断——启动性能、表大小、配置来源——用于故障排查和容量规划。 |
| **优先级** | P1 — 运维基础 |
| **依赖** | 1a.1（表已创建）、1a.5（config 加载）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/http-api.md` | §2.3 | `/daemon/health` 标准 response + verbose 扩展字段 |
| `03-daemon/http-api.md` | §2.3 内部流程 | `PRAGMA integrity_check` + `SELECT COUNT(*)` + 文件大小 |
| `03-daemon/startup.md` | §1.1 各步骤 | WAL、migration、crash recovery 的 timing |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| Startup timing 缺失（daemon 直接启动未走完整序列） | verbose health 中 `startup_timings` 为空 | `./bible-cc status --verbose` 不显示启动耗时 | — | 正常——可能 daemon 是幂等启动（已运行） |
| sqlite.detailed 中 table row counts 异常大 | sessions >10000 或 turns > 100000 | 正常——使用量大 | `daemon.log` 搜索 `table.*row_count` | 考虑 `/bible-cc:gc` 清理 |
| config_sources 含敏感值（token） | config trace 中 token 为明文 | 安全风险 | — | config debug 输出必须遮盖 token：`"bible.token": "***"` |

**Function-Level Steps**（按实现顺序）:

```python
# server.py

async def daemon_health(verbose: bool = False):
    """GET /daemon/health[?verbose=true]。
    标准字段始终返回。verbose=true 时追加 startup_timings, sqlite.detailed, config_sources。
    """

# 辅助函数
def get_startup_timings() -> dict[str, int]:
    """返回 6 步启动每步耗时（ms）。从 daemon startup 记录的 timing dict 读取。"""

def get_sqlite_detailed(conn) -> dict:
    """返回 sqlite 详细诊断：table row counts, WAL 文件大小, page count, index list。"""

def get_config_sources() -> dict[str, str]:
    """返回每个配置项的来源：'default' | 'config.json' | 'env:BIBLE_ATLAS_BASE_URL' 等。
    token 值显示为 "***"。
    """
```

**交付标准**:

- [ ] `GET /daemon/health` 返回标准 8 字段
- [ ] `GET /daemon/health?verbose=true` 追加 startup_timings、sqlite.detailed、config_sources
- [ ] config_sources 中 token 显示为 "***"
- [ ] startup_timings 中 6 步每步 ≥ 0ms

**测试用例**（先于实现编写）:

*功能测试*:
- [ ] `test_health_standard_fields` — 无 verbose → 8 个标准字段存在 + 类型正确
- [ ] `test_health_verbose_has_startup_timings` — verbose=true → startup_timings 含 6 个 key
- [ ] `test_health_verbose_has_sqlite_detailed` — verbose=true → sqlite.detailed 含 table_counts, wal_size, page_count
- [ ] `test_health_verbose_config_sources_no_token_leak` — verbose=true → config_sources 中 bible.token 值为 "***"
- [ ] `test_health_bible_connectivity_null_in_phase1` — Phase 1 → bible_connectivity.reachable = null（BiBLE client 未实现）

*意图测试*:
- [ ] `test_health_never_fails` — **意图: 健康检查不崩溃**。即使子检查失败（如 integrity_check 返回 error），`/daemon/health` 本身也不能抛 500——返回 degraded 状态即可。如果 health 端点在 BiBLE 不可达时崩溃，监控系统会以为 daemon 挂了（实际没问题）。验证方法：mock sqlite3 抛异常 → health 仍返回 200 + status="degraded" + error 描述。
- [ ] `test_verbose_health_adds_diagnostic_value_not_noise` — **意图: 信息密度**。每个 verbose 字段都必须有排障价值——"page_count" 帮用户判断是否需要 VACUUM，"startup_timings" 帮定位哪一步慢。不添加"好看但没用"的字段。验证方法：每个 verbose 字段在 failure-paths.md 中至少有 1 个故障场景引用它。

---

### Feature 1d.4: Request-ID Middleware + 启动诊断日志

**Scenario**: 每个 HTTP 请求生成 UUID4 `X-Request-ID` → FastAPI middleware 注入到 request.state → 所有该请求的日志自动带 request_id → response header 中返回 `X-Request-ID`。启动时 6 步诊断日志输出到 `~/.bible-cc/daemon.log`，格式：`[daemon] Step N/6: description... OK (Xms)`。

| 属性 | 说明 |
|------|------|
| **理由** | 全链路 request-id 追踪是排障基础——当用户报告"昨天的 turn 没存上"，可以通过 request-id 在 daemon.log 中定位具体请求。启动诊断日志让用户能看到 daemon 是否成功启动、哪一步耗时最久。 |
| **优先级** | P1 — 运维基础 |
| **依赖** | 1a.1（启动序列）、1b.1-1b.3（HTTP 端点）|

**SW Design 引用**:

| 文件 | 章节 | 引用内容 |
|------|------|---------|
| `03-daemon/startup.md` | §1.1 Step 3-6 | 启动诊断日志格式：`[daemon] Step N/6: description... OK (Xms)` |
| `03-daemon/http-api.md` | §1.3 设计约束 | 返回时间要求 |
| Phase 0 已有 | logging_config.py | Phase 0 的日志基础设施 |

**Troubleshooting 设计**:

| 故障场景 | 检测方式 | 用户感知 | 日志位置 | 恢复操作 |
|---------|---------|---------|---------|---------|
| 启动某步失败 | 日志中该步显示 `FAIL (Xms)` + 错误原因 | 如果致命（Step 1-4）→ daemon 不启动 + error hint。如果非致命（Step 5）→ daemon 启动但 recovery 功能降级 | `daemon.log` 搜索 `Step N/6.*FAIL` | 根据失败步骤查对应 feature 的 troubleshooting |
| Request-ID 未传回 | response header 缺失 `X-Request-ID` | 无直接感知——但排障时无法追踪请求 | — | middleware bug——检查 middleware 注册顺序 |
| daemon.log 文件过大 | 磁盘空间不足 | daemon 运行变慢或日志写入失败 | `df -h ~/.bible-cc/` | 手动清理 `> ~/.bible-cc/daemon.log`（Phase 5 实现 log rotation） |

**Function-Level Steps**（按实现顺序）:

```python
# server.py

# Request-ID Middleware
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """每个 HTTP 请求生成 UUID4 → request.state.request_id。
    执行前后记录 {method} {path} → {status} ({duration}ms)。
    Response header 添加 X-Request-ID。
    """

# 启动诊断日志
def log_startup_step(step: int, total: int, description: str):
    """输出启动步骤日志到 daemon.log：
    [daemon] Step {step}/{total}: {description}...
    """

def log_startup_step_result(step: int, success: bool, elapsed_ms: int, detail: str = ""):
    """输出步骤结果：
    OK ({elapsed_ms}ms) 或 FAIL ({elapsed_ms}ms) — {detail}
    """
```

**交付标准**:

- [ ] 每个 HTTP response 包含 `X-Request-ID` header
- [ ] 启动时 stderr/daemon.log 可见 6 步诊断日志
- [ ] 每步带 timing（OK/FAIL + ms）
- [ ] Request-ID 在内部 log 中可追溯

**测试用例**（混合——middleware HTTP 测试需要 daemon 进程，logger 测试纯函数）:

*功能测试*:
- [ ] `[Integration] [Post]` `test_request_id_in_response_header` — 任意端点 → response headers 含 `X-Request-ID`
- [ ] `[Integration] [Post]` `test_request_id_unique_per_request` — 两次请求 → 不同 request-id
- [ ] `[Unit] [Pre]` `test_request_id_logged_with_request` — mock logger → 验证 log message 含 request_id
- [ ] `[Integration] [Post]` `test_startup_log_six_steps` — daemon 启动 → daemon.log 含 6 行 `Step N/6`
- [ ] `[Integration] [Post]` `test_startup_log_includes_timing` — 每步后含 `OK (Xms)` 或 `FAIL (Xms)`

*意图测试*:
- [ ] `[Integration] [Post]` `test_request_id_propagated_to_error_responses` — **意图: 排障完整性**。发非法 body → 400 response → header 仍含 X-Request-ID。
- [ ] `[Integration] [Post]` `test_startup_log_order_matches_startup_sequence` — **意图: 顺序不可变**。capture 日志输出 → 检查 step 序号严格递增。

---

