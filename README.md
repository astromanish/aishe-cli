# Aishe

A personal AI assistant that works from the terminal, the desktop, and Telegram —
one agent, one memory, one conversation.

Not a chatbot bolted onto three frontends: all three surfaces talk to the same
DeepAgent backend over the same conversation thread, so a message sent from your
phone continues the exact conversation you were having in the terminal.

## Surfaces

| Surface | How you reach it |
|---------|------------------|
| Terminal | `aishe` |
| Desktop | `aishe desktop` — Electron app: chat, voice, history, memory, keys |
| Telegram | `aishe telegram start` — long-polling bridge |

This repository is the CLI. The desktop app is
[aishe-desktop](https://github.com/astromanish/aishe-desktop).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/astromanish/aishe-cli/main/setup.sh | bash
```

## Usage

```bash
aishe                        # interactive streaming chat
aishe "What is 15 * 4?"      # one-shot streaming chat
aishe desktop                # open the desktop app
aishe status                 # service health
aishe doctor                 # full diagnostics
aishe start-all              # bring up every service

aishe memory add "fact"      # save a fact
aishe memory search "query"  # search memory
aishe memory status          # embedder / plugin / vector DB

aishe config                 # view config
aishe config set <key> <v>   # set a value

aishe telegram start|status|stop
aishe gateway start|restart|stop|status
aishe version
```

In the interactive loop, `/exit` or Ctrl+C quits.

## One shared foundation

Everything resolves through [`aishe_pkg/aishe_core.py`](aishe_pkg/aishe_core.py)
— where the backend lives, where the desktop app lives, where the sidecars live,
and which services exist on which ports.

That module exists because the alternative already happened once: the CLI,
`start-all.sh` and the desktop each hardcoded their own paths, and they drifted.
Two divergent copies of DeepAgent ended up competing for `:8765`, with the winner
decided by whichever tool launched first.

```python
from aishe_pkg import aishe_core

aishe_core.deepagent_dir()     # where server.py is
aishe_core.desktop_dir()       # the Electron app
aishe_core.telegram_bot()      # the Telegram sidecar
aishe_core.SERVICES            # deepagent :8765, ollama :11434, qdrant :6333
aishe_core.describe()          # all of the above, with existence flags
```

Resolution order: `AISHE_HOME` override → the repo checkout (so an editable
clone is authoritative) → `~/.local/share/aishe-cli`. Set `AISHE_HOME` to
relocate the whole tree at once.

## Services

| Service | Port | Health | Purpose |
|---------|------|--------|---------|
| DeepAgent | 8765 | `/health` | the agent: tools, memory, model routing |
| Ollama | 11434 | `/api/tags` | local models, embeddings |
| Qdrant | 6333 | `/readyz` | vector DB for semantic memory |

Voice is **not** a service. STT and TTS go straight to Sarvam's cloud API
(Saaras v3 and Bulbul v3) — no local ports, no models to download.

`aishe doctor` and the desktop's service panel read the same registry, so they
can't disagree about what's healthy.

## Memory

Two layers:

- **Vector** — mem0 + Qdrant (`aishe_memories`), embeddings by
  `qwen3-embedding:0.6b` on Ollama. This is semantic search.
- **Short-term** — SQLite with FTS5 at `~/aishe/aishe.db`, which is what
  `aishe search` queries.

Check both with `aishe memory status`.

## Requirements

- Python 3.9+, `requests`, `pyyaml`, `ffmpeg`
- Ollama (for embeddings), and Qdrant for vector memory
- A Sarvam API key for voice
- A provider key for whichever chat model you use

## Data

All data lives in `~/aishe`:

- `~/aishe/aishe.db` — conversations, FTS5-indexed
- `~/aishe/memory/` — short-term memory files

Config: `~/.config/aishe/config.yaml`

## Development

```bash
python tests/test_aishe_core.py    # path resolver + service registry
```

The desktop app has its own suite: `cd aishe-desktop && npm test`.

## License

MIT
