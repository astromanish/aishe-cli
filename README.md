# Aishe CLI

**Voice-first AI assistant for your terminal.** Minimal interface, powerful internals.

```bash
aishe status        # check all services
aishe chat "hello"  # one-shot chat
aishe live          # live voice conversation
aishe repl          # continuous text chat
```

## Features

- **Chat** — one-shot (`aishe chat`) and streaming (`aishe stream`) with tool calling
- **REPL** — `aishe repl` for continuous text conversation (no voice deps needed)
- **Live Voice** — `aishe live` for full voice conversation: record → STT → LLM → TTS → play
- **VAD Recording** — voice activity detection (via webrtcvad) for natural turn-taking
- **Memory** — persistent personal memory across sessions
- **Threads** — multiple conversation threads with history
- **Voice I/O** — transcribe audio files, synthesize text to speech
- **Ollama Integration** — list, pull, and manage models
- **Intent Lab** — log and analyze user intent classification stats
- **Search** — full-text search across all threads and memory
- **Export** — export threads (Markdown) and memory (Markdown/CSV)
- **Doctor** — comprehensive diagnostics (services, deps, roundtrip tests)
- **Config** — YAML config file with `aishe config get/set`
- **Shell Completions** — `aishe completions bash|zsh|fish`
- **macOS** — native mic recording via AVFoundation, audio playback via `afplay`

## Requirements

- **Python 3.9+**
- **`requests`** — `pip install requests`
- **`pyyaml`** — `pip install pyyaml` (for config)
- **`webrtcvad`** — `pip install webrtcvad` (for VAD recording, optional)
- **`ffmpeg`** — `brew install ffmpeg` (for mic recording)
- **Running services:**
  - [Ollama](https://ollama.com) on `:11434`
  - [DeepAgent](https://github.com/langchain-ai/deepagents) sidecar on `:8765`
  - [Parakeet STT](https://github.com/nvidia/parakeet) on `:5093`
  - [Supertonic TTS](https://github.com/opencode-ai/supertonic-tts) on `:8766`

## Install

```bash
# 1. Install Python dependencies
pip install requests pyyaml webrtcvad

# 2. Symlink to your PATH
ln -sf $(pwd)/aishe ~/.local/bin/aishe

# 3. Verify
aishe status
```

## Usage

### Status & Diagnostics
```bash
aishe status        # quick service health
aishe doctor        # comprehensive diagnostics + roundtrip tests
```

### Chat
```bash
aishe chat "What is 15 * 4?"          # one-shot
aishe chat "What time is it in Tokyo?" -v  # verbose: show tool calls
aishe stream "Tell me a short joke"    # streaming: tokens print live
aishe repl                             # continuous text REPL
```

### Live Voice Conversation
```bash
aishe live                    # press Enter to record, speak, get spoken response
aishe live --list             # list available microphone devices
aishe live -d 1               # use specific mic device
aishe live --no-vad           # disable VAD, use fixed duration
aishe live -V M1              # use male voice for TTS
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

### Search & Export
```bash
aishe search "project"         # search threads + memory
aishe export                   # export all data to ~/Downloads/
aishe export -d ~/Documents    # export to custom directory
```

### Config
```bash
aishe config                   # view full config
aishe config get voice.default_voice
aishe config set voice.default_voice M1
aishe config set voice.recording_duration 8
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

### Shell Completions
```bash
aishe completions bash > ~/.bash_completion.d/aishe
aishe completions zsh > /usr/local/share/zsh/site-functions/_aishe
aishe completions fish > ~/.config/fish/completions/aishe.fish
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
- **Config**: `~/.config/aishe/config.yaml`

## Extending

The CLI is organized as a Python package (`aishe_pkg/`) — easy to fork, modify, and build upon. The architecture is service-oriented: swap in any OpenAI-compatible LLM, any STT/TTS backend, or add new tools to the DeepAgent sidecar.

## License

MIT
