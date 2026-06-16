# Plan: 消除硬编码 — 全面配置化改造

> 2026-06-16 | 影响 Phase 2b–2d | 优先级：P0

## 动机

全代码库审计发现 **~70 处硬编码值**（端口、路径、超时、限制、魔术字符串），其中仅 ~25 处有对应的 config 字段但未使用，~45 处需要新增配置字段。同时 SW design 文档与实现间存在 **20 个 gap**（默认值不一致、缺失 validator、env var 未文档化）。

**目标：** 首选从 `~/.bible-cc/config.json` 读取，最次硬编码（仅限 schema DDL、migration SQL hash、HTTP status code 等不可配置项）。

## 审计摘要

### 硬编码分布

| 目录 | 硬编码数 | 典型类别 |
|------|---------|---------|
| `src/bible_cc_plugin/config.py` | ~15 | 默认值（合法，但部分与设计文档不一致） |
| `src/bible_cc_plugin/daemon/server.py` | ~15 | db_path 重复、LIMIT、CORS、FastAPI meta、sleep |
| `src/bible_cc_plugin/daemon/buffer.py` | ~5 | busy_timeout、recovery LIMIT |
| `src/bible_cc_plugin/daemon/injector.py` | ~10 | max_turns、截断长度、token 估算、truncation 标记 |
| `src/bible_cc_plugin/daemon/daemon_launcher.py` | ~12 | timeout、poll_interval、host、uvicorn_app |
| `src/bible_cc_plugin/daemon/port_manager.py` | ~4 | timeout、max_attempts |
| `src/bible_cc_plugin/scripts/daemon.py` | ~12 | timeout、host、port sleep、log path |
| `src/bible_cc_plugin/scripts/hook.py` | ~6 | timeout、log path、截断长度 |
| `src/bible_cc_plugin/scripts/setup.py` | ~6 | port、timeout、url |
| `hooks/hooks.json` | ~6 | 各 hook 的 timeout |
| `tests/` | ~50+ | port 9777 出现 15+ 次、endpoint 路径、timeout |

### 设计文档 vs 实现 Gap（关键）

| Gap | 严重性 | 说明 |
|-----|--------|------|
| `detection.model` 默认值 | **Critical** | 设计 `claude-sonnet-4-5`，代码 `deepseek-v4-flash` |
| `inject_fallback` 默认值 | **Critical** | 设计 `"skip"`，代码 `"empty"` |
| `ANTHROPIC_SMALL_FAST_MODEL` 未实现 | **Critical** | 设计要求多级 model resolution，代码未实现 |
| `LoggingConfig` 无设计覆盖 | High | 5 字段全在代码中，设计文档零覆盖 |
| `SearchConfig` 无 validator | High | 设计要求 range check，代码缺失 |
| `DetectionConfig` 无 validator | High | 设计要求 max_tokens 1-4096、temperature 0.0-1.0 |
| 5 个 env var 未文档化 | Medium | 代码有但 schema.md 没有 |
| `capture.mode` 多了 `"all"` | Medium | 代码有但设计未定义 |

---

## 实施方案（4 Phase）

### Phase A: 使用已有 Config 字段（低风险）

消除"有 config 字段但代码不用"的情况。纯机械替换，无新字段。

#### A1. `server.py` db_path — 停止直接读 env var
**文件:** `src/bible_cc_plugin/daemon/server.py` 行 99, 168

```
Before: os.getenv("BIBLE_CC_DB_PATH", str(Path.home() / ".bible-cc" / "daemon.db"))
After:  _config.daemon.db_path
```

#### A2. `scripts/daemon.py` + `scripts/hook.py` log path
**文件:** `scripts/daemon.py` 行 26, `scripts/hook.py` 行 26

```
Before: _DAEMON_LOG = Path.home() / ".bible-cc" / "daemon.log"
After:  删除模块级常量，改用 Path(config.logging.file).expanduser()
```

#### A3. `scripts/setup.py` port
**文件:** `scripts/setup.py` 行 127, 137

```
Before: "http://127.0.0.1:9777/daemon/stop"
After:  f"http://127.0.0.1:{config.daemon.port}/daemon/stop"
```

#### A4. 补充缺失的 env var 覆盖
**文件:** `config.py` 行 162 后

