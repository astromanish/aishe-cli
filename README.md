# Aishe CLI

**Voice-first AI assistant for your terminal.**

Aishe CLI is a standalone command-line AI assistant with voice input/output, persistent memory, chat threads, tool calling, and live voice conversation — all from your terminal.

```bash
curl -fsSL https://raw.githubusercontent.com/astromanish/aishe-cli/main/setup.sh | bash
aishe status
aishe chat "Hello!"
aishe live
```

## Features

- **Chat** — one-shot and streaming chat with tool calling (calculator, timezone, memory, and more)
- **Live Voice** — press Enter, speak, get a spoken response back. Full duplex: record → STT → LLM → TTS → play
- **REPL** — continuous text chat mode with thread management
- **Memory** — persistent personal memory. The AI remembers facts about you across sessions
- **Threads** — multiple conversation threads with history
- **Voice I/O** — transcribe audio files, synthesize text to speech
- **Ollama Integration** — list, pull, and manage models
- **Intent Lab** — log and analyze user intent classification stats
- **Diagnostics** — comprehensive system check with roundtrip tests
- **Export** — export memory and threads to Markdown/CSV
- **Search** — full-text search across all threads and memory
- **Shell Completions** — bash, zsh, and fish

## Quick Install

```bash
# One-command setup (installs deps, creates config, links to PATH)
curl -fsSL https://raw.githubusercontent.com/astromanish/aishe-cli/main/setup.sh | bash

# Or manually:
pip install requests pyyaml
ln -sf $(pwd)/aishe ~/.local/bin/aishe
```

## Requirements

- **Python 3.9+**
- **`requests`** + **`pyyaml`** (auto-installed by setup script)
- **`ffmpeg`** — for mic recording (`brew install ffmpeg` or `apt-get install ffmpeg`)
- **Running services:**
  - [Ollama](https://ollama.com) on `:11434` (local or cloud models)
  - [DeepAgent](https://github.com/langchain-ai/deepagents) sidecar on `:8765`
  - [Parakeet STT](https://github.com/nvidia/parakeet) on `:5093`
  - [Supertonic TTS](https://github.com/opencode-ai/supertonic-tts) on `:8766`

## Usage

### Status
```bash
aishe status
```
Shows all service health with beautiful boxed UI, model counts, memory entries, and thread counts.

### Chat
```bash
aishe chat "What is 15 * 4?"
aishe chat "What time is it in Tokyo?" -v   # verbose: shows tool calls
aishe stream "Tell me a short joke"          # streaming: tokens print live
aishe repl                                   # continuous text chat mode
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

### Diagnostics
```bash
aishe doctor
```
Runs comprehensive checks: Python environment, dependencies, external tools, all services, STT/TTS/DeepAgent roundtrip tests.

### Search & Export
```bash
aishe search "pizza"           # search across memory + threads
aishe export                   # export all data to ~/Downloads
```

### Configuration
```bash
aishe config                   # view full config
aishe config get voice.default_voice
aishe config set voice.default_voice M1
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

## Data

All data is local:
- **Memory**: `~/Library/Application Support/aishe/memory/facts.jsonl`
- **Threads**: `~/Library/Application Support/aishe/threads/*.json`
- **Intent Logs**: `~/Library/Application Support/aishe/intent_lab/intent_*.jsonl`
- **Config**: `~/.config/aishe/config.yaml`

## Extending

The CLI is a single Python package — easy to fork, modify, and build upon. The architecture is service-oriented: swap in any OpenAI-compatible LLM, any STT/TTS backend, or add new tools to the DeepAgent sidecar.

## License

MIT
