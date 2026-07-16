# Aishe CLI

Command-line interface for [Aishe](https://github.com/astromanish/aishe) — voice-first AI for Bharat.

Hits the same HTTP endpoints the desktop Tauri app uses: DeepAgent, Ollama, Parakeet STT, Supertonic TTS, and file-based memory/threads.

## Install

```bash
pip install requests
ln -sf $(pwd)/aishe ~/.local/bin/aishe
```

## Usage

```
aishe status                    — check all services
aishe chat "hello"              — one-shot chat (DeepAgent)
aishe stream "hello"            — streaming chat (tokens printed live)
aishe live                      — live voice conversation (press Enter to record)
aishe live --list               — list mic devices
aishe threads                   — list all chat threads
aishe threads --new             — create a new thread
aishe threads --show <id>       — show thread messages
aishe threads --delete <id>     — delete a thread
aishe memory add "fact"         — add a memory
aishe memory search "query"     — search memories
aishe memory list               — list all memories
aishe memory clear              — clear all memories
aishe voice transcribe <wav>    — transcribe audio file
aishe voice speak "text"        — synthesize speech + play
aishe voice speak "text" -o out.wav  — save without playing
aishe voice status              — check STT/TTS services
aishe ollama models             — list pulled models
aishe ollama pull <model>       — pull a model
aishe ollama whoami             — check ollama login
aishe intent stats --days 7     — intent lab stats
aishe intent export             — export intent log as CSV
```

## Requirements

- Python 3.12+
- `requests` (`pip install requests`)
- `ffmpeg` (for `aishe live` mic recording)
- Running services: DeepAgent (:8765), Parakeet STT (:5093), Supertonic TTS (:8766), Ollama (:11434)

## Data

Memory and threads are stored at `~/Library/Application Support/aishe/` — shared with the Aishe Tauri desktop app.
