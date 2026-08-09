# Aishe CLI

Voice-first AI assistant for your terminal — streaming chat, live voice, and persistent memory.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/astromanish/aishe-cli/main/setup.sh | bash
```

## Usage

```bash
aishe                    # interactive streaming chat
aishe "What is 15 * 4?"  # one-shot streaming chat
aishe live               # live voice conversation
aishe status             # service health
aishe memory add "fact"  # save a fact
aishe memory search "x"  # search memory
aishe memory list        # list memory
aishe memory clear       # clear memory
aishe config             # view config
aishe config get <key>   # get a config value
aishe config set <k> <v> # set a config value
aishe setup              # choose your Ollama model + optional Telegram setup
aishe telegram status    # Telegram bridge status
aishe telegram auth <token>  # save bot token
aishe gateway start      # start the Telegram connection bridge
aishe gateway restart    # restart the Telegram bridge
aishe gateway stop       # stop the Telegram bridge
aishe doctor             # run diagnostics
aishe version            # show version
```

In the interactive loop, use `/exit` or Ctrl+C to quit.

`aishe setup` lists your pulled Ollama models, lets you pick one (or type a
new model name to pull), saves it, restarts DeepAgent to apply it, and
optionally walks you through connecting Telegram (bot token + your user id).

### Telegram bridge

Chat with Aishe from Telegram:

1. Get a bot token from **@BotFather** (`/newbot`).
2. Run `aishe setup` and choose "yes" for the Telegram bridge, or:
   ```bash
   aishe telegram auth <bot_token>
   ```
3. Add your Telegram user id (get it from @userinfobot) via the setup prompt.
4. Start the bridge:
   ```bash
   aishe gateway start
   aishe gateway status   # verify it's running
   ```
5. Message your bot on Telegram — Aishe replies via DeepAgent. Each chat keeps
   its own conversation in `~/aishe/threads/`.

Manage it anytime with `aishe gateway start|restart|stop|status`.

## Requirements

- Python 3.9+, `requests`, `pyyaml`, `ffmpeg`
- Services: Ollama, DeepAgent, STT, TTS
- Voice sidecars are opt-in: `AISHE_INSTALL_VOICE=1 bash setup.sh`

## Data

All data lives in `~/aishe` on every platform:

- `~/aishe/memory/facts.jsonl` — memory
- `~/aishe/threads/*.json` — conversations

Config: `~/.config/aishe/config.yaml`

## License

MIT