```python
if v := os.getenv("ANTHROPIC_SMALL_FAST_MODEL"):
    config.detection.model = v
if v := os.getenv("ANTHROPIC_MODEL"):
    config.detection.model = v  # SMALL_FAST_MODEL 优先级更高
```

#### A5. 修复 `inject_fallback` 默认值
**文件:** `config.py` 行 63

```
Before: inject_fallback: str = "empty"
After:  inject_fallback: str = "skip"
```

### Phase C: 设计对齐 — 修复默认值和 Validator（中风险）

#### C1. 修复 `detection.model` 默认值
```
Before: model: str = "deepseek-v4-flash"
After:  model: str = "claude-sonnet-4-5"
```

#### C2. 添加 `SearchConfig` validator
```python
@field_validator("default_top_k")
def _validate_top_k(cls, v): return v if 1 <= v <= 100 else 8

@field_validator("default_min_score")
def _validate_min_score(cls, v): return v if 0.0 <= v <= 1.0 else 0.35
```

#### C3. 添加 `DetectionConfig` validator
```python
@field_validator("max_tokens")
def _validate_max_tokens(cls, v): return v if 1 <= v <= 4096 else 512

@field_validator("temperature")
def _validate_temperature(cls, v): return v if 0.0 <= v <= 1.0 else 0.0
```

#### C4. 修复 `base_url` validator — strip trailing slash 而非回退到默认
```python
# Before: if v.endswith("/"): return "http://localhost:5555"
# After:  return v.rstrip("/")
```

### Phase D: 脚本整合 — 消除重复模式（中风险）

#### D1. 提取 `local_client()` 到 `daemon_launcher.py`
- 从 `scripts/daemon.py` 和 `scripts/hook.py` 删除重复定义
- 统一导入 `from bible_cc_plugin.daemon.daemon_launcher import local_client`

#### D2. 提取 `tail_log()` 到 `daemon_launcher.py`
- 同上，消除 3 处重复

#### D3. 创建 config 单例存取器（可选）
```python
# config.py
def get_config() -> AppConfig:  # lazy singleton
```

此步骤可选，视 buffer.py/injector.py 需要访问 config 的程度决定。

---

## 不提取为配置的值（永久硬编码）

| 类别 | 示例 | 原因 |
|------|------|------|
| SQL schema DDL | 表名、列名、索引 | 数据模型契约，变更是 migration |
| Migration version + SQL hash | `buffer.py` MIGRATIONS | 不可变审计记录 |
| Content-hash 算法 | SHA-256 + null byte | 协议契约，变更会破坏已有 dedup |
| HTTP status code | 200, 404, 503 | 标准协议值 |
| WAL PRAGMA | `journal_mode=WAL` | 引擎级设置 |
| Module path | `bible_cc_plugin.daemon.server:app` | 代码结构，不是配置 |
| Migration SQL SHA 锁 | `test_buffer.py` 行 612 | 防止已发布 migration 被静默修改 |
| Logger namespace | `bible_cc` | 固定命名空间 |

---

## 与后续 Phase Plan 的集成

Phase 2b 开始前应先完成 Phase A（已有字段使用），避免新增功能引入更多硬编码。
Phase B/C/D 可在 Phase 2b-2d 开发过程中逐步推进。

| 硬编码改造 Phase | 对应开发 Phase | 说明 |
|-----------------|---------------|------|
| Phase A | **Phase 2b 开始前** | 消除低风险问题，建基础 |
| Phase B 部分 | Phase 2b (capture pipeline) | detection/injection 相关新字段 |
| Phase B 其余 | Phase 2c (recall pipeline) | BiBLE client 相关新字段 |
| Phase C | Phase 2b (同步进行) | 默认值/validator 修正 |
| Phase D | Phase 2c (同步进行) | 脚本整合 |

---

## 验证计划

1. **单元测试** — `test_config.py` 验证所有新字段的 default、validation、env override
2. **契约测试** — `test_daemon_api.py` 确保 daemon 行为不变
3. **端到端** — 完整 session start → turn → end 流程，验证所有端点仍正常工作
4. **向后兼容** — 用一个不含新字段的旧 `config.json` 测试，确保 Pydantic default 生效
5. **env var 优先** — 对每个新增 env var 写 test case
