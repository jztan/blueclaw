# Telegram bridge

Talk to blueclaw from your phone over Telegram. Allowlist-enforced. Per-chat
workspaces under `~/blueclaw/chats/<chat_id>/`, each with its own `CONTEXT.md`
and `history.jsonl`.

## 1. Create a bot

1. Open Telegram, message `@BotFather`.
2. Send `/newbot`, follow prompts. Note the **bot token** it returns.

## 2. Install the optional extra

```bash
pip install -e ".[telegram]"
```

## 3. Configure

Add to `blueclaw.yaml`:

```yaml
bridges:
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
    allowed_chat_ids: []          # empty => refuse everyone (safe default)
    allowed_user_ids: []          # optional finer-grain
    mode: polling                  # or "webhook"
```

Export your token:

```bash
export TELEGRAM_BOT_TOKEN="123456:abc..."
```

## 4. Find your chat ID

```bash
blueclaw telegram
```

Send `/whoami` to your bot in Telegram. It replies with
`chat_id=N user_id=M`. Add that `chat_id` to `allowed_chat_ids` in
`blueclaw.yaml`, stop the bridge (Ctrl-C), restart.

## 5. Send a message

DM your bot any text — blueclaw replies inline. Commands:

- `/whoami` — returns your IDs (works even for unauthorized users so a new
  user can onboard).
- `/start` — greeting + auth status.
- `/reset` — wipes the chat's `history.jsonl`; keeps `CONTEXT.md`.
- `/forget` — wipes both `history.jsonl` and `CONTEXT.md`.

## Single-instance rule

Long-polling allows exactly one `blueclaw telegram` process per bot token. If
you see `Conflict: terminated by other getUpdates request`, another instance is
running — stop it and retry. The bridge logs a friendly message and exits with
code 2 in this case.

## Webhook mode (optional)

If running on AWS / behind a public HTTPS endpoint:

```yaml
bridges:
  telegram:
    mode: webhook
    webhook_url: https://your.host/telegram
    webhook_port: 8421
```

Or override at the CLI:

```bash
blueclaw telegram --webhook https://your.host/telegram
```

Run behind your reverse proxy of choice (Caddy, Cloudflare Tunnel, ALB).

## Smoke testing without a model

```bash
blueclaw telegram --echo --allow $YOUR_CHAT_ID
```

Replies with `echo: <your message>` instead of calling the LLM. Useful for
verifying network plumbing and the allowlist without burning model tokens.
