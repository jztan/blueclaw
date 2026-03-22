# HTTP API

`blueclaw serve` exposes the agent as a local HTTP server for programmatic access or tool integration.

## Start the server

```bash
blueclaw serve                                        # http://127.0.0.1:8420
blueclaw serve --host 0.0.0.0 --port 9000            # custom bind
blueclaw serve --model ollama/llama3                 # different model
blueclaw serve --cors-origin https://app.example.com # extra CORS origin
```

## Endpoints

### `POST /message`

Run the agent and get a reply.

**Request**

```json
{
  "message": "what files are in the workspace?",
  "conversation_id": "sess-001"
}
```

| Field | Type | Required |
|---|---|---|
| `message` | string | yes |
| `conversation_id` | string (1–64 alphanumeric/dash/underscore) | no |

**Response**

```json
{
  "reply": "The workspace contains CONTEXT.md and no other files yet.",
  "run_id": "20260322-142301-a3f1",
  "conversation_id": "sess-001",
  "tokens": 150,
  "cost": 0.0023
}
```

### `GET /health`

```json
{"status": "ok", "version": "1.5.0"}
```

## Authentication

Set `BLUECLAW_API_KEY` in `.env` to require a Bearer token on all `/message` requests:

```
BLUECLAW_API_KEY=my-secret
```

```bash
curl -X POST http://127.0.0.1:8420/message \
  -H "Authorization: Bearer my-secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'
```

Requests without the correct token return `401`. Uses `hmac.compare_digest` to prevent timing attacks. `/health` is always unauthenticated.

## Error responses

| Status | Cause |
|---|---|
| `400` | Missing `message` field, invalid JSON, or invalid `conversation_id` |
| `401` | `BLUECLAW_API_KEY` set but token missing or wrong |
| `413` | Request body exceeds 1 MB |
| `504` | Agent did not complete within 300 s |
| `500` | Workspace error or unexpected exception |

## CORS

Requests from `http://localhost:<any port>` and `http://127.0.0.1:<any port>` are allowed by default. Pass `--cors-origin` to add one additional origin.

## Traces

Every API request writes a trace tagged `source: "api"` to `.blueclaw/traces/`. Traces are visible in `blueclaw trace ui` alongside terminal runs.
