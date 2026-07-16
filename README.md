# Aishe CLI

**Voice-first AI assistant for your terminal.**

Aishe CLI is a standalone command-line AI assistant with voice input/output, persistent memory, chat threads, tool calling, and live voice conversation — all from your terminal.

```bash
aishe status        # check all services
aishe chat "hello"  # one-shot chat
aishe live          # live voice conversation (press Enter to record)
```

## Features

- **Chat** — one-shot and streaming chat with tool calling (calculator, timezone, memory, and more)
- **Live Voice** — press Enter, speak, get a spoken response back. Full duplex: record → STT → LLM → TTS → play
- **Memory** — persistent personal memory. The AI remembers facts about you across sessions
- **Threads** — multiple conversation threads with history
- **Voice I/O** — transcribe audio files, synthesize text to speech
- **Ollama Integration** — list, pull, and manage models
- **Intent Lab** — log and analyze user intent classification stats
- **macOS** — native mic recording via AVFoundation, audio playback via `afplay`

## Requirements

- **Python 3.12+**
- **`requests`** — `pip install requests`
- **`ffmpeg`** — for mic recording (`brew install ffmpeg`)
- **Running services:**
  - [Ollama](https://ollama.com) on `:11434` (local or cloud models)
  - [DeepAgent](https://github.com/langchain-ai/deepagents) sidecar on `:8765`
  - [Parakeet STT](https://github.com/nvidia/parakeet) on `:5093`
  - [Supertonic TTS](https://github.com/opencode-ai/supertonic-tts) on `:8766`

## Install

```bash
# 1. Install Python dependency
pip install requests

# 2. Symlink to your PATH
ln -sf $(pwd)/aishe ~/.local/bin/aishe

# 3. Verify
aishe status
```

## Usage

### Status
```bash
aishe status
```
Shows all service health, model counts, memory entries, and thread counts.

### Chat
```bash
aishe chat "What is 15 * 4?"
aishe chat "What time is it in Tokyo?" -v   # verbose: shows tool calls
aishe stream "Tell me a short joke"          # streaming: tokens print live
```

### Live Voice Conversation
```bash
aishe live                    # press Enter to record, speak, get spoken response
aishe live --list              # list available microphone devices
aishe live -d 1                # use specific mic device
aishe live --duration 8        # record 8 seconds per turn
aishe live --no-tts            # voice input only, text responses
aishe live -V M1               # use male voice for TTS
```

### Memory
```bash
aishe memory add "User prefers Hindi voice interface"
aishe memory search "Hindi"
aishe memory list
aishe memory clear
```

### Threads
```bash
aishe threads                  # list all threads
aishe threads --new            # create a new thread
aishe threads --show <id>      # show thread messages
aishe threads --delete <id>    # delete a thread
```

### Voice I/O
```bash
aishe voice status
aishe voice transcribe recording.wav
aishe voice speak "Namaste, main Aishe hoon"
aishe voice speak "Hello" -o output.wav --no-play
```

### Ollama
```bash
aishe ollama models
aishe ollama pull deepseek-v4-flash:cloud
aishe ollama whoami
aishe ollama signin
```

### Intent Lab
```bash
aishe intent stats --days 7
aishe intent export
```

## Architecture

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  aishe   │────▶│ DeepAgent│────▶│  Ollama  │
│   CLI    │     │  :8765   │     │ :11434   │
└──────────┘     └──────────┘     └──────────┘
       │               │
       ▼               ▼
┌──────────┐     ┌──────────┐
│ Parakeet │     │Supertonic│
│ STT:5093 │     │TTS: 8766 │
└──────────┘     └──────────┘
```

The CLI talks to four local services:
- **DeepAgent** — LangGraph agent with tool calling, streaming, and conversation memory
- **Ollama** — LLM inference (local or cloud models)
- **Parakeet STT** — Speech-to-text (NVIDIA Parakeet ONNX model)
- **Supertonic TTS** — Text-to-speech (ONNX-based, 10 voices)

Memory and threads are stored as JSONL/JSON files at `~/Library/Application Support/aishe/`.

## Data

All data is local:
- **Memory**: `~/Library/Application Support/aishe/memory/facts.jsonl`
- **Threads**: `~/Library/Application Support/aishe/threads/*.json`
- **Intent Logs**: `~/Library/Application Support/aishe/intent_lab/intent_*.jsonl`

## Extending

The CLI is a single Python file — easy to fork, modify, and build upon. The architecture is service-oriented: swap in any OpenAI-compatible LLM, any STT/TTS backend, or add new tools to the DeepAgent sidecar.

## License

MIT
