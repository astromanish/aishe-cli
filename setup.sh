#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
#  Aishe CLI — One-Command Setup
#  "Voice-first AI assistant for your terminal"
# ─────────────────────────────────────────────────────────────

BOLD='\033[1m'
GREEN='\033[32m'
CYAN='\033[36m'
YELLOW='\033[33m'
RED='\033[31m'
DIM='\033[2m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }

echo ""
echo -e "  ${BOLD}╭──────────────────────────────────────╮${NC}"
echo -e "  ${BOLD}│${NC}        ${CYAN}✨ Aishe CLI Setup ✨${NC}        ${BOLD}│${NC}"
echo -e "  ${BOLD}│${NC}  ${DIM}Voice-first AI for your terminal${NC}   ${BOLD}│${NC}"
echo -e "  ${BOLD}╰──────────────────────────────────────╯${NC}"
echo ""

# ── Detect platform ────────────────────────────────────────
PLATFORM="$(uname -s)"
ARCH="$(uname -m)"
info "Detected: ${PLATFORM} ${ARCH}"

# ── 1. Python check ─────────────────────────────────────────
echo -e "\n  ${BOLD}1. Python${NC}"
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        fullver=$("$cmd" --version 2>&1)
        # Extract major.minor (e.g. "3.12" from "Python 3.12.13")
        ver=$(echo "$fullver" | awk '{print $2}' | cut -d. -f1,2)
        major=$(echo "$ver" | cut -d. -f1)
        if [ "$major" -ge 3 ]; then
            PYTHON="$cmd"
            pass "Python ${ver} found at $(command -v $cmd)"
            break
        fi
    fi
done
if [ -z "$PYTHON" ]; then
    fail "Python 3 not found. Install it: https://python.org/downloads/"
    exit 1
fi

# ── 2. pip dependencies ────────────────────────────────────
echo -e "\n  ${BOLD}2. Python Dependencies${NC}"
DEPS=("requests" "pyyaml")
for dep in "${DEPS[@]}"; do
    if "$PYTHON" -c "import ${dep%%=*}" 2>/dev/null; then
        pass "${dep} already installed"
    else
        info "Installing ${dep}..."
        "$PYTHON" -m pip install "$dep" --quiet 2>/dev/null && pass "${dep} installed" || warn "Could not install ${dep}"
    fi
done

# Optional: webrtcvad for voice activity detection
if "$PYTHON" -c "import webrtcvad" 2>/dev/null; then
    pass "webrtcvad (VAD) already installed"
else
    info "webrtcvad (VAD) not found — optional, install with: pip install webrtcvad"
fi

# ── 3. External tools ──────────────────────────────────────
echo -e "\n  ${BOLD}3. External Tools${NC}"

# ffmpeg
if command -v ffmpeg &>/dev/null; then
    pass "ffmpeg found at $(command -v ffmpeg)"
else
    if [[ "$PLATFORM" == "Darwin" ]]; then
        warn "ffmpeg not found. Install: brew install ffmpeg"
    elif command -v apt-get &>/dev/null; then
        warn "ffmpeg not found. Install: sudo apt-get install ffmpeg"
    else
        warn "ffmpeg not found — needed for mic recording"
    fi
fi

# ollama
if command -v ollama &>/dev/null; then
    pass "ollama found at $(command -v ollama)"
else
    warn "ollama not found. Install: https://ollama.com/download"
fi

# ── 4. Symlink to PATH ────────────────────────────────────
echo -e "\n  ${BOLD}4. Install aishe Command${NC}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="${HOME}/.local/bin"
mkdir -p "$TARGET_DIR"
TARGET="${TARGET_DIR}/aishe"

if [ -L "$TARGET" ] && [ "$(readlink "$TARGET")" = "${SCRIPT_DIR}/aishe" ]; then
    pass "aishe already linked to ${TARGET}"
else
    ln -sf "${SCRIPT_DIR}/aishe" "$TARGET"
    chmod +x "${SCRIPT_DIR}/aishe"
    pass "Linked aishe → ${TARGET}"
fi

# Check if TARGET_DIR is in PATH
if [[ ":$PATH:" != *":${TARGET_DIR}:"* ]]; then
    warn "${TARGET_DIR} is not in your PATH"
    info "Add this to your ~/.zshrc or ~/.bashrc:"
    echo -e "       ${CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
fi

# ── 5. Config directory ────────────────────────────────────
echo -e "\n  ${BOLD}5. Configuration${NC}"
CONFIG_DIR="${HOME}/.config/aishe"
mkdir -p "$CONFIG_DIR"
if [ ! -f "${CONFIG_DIR}/config.yaml" ]; then
    cat > "${CONFIG_DIR}/config.yaml" << 'YAMLEOF'
# Aishe CLI Configuration
services:
  deepagent: "http://localhost:8765"
  ollama: "http://localhost:11434"
  stt: "http://localhost:5093"
  tts: "http://localhost:8766"
voice:
  default_voice: "F4"
  recording_duration: 5
  vad_enabled: true
  vad_threshold: 0.5
  vad_min_speech_duration_ms: 250
  vad_min_silence_duration_ms: 500
data:
  dir: "~/Library/Application Support/aishe"
ui:
  color: true
YAMLEOF
    pass "Created default config at ${CONFIG_DIR}/config.yaml"
else
    pass "Config already exists at ${CONFIG_DIR}/config.yaml"
fi

# ── 6. Data directories ────────────────────────────────────
echo -e "\n  ${BOLD}6. Data Directories${NC}"
DATA_DIR="${HOME}/Library/Application Support/aishe"
mkdir -p "${DATA_DIR}/memory" "${DATA_DIR}/threads" "${DATA_DIR}/intent_lab"
pass "Created data directories at ${DATA_DIR}"

# ── Done ───────────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}╭──────────────────────────────────────╮${NC}"
echo -e "  ${BOLD}│${NC}        ${GREEN}✨ Setup Complete! ✨${NC}         ${BOLD}│${NC}"
echo -e "  ${BOLD}╰──────────────────────────────────────╯${NC}"
echo ""
echo -e "  ${CYAN}Next steps:${NC}"
echo -e "  1. Start the required services:"
echo -e "     ${DIM}  ollama serve &${NC}"
echo -e "     ${DIM}  cd aishe-tauri/deepagent && .venv/bin/python server.py &${NC}"
echo -e "     ${DIM}  # Parakeet STT and Supertonic TTS (see docs)${NC}"
echo ""
echo -e "  2. Run: ${CYAN}aishe status${NC}"
echo ""
echo -e "  3. Start chatting: ${CYAN}aishe chat \"Hello!\"${NC}"
echo -e "     Or go live:      ${CYAN}aishe live${NC}"
echo ""
