# /bible-cc:context

View the current local context that would be injected on SessionStart.

```bash
# session_id: CC provides $CLAUDE_SESSION_ID in command environment
# Fallback: daemon infers from most recent active session
curl -s -X POST http://127.0.0.1:9777/context/inject \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"${CLAUDE_SESSION_ID:-}\"}" | python3 -m json.tool
```
