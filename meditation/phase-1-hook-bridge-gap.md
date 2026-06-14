# Phase 1a+1b Hook Bridge Gap

**Date**: 2026-06-14

## Finding

Phase 1a+1b 交付了 daemon 的全部核心端点（health、session、turn），但**在 Claude Code 真实环境中一个都无法使用**。

## Root Cause

1. **hook.py** — turn-user、turn-tool、session-end 三个 action 均为 pass-through 空操作
2. **hooks.json** — 四个 hook 命令均**未传入 CC 环境变量**（`$CLAUDE_SESSION_ID`、`$USER_PROMPT`、`$TOOL_NAME`、`$TOOL_OUTPUT`）

## Resolution Plan

Phase 2 feature **F2.1 — Hook Bridge** 负责连接：

| Action | 需实现 |
|--------|-------|
| session-start | `_ensure_daemon()` → `POST /session/start` → `POST /context/inject` → stdout |
| turn-user | `POST /turn/user`（daemon 不可达静默跳过） |
| turn-tool | `POST /turn/tool`（daemon 不可达静默跳过） |
| session-end | `POST /session/end`（daemon 不可达静默跳过） |

并同步更新 `hooks.json` 传入 CC 环境变量。

## Current State

| 组件 | 状态 |
|------|------|
| Phase 0 daemon lifecycle | ✅ 可用 |
| Phase 1a SQLite + buffer | ✅ 端点可用，curl 可测 |
| Phase 1b Session/Turn 端点 | ✅ 端点可用，curl 可测 |
| Hook → Daemon 连线 | ❌ Phase 2 F2.1 |
| Context injection | ❌ Phase 1c |
| Slash commands | ❌ 未实现 |
