# bible-cc-plugin

`bible-cc-plugin` is a Claude Code plugin that integrates BiBLE Atlas as a memory + knowledge broker — providing context recall, session capture, and agent-accessible tools for Claude Code.

## Architecture

Four components, all Python:

```
bible-cc-plugin/
├── pyproject.toml            # deps, entry points, build config
├── .claude-plugin/
│   └── plugin.json           # .claude-plugin manifest
├── hooks/hooks.json          # Hook → daemon HTTP mappings
├── commands/                 # User-facing slash commands
│   ├── status.md             #   /bible-cc:status
│   ├── push.md               #   /bible-cc:push
│   ├── consult.md            #   /bible-cc:consult
│   ├── review.md             #   /bible-cc:review
│   └── help.md               #   /bible-cc:help
├── src/bible_cc_plugin/
│   ├── daemon/
│   │   ├── server.py         #   FastAPI HTTP server on :9777
│   │   ├── buffer.py         #   SQLite session/turn/moment store (sqlite3)
│   │   ├── detector.py       #   LLM moment detection (anthropic SDK)
│   │   ├── injector.py       #   Context recall (local buffer: turns + moments)
│   │   └── client.py         #   BiBLE HTTP client (against BiBLE Atlas REST API)
│   ├── mcp/server.py         #   MCP stdio server (mcp Python SDK)
│   ├── config.py             #   Config loading (JSON + env var overrides)
│   └── types.py              #   Shared types (Pydantic models)
├── scripts/
│   ├── daemon.py             #   Daemon lifecycle CLI (start/stop/status)
│   ├── hook.py               #   Hook bridge (calls daemon HTTP endpoints)
│   ├── setup.py              #   Setup wizard (generates .mcp.json)
│   ├── uninstall.sh          #   Complete uninstall
│   └── dev.sh                #   Dev helper (init/test/lint/ci)
└── tests/
    ├── test_daemon.py
    ├── test_buffer.py
    ├── test_detector.py
    ├── test_mcp.py
    └── test_client.py
```

### Component Roles

| Component | Role | Transport | Lifetime |
|---|---|---|---|
| **Daemon** | Buffer turns, detect key moments via LLM, flush to BiBLE, serve local context injection | HTTP `localhost:9777` | Persistent |
| **MCP Server** | 6 BiBLE tools (memory search/save/get, knowledge search, skill search/get) + 2 postponed (delete, list) | Stdio (MCP) | Per Claude Code session |
| **Hooks** | Glue — start daemon, inject context, feed turns | Shell → HTTP to daemon | Event-driven |
| **Commands** | User-facing manual control (push, consult, status, review) | Shell → HTTP to daemon | On-demand |
