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
# One-command setup (auto-clones to ~/.local/share/aishe-cli)
curl -fsSL https://raw.githubusercontent.com/astromanish/aishe-cli/main/setup.sh | bash

# Or clone first, then run setup:
git clone https://github.com/astromanish/aishe-cli.git ~/aishe-cli
cd ~/aishe-cli
bash setup.sh
```

## Requirements

- **Python 3.9+**
- **`requests`** + **`pyyaml`** (auto-installed by setup script)
- **`ffmpeg`** — for mic recording
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt-get install ffmpeg`
  - Windows: download from https://ffmpeg.org/download.html
- **Running services:**
  - [Ollama](https://ollama.com) on `:11434` (local or cloud models) — auto-installed
  - [DeepAgent](https://github.com/langchain-ai/deepagents) sidecar on `:8765` — auto-installed
  - **STT** ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)) on `:5093` — opt-in via `AISHE_INSTALL_VOICE=1`
  - **TTS** ([Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx)) on `:8766` — opt-in via `AISHE_INSTALL_VOICE=1`

## Voice setup

Voice sidecars (STT + TTS) are **opt-in** to keep the default install light (~50 MB). Enable them on first install or any time afterwards:

```bash
# First install with voice
curl -fsSL https://raw.githubusercontent.com/astromanish/aishe-cli/main/setup.sh | AISHE_INSTALL_VOICE=1 bash

# Or add voice to an existing install (re-run setup)
cd ~/.local/share/aishe-cli && AISHE_INSTALL_VOICE=1 bash setup.sh
```

This downloads:
- **Kokoro TTS model** (~350 MB) — high-quality multi-voice TTS, 54 voices, CPU-runnable
- **faster-whisper STT model** — auto-downloaded on first request, default `small` (~460 MB)

After install, voice works out of the box:
```bash
aishe voice speak "Hello, I'm Aishe" -V F4   # synthesize
aishe voice transcribe recording.wav         # transcribe
aishe live                                   # full voice conversation
```

The STT uses **OpenAI Whisper** via `faster-whisper` (CTranslate2 port, CPU or GPU). The TTS uses **Kokoro** with Supertonic-style voice names (F1-F10, M1-M10) auto-mapped to Kokoro voices.

### Voice model tuning

```bash
# Use a smaller STT model (faster, less accurate)
AISHE_STT_MODEL=base AISHE_INSTALL_VOICE=1 bash setup.sh

# Use GPU STT (requires libcublas — usually pre-installed with nvidia-driver-535+)
AISHE_STT_DEVICE=cuda AISHE_INSTALL_VOICE=1 bash setup.sh
```

## Platform Support

| Platform | Status | Notes |
|----------|--------|-------|
| **macOS** | ✅ Full | Native audio playback (`afplay`), AVFoundation mic recording |
| **Linux** | ✅ Full | PulseAudio/ALSA mic recording, `aplay`/`paplay` playback |
| **Windows** | ✅ Core | DirectShow mic recording, PowerShell audio playback |

Voice features (STT/TTS) work on all platforms — they're HTTP services. Mic recording uses ffmpeg with the appropriate platform backend. Audio playback uses the native player for each OS.

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
│faster-   │     │  Kokoro  │
│whisper   │     │   TTS    │
│ STT:5093 │     │  :8766   │
└──────────┘     └──────────┘
```

The CLI talks to four local services:
- **DeepAgent** — LangGraph agent with tool calling, streaming, and conversation memory
- **Ollama** — LLM inference (local or cloud models)
- **faster-whisper STT** — OpenAI Whisper via CTranslate2 (CPU or GPU)
- **Kokoro TTS** — ONNX-based, 54 voices, ~350 MB model

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
