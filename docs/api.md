# HTTP API

`blueclaw serve` exposes the agent as a local HTTP server for programmatic access or tool integration.

## Start the server

```bash
blueclaw serve                                        # http://127.0.0.1:8420
blueclaw serve --host 0.0.0.0 --port 9000            # custom bind
blueclaw serve --model ollama/llama3                 # different model
blueclaw serve --cors-origin https://app.example.com # extra CORS origin
blueclaw serve --max-concurrent 8                    # raise concurrency cap
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

### `POST /message/stream`

Same request schema as `/message`. Returns `Content-Type: text/event-stream` and emits Server-Sent Events as the agent generates output.

```bash
curl -N -X POST http://127.0.0.1:8420/message/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "summarize today’s python.org news"}'
```

**Event sequence**

```
event: delta
data: {"text": "Today the Python "}

event: delta
data: {"text": "Steering Council "}

event: done
data: {"reply": "Today the Python Steering Council ...",
       "run_id": "20260506-125758-ae79",
       "conversation_id": null,
       "tokens": 7230,
       "cost": null}
```

| Event | Payload | Meaning |
|---|---|---|
| `delta` | `{"text": "..."}` | One model output chunk. Concatenate to reconstruct the full reply. |
| `done` | Same shape as `/message` response (`reply`, `run_id`, `conversation_id`, `tokens`, `cost`) | Final event. Emitted once. |
| `error` | `{"error": "..."}` | Failure after the stream opened. The connection then closes. |

Auth, body cap (1 MB), and timeout (300 s) work the same as `/message`. Failures *before* the stream opens (`401`, `413`, `400`) return JSON with the appropriate HTTP status; failures *after* the stream opens are signaled via `event: error` because the status line is already on the wire.

The `done` payload's `conversation_id` echoes the request value (or `null`).

### `GET /health`

```json
{"status": "ok", "version": "2.0.0"}
```

## Concurrency

A single `asyncio.Semaphore` shared across `/message` and `/message/stream` caps simultaneous in-flight agent runs. Defaults:

- `max_concurrent_runs: 4` in `SessionConfig`
- override in `blueclaw.yaml`:
  ```yaml
  server:
    max_concurrent_runs: 8
  ```
- override at the CLI: `blueclaw serve --max-concurrent 8`

Requests above the cap queue (no 503) and are bounded by the existing 300 s per-request timeout.

## Authentication

Set `BLUECLAW_API_KEY` in `.env` to require a Bearer token on `/message` and `/message/stream`:

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

When `conversation_id` is rejected (path-traversal characters, whitespace/control characters, `.`/`..`, empty, or longer than 128 chars), the response body is the generic `{"error": "invalid conversation_id"}` and **does not contain the rejected value** — server logs carry the underlying validation error, the client does not.

## Per-turn capture

Every API turn writes `response.txt` and `messages.json` to `<workspace>/.blueclaw/turns/<conversation_id>/turn-NNN/` (numbering is filesystem-derived per `conversation_id`). Requests without a `conversation_id` are not captured. Capture is best-effort: write failures log to stderr but never fail the request.

The trace JSON stored alongside (`.blueclaw/traces/<run_id>.json`) carries a `capture_path` field pointing at the per-turn directory, so dashboards and external tools can link a trace to its raw artifacts.

The captured artifacts are surfaced inline in `blueclaw trace ui` (preview chip + "view full" links per row) via dashboard-only routes `GET /api/turns/<cid>/<n>/{response,messages}`. **These routes are not part of the `blueclaw serve` HTTP API** — only the `blueclaw trace ui` dashboard exposes them. If you need to fetch captures programmatically over the gateway API, read the files directly from the workspace path above, or file a request for serve-side parity (tracked as backlog item #9).

## CORS

Requests from `http://localhost:<any port>` and `http://127.0.0.1:<any port>` are allowed by default. Pass `--cors-origin` to add one additional origin.

## Traces

Every API request writes a trace tagged `source: "api"` to `.blueclaw/traces/`. Traces are visible in `blueclaw trace ui` alongside terminal runs.
