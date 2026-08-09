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
aishe doctor             # run diagnostics
aishe version            # show version
```

In the interactive loop, use `/exit` or Ctrl+C to quit.

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
