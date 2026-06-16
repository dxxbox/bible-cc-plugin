# /bible-cc:review

Browse and manage pending key moments detected by the bible-cc daemon.

## View pending moments

```bash
curl -s "http://127.0.0.1:9777/daemon/moments?session_id=$CLAUDE_SESSION_ID" | python3 -m json.tool
```

## Edit a moment

```bash
curl -s -X PUT "http://127.0.0.1:9777/daemon/moments/{id}" \
  -H "Content-Type: application/json" \
  -d '{"title": "New title", "narrative": "Updated narrative."}'
```

## Delete a moment

```bash
curl -s -X DELETE "http://127.0.0.1:9777/daemon/moments/{id}"
```

## Detection health

```bash
curl -s "http://127.0.0.1:9777/daemon/debug/detections/stats" | python3 -m json.tool
```
