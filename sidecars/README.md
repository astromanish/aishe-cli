# Aishe sidecar services

Long-running processes that sit beside the CLI. The CLI and the desktop app
talk to these over HTTP; none of them are imported as libraries.

## Components

| File | Port | Purpose |
|------|------|---------|
| `telegram_bot.py` | — (long-polls Telegram) | Telegram bridge: receives messages, forwards to DeepAgent, replies |

## Telegram bridge

The bridge is how Aishe is reachable from Telegram. It shares the **same
conversation thread** as the CLI and desktop app (`main`), so a message sent
from your phone continues the exact conversation you were having in the
terminal.

Messages are sent with `parse_mode` unset. Aishe's replies contain Markdown
that Telegram's parser rejects (`**bold**` is not valid Telegram Markdown), and
setting the mode made the API reject the send outright — a silent failure that
looked like the bot had hung.

Tool calls are rendered as readable one-liners by `describe_tool(name, args)`,
so you see *what* Aishe is doing ("searching memory for 'aishe'") rather than a
bare tool name.

```bash
aishe telegram auth <bot-token>   # save the token
aishe telegram start              # start the bridge
aishe telegram status             # is it running?
aishe gateway start               # same thing, via the gateway
```

The bridge is located through `aishe_pkg/aishe_core.py`, the shared path
resolver, so it is found the same way from every surface.

## Voice

Voice is **not** a sidecar. STT and TTS go straight to Sarvam's cloud API
(Saaras v3 for transcription, Bulbul v3 for speech) from `aishe_pkg/voice.py`.
There are no local ports to start, no models to download, and nothing here to
run.
