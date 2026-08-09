#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
#  Aishe CLI — One-Command Setup
#  "Voice-first AI assistant for your terminal"
#
#  Installs everything: Python deps, Ollama, DeepAgent sidecar,
#  configures services, and starts them on HTTP.
#
#  Supports: macOS, Linux, Windows (Git Bash / WSL)
#
#  Usage:
#    curl -fsSL https://raw.githubusercontent.com/astromanish/aishe-cli/main/setup.sh | bash
#    # or: bash setup.sh  (after cloning)
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
case "$PLATFORM" in
    Darwin)  OS_NAME="macOS" ;;
    Linux)   OS_NAME="Linux" ;;
    MINGW*|MSYS*|CYGWIN*) OS_NAME="Windows" ;;
    *)       OS_NAME="$PLATFORM" ;;
esac
info "Detected: ${OS_NAME} ${ARCH}"

# ── Self-install: if running from a temp dir, clone first ──
SCRIPT_SRC="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo "/tmp")"
INSTALL_DIR="${HOME}/.local/share/aishe-cli"

if echo "$SCRIPT_SRC" | grep -Eq "/tmp/|/Temp/|/temp/" || [ ! -f "${SCRIPT_SRC}/aishe" ]; then
    info "Running from temporary location — cloning repo to ${INSTALL_DIR}"

    if ! command -v git &>/dev/null; then
        fail "git is required. Install it first."
        exit 1
    fi

    rm -rf "$INSTALL_DIR"
    git clone --depth 1 https://github.com/astromanish/aishe-cli.git "$INSTALL_DIR" 2>&1 | tail -1
    pass "Cloned to ${INSTALL_DIR}"

    exec bash "${INSTALL_DIR}/setup.sh"
fi

info "Running from ${SCRIPT_SRC}"

# ── 1. Python check ─────────────────────────────────────────
echo -e "\n  ${BOLD}1. Python${NC}"
PYTHON=""
for cmd in python3 python python.exe; do
    if command -v "$cmd" &>/dev/null; then
        fullver=$("$cmd" --version 2>&1)
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

# ── 3. Install Ollama ──────────────────────────────────────
echo -e "\n  ${BOLD}3. Ollama${NC}"

install_ollama() {
    case "$OS_NAME" in
        macOS)
            if command -v brew &>/dev/null; then
                info "Installing Ollama via Homebrew..."
                brew install ollama 2>&1 | tail -1
            else
                info "Downloading Ollama for macOS..."
                curl -fsSL https://ollama.com/install.sh | sh 2>&1 | tail -3
            fi
            ;;
        Linux)
            info "Installing Ollama via official script..."
            curl -fsSL https://ollama.com/install.sh | sh 2>&1 | tail -3
            ;;
        Windows)
            warn "Ollama on Windows: Download from https://ollama.com/download"
            warn "  or: winget install Ollama.Ollama"
            return 1
            ;;
    esac
    return 0
}

if command -v ollama &>/dev/null; then
    pass "ollama found at $(command -v ollama)"
else
    if install_ollama; then
        pass "Ollama installed"
    else
        warn "Ollama installation skipped. Install manually: https://ollama.com/download"
    fi
fi

# ── 4. Start Ollama serve ──────────────────────────────────
echo -e "\n  ${BOLD}4. Start Ollama${NC}"

ollama_running() {
    curl -s --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1
}

if ollama_running; then
    pass "Ollama already running on http://localhost:11434"
else
    info "Starting Ollama server..."
    if command -v ollama &>/dev/null; then
        # Start in background
        nohup ollama serve > /tmp/ollama.log 2>&1 &
        OLLAMA_PID=$!
        # Wait for it to be ready
        for i in $(seq 1 15); do
            sleep 1
            if ollama_running; then
                pass "Ollama server started (PID: $OLLAMA_PID)"
                break
            fi
        done
        if ! ollama_running; then
            warn "Ollama may not have started. Check: cat /tmp/ollama.log"
        fi
    else
        warn "ollama command not found — skipping server start"
    fi
fi

# ── 5. Pull default model ──────────────────────────────────
echo -e "\n  ${BOLD}5. Pull Default Model${NC}"

DEFAULT_MODEL="${AISHE_MODEL:-qwen2.5:3b}"

if ollama_running; then
    # Check if model already exists
    if curl -s http://localhost:11434/api/tags | grep -q "$DEFAULT_MODEL"; then
        pass "Model ${DEFAULT_MODEL} already pulled"
    else
        info "Pulling ${DEFAULT_MODEL} (this may take a while)..."
        ollama pull "$DEFAULT_MODEL" 2>&1 | tail -1
        pass "Pulled ${DEFAULT_MODEL}"
    fi
else
    warn "Ollama not running — skipping model pull"
fi

# ── 6. Install DeepAgent sidecar ────────────────────────────
echo -e "\n  ${BOLD}6. DeepAgent Sidecar${NC}"

DEEPAGENT_DIR="${HOME}/.local/share/aishe-cli/deepagent"
DEEPAGENT_VENV="${DEEPAGENT_DIR}/.venv"

if [ -f "${DEEPAGENT_VENV}/bin/python" ]; then
    pass "DeepAgent venv already exists at ${DEEPAGENT_DIR}"
else
    info "Setting up DeepAgent sidecar at ${DEEPAGENT_DIR}..."
    mkdir -p "$DEEPAGENT_DIR"

    # Create server.py and agent.py
    cat > "${DEEPAGENT_DIR}/requirements.txt" << 'EOF'
fastapi>=0.100.0
uvicorn>=0.22.0
langchain>=1.3.0
langchain-ollama>=1.0.0
langchain-openai>=0.1.0
deepagents>=0.6.0
pydantic>=2.0
EOF

    cat > "${DEEPAGENT_DIR}/tools.py" << 'PYEOF'
"""Custom tools for the Aishe DeepAgent."""
from __future__ import annotations
import ast
import json
import operator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from langchain_core.tools import tool

_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

def _safe_eval(expr: str) -> float:
    tree = ast.parse(expr, mode="eval")
    def _eval(node):
        if isinstance(node, ast.Expression): return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)): return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS: return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS: return _UNARY_OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"unsupported expression node: {type(node).__name__}")
    return _eval(tree)

@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression safely. Supports + - * / // % ** and parentheses."""
    try: return str(_safe_eval(expression))
    except Exception as exc: return f"error: {exc}"

@tool
def get_current_time(timezone: str = "UTC") -> str:
    """Return the current local time in a given IANA timezone. Examples: UTC, Asia/Kolkata, America/New_York."""
    try: tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError: return f"error: unknown timezone '{timezone}'"
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

@tool
def word_stats(text: str) -> str:
    """Count words, characters, and sentences in a piece of text."""
    words = [w for w in text.split() if w]
    sentences = [s for s in text.replace("?", ".").replace("!", ".").split(".") if s.strip()]
    avg = sum(len(w) for w in words) / len(words) if words else 0
    return f"words={len(words)} chars={len(text)} sentences={len(sentences)} avg_word_len={avg:.1f}"

MEMORY_FILE = Path.home() / ".local" / "share" / "aishe" / "memory" / "facts.jsonl"

@tool
def memory_search(query: str) -> str:
    """Search the user's personal memory store for facts about them. Use this when the user asks about themselves."""
    if not MEMORY_FILE.exists(): return "No memories stored yet."
    query_words = [w for w in query.lower().split() if len(w) > 2]
    results = []
    for line in MEMORY_FILE.read_text().splitlines():
        if not line.strip(): continue
        try: entry = json.loads(line)
        except: continue
        fact_lower = entry.get("fact", "").lower()
        if any(word in fact_lower for word in query_words):
            results.append(f"• {entry['fact']} (saved {entry.get('timestamp', '?')[:10]})")
    if results: return "Relevant memories:\n" + "\n".join(results[:10])
    return "No matching memories found."

@tool
def memory_add(fact: str) -> str:
    """Save a new fact about the user to their personal memory store."""
    import uuid
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {"id": f"mem_{uuid.uuid4().hex}", "fact": fact, "timestamp": datetime.now().isoformat()}
    with open(MEMORY_FILE, "a") as f: f.write(json.dumps(entry) + "\n")
    return f"Saved: {fact}"
PYEOF

    cat > "${DEEPAGENT_DIR}/agent.py" << 'PYEOF'
"""Deep agent setup for Aishe CLI."""
from __future__ import annotations
import os
from pathlib import Path
from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_openai import ChatOpenAI
from tools import calculator, get_current_time, word_stats, memory_search, memory_add

MODEL_NAME = os.environ.get("AISHE_MODEL", "qwen2.5:3b")
BASE_URL = os.environ.get("AISHE_OLLAMA_URL", "http://localhost:11434/v1")
API_KEY = os.environ.get("AISHE_API_KEY", "ollama")

_model = ChatOpenAI(model=MODEL_NAME, base_url=BASE_URL, api_key=API_KEY, temperature=0.1)
WORKSPACE = Path(__file__).parent / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

SYSTEM_PROMPT = f"""You are a helpful research and analysis assistant.

You have these custom tools:
- `calculator`: evaluate arithmetic expressions like "(15 * 4) / 3"
- `get_current_time`: get the current time in any IANA timezone
- `word_stats`: count words, characters, and sentences in a piece of text
- `memory_search`: search the user's personal memory for facts about them
- `memory_add`: save a new fact about the user to their personal memory

IMPORTANT — always check memory first:
Whenever the user asks about themselves, call `memory_search` with a relevant keyword BEFORE answering.
When the user tells you something personal, call `memory_add` to save it.

You also have built-in tools to manage a todo list, read/write files in {WORKSPACE}, and delegate to a sub-agent named `researcher`.
Be concise in your final answer — short paragraphs, no preamble.
"""

RESEARCHER_SUBAGENT = {
    "name": "researcher",
    "description": "A focused research sub-agent for digging into specific sub-questions.",
    "system_prompt": "You are a focused research sub-agent. Answer concisely.",
}

def build_agent():
    return create_deep_agent(
        model=_model,
        tools=[calculator, get_current_time, word_stats, memory_search, memory_add],
        system_prompt=SYSTEM_PROMPT,
        subagents=[RESEARCHER_SUBAGENT],
        backend=FilesystemBackend(root_dir=WORKSPACE, virtual_mode=True),
        name="aishe-deepagent",
    )

agent = build_agent()

def extract_final_answer(state: dict) -> str:
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            content = msg.content
            if isinstance(content, str): return content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text": parts.append(block.get("text", ""))
                    elif isinstance(block, str): parts.append(block)
                return "\n".join(parts).strip()
    return ""
PYEOF

    cat > "${DEEPAGENT_DIR}/server.py" << 'PYEOF'
"""FastAPI server for Aishe DeepAgent."""
from __future__ import annotations
import json, os
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from agent import agent, extract_final_answer

HOST = os.environ.get("AISHE_HOST", "127.0.0.1")
PORT = int(os.environ.get("AISHE_PORT", "8765"))

class InvokeRequest(BaseModel):
    message: str = Field(..., description="The user's request")
    thread_id: str = Field(default="default", description="Conversation thread id")

class InvokeResponse(BaseModel):
    thread_id: str
    answer: str
    steps: int
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

class StreamRequest(InvokeRequest): pass

app = FastAPI(title="Aishe DeepAgent", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def root():
    return {"service": "aishe-deepagent", "model": os.environ.get("AISHE_MODEL", "qwen2.5:3b"), "endpoints": ["/health", "/tools", "/invoke", "/stream"]}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/tools")
def list_tools():
    try: nodes = agent.get_graph().nodes; return {"nodes": sorted(nodes.keys())}
    except: return {"nodes": []}

def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}

def _summarize_tool_calls(messages) -> list[dict[str, Any]]:
    calls = []
    for msg in messages:
        if getattr(msg, "type", None) == "ai":
            for tc in getattr(msg, "tool_calls", []) or []:
                calls.append({"name": tc.get("name"), "args": tc.get("args"), "id": tc.get("id")})
    return calls

@app.post("/invoke", response_model=InvokeResponse)
def invoke(req: InvokeRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    state = agent.invoke({"messages": [{"role": "user", "content": req.message}]}, config=_config(req.thread_id))
    answer = extract_final_answer(state)
    messages = state.get("messages", [])
    return InvokeResponse(thread_id=req.thread_id, answer=answer, steps=len(messages), tool_calls=_summarize_tool_calls(messages))

@app.post("/stream")
def stream(req: StreamRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    def _gen():
        last_answer = ""
        for event in agent.stream({"messages": [{"role": "user", "content": req.message}]}, config=_config(req.thread_id), stream_mode="messages"):
            runnable, raw = event
            chunk = runnable
            if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                for tc in chunk.tool_calls:
                    yield json.dumps({"event": "tool_call", "id": tc.get("id"), "name": tc.get("name"), "args": tc.get("args")}, default=str) + "\n"
                continue
            if hasattr(chunk, "type") and chunk.type == "tool":
                content = getattr(chunk, "content", "")
                yield json.dumps({"event": "tool_result", "result": str(content)[:500]}, default=str) + "\n"
                continue
            if hasattr(chunk, "type") and chunk.type in ("AIMessageChunk", "ai"):
                content = getattr(chunk, "content", None)
                if isinstance(content, str) and content:
                    last_answer += content
                    yield json.dumps({"event": "token", "content": content}) + "\n"
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                last_answer += text
                                yield json.dumps({"event": "token", "content": text}) + "\n"
        yield json.dumps({"event": "final", "answer": last_answer}) + "\n"
    return StreamingResponse(_gen(), media_type="application/x-ndjson")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=HOST, port=PORT, log_level="info")
PYEOF

    # Create venv and install deps
    info "Creating Python venv for DeepAgent..."
    "$PYTHON" -m venv "${DEEPAGENT_VENV}" 2>&1 | tail -1
    info "Installing DeepAgent dependencies..."
    "${DEEPAGENT_VENV}/bin/pip" install -r "${DEEPAGENT_DIR}/requirements.txt" --quiet 2>&1 | tail -1
    pass "DeepAgent sidecar ready at ${DEEPAGENT_DIR}"
fi

# ── 7. Start DeepAgent sidecar ──────────────────────────────
echo -e "\n  ${BOLD}7. Start DeepAgent${NC}"

deepagent_running() {
    curl -s --max-time 2 http://localhost:8765/health >/dev/null 2>&1
}

if deepagent_running; then
    pass "DeepAgent already running on http://localhost:8765"
else
    if [ -f "${DEEPAGENT_VENV}/bin/python" ]; then
        info "Starting DeepAgent sidecar..."
        nohup "${DEEPAGENT_VENV}/bin/python" "${DEEPAGENT_DIR}/server.py" > /tmp/deepagent.log 2>&1 &
        DA_PID=$!
        for i in $(seq 1 20); do
            sleep 1
            if deepagent_running; then
                pass "DeepAgent started (PID: $DA_PID) on http://localhost:8765"
                break
            fi
        done
        if ! deepagent_running; then
            warn "DeepAgent may not have started. Check: cat /tmp/deepagent.log"
        fi
    else
        warn "DeepAgent venv not found — skipping"
    fi
fi

# ── 8. Voice sidecars (STT + TTS) ─────────────────────────────
# Opt-in: set AISHE_INSTALL_VOICE=1 to auto-install + start STT/TTS
# Set AISHE_STT_DEVICE=cuda for GPU STT (needs libcublas; otherwise CPU)
# Set AISHE_STT_MODEL=base|small|medium|large-v3 to choose model size

echo -e "\n  ${BOLD}8. Voice Sidecars (STT + TTS)${NC}"

SIDECAR_DIR="${HOME}/.local/share/aishe-cli/sidecars"
SIDECAR_VENV="${SIDECAR_DIR}/.venv"
SIDECAR_MODELS="${SIDECAR_DIR}/models"

install_voice_sidecars() {
    if [ ! -f "${SIDECAR_VENV}/bin/python" ]; then
        info "Creating voice sidecar venv..."
        mkdir -p "${SIDECAR_DIR}"
        if command -v uv &>/dev/null; then
            uv venv "${SIDECAR_VENV}" --python 3.11 2>&1 | tail -1
        else
            "$PYTHON" -m venv "${SIDECAR_VENV}" 2>&1 | tail -1
        fi
        info "Installing STT (faster-whisper) + TTS (kokoro-onnx)..."
        if command -v uv &>/dev/null; then
            uv pip install --python "${SIDECAR_VENV}/bin/python" \
                faster-whisper "kokoro-onnx>=0.5.0" "fastapi>=0.110.0" \
                "uvicorn[standard]" python-multipart soundfile 2>&1 | tail -2
        else
            "${SIDECAR_VENV}/bin/pip" install --quiet \
                "faster-whisper>=1.0.0" "kokoro-onnx>=0.5.0" \
                "fastapi>=0.110.0" "uvicorn[standard]" python-multipart soundfile 2>&1 | tail -2
        fi
    else
        pass "Voice sidecar venv already exists at ${SIDECAR_VENV}"
    fi

    # Download Kokoro TTS model (~325 MB ONNX + 28 MB voices)
    mkdir -p "${SIDECAR_MODELS}/kokoro"
    if [ ! -f "${SIDECAR_MODELS}/kokoro/kokoro-v1.0.onnx" ] || \
       [ ! -f "${SIDECAR_MODELS}/kokoro/voices-v1.0.bin" ]; then
        info "Downloading Kokoro TTS model (~350 MB, one-time)..."
        if command -v curl &>/dev/null; then
            curl -fsSL -o "${SIDECAR_MODELS}/kokoro/kokoro-v1.0.onnx" \
                "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx" \
                && pass "Downloaded kokoro onnx" \
                || warn "Failed to download kokoro onnx"
            curl -fsSL -o "${SIDECAR_MODELS}/kokoro/voices-v1.0.bin" \
                "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin" \
                && pass "Downloaded kokoro voices" \
                || warn "Failed to download kokoro voices"
        else
            warn "curl not found — please download Kokoro models manually to ${SIDECAR_MODELS}/kokoro/"
        fi
    else
        pass "Kokoro TTS model already downloaded"
    fi

    pass "STT model (faster-whisper) will auto-download on first request"
}

stt_running() { curl -s --max-time 2 http://localhost:5093/healthz >/dev/null 2>&1; }
tts_running() { curl -s --max-time 2 http://localhost:8766/health >/dev/null 2>&1; }

if [ "${AISHE_INSTALL_VOICE:-0}" = "1" ]; then
    install_voice_sidecars

    # Start STT
    if stt_running; then
        pass "STT already running on http://localhost:5093"
    else
        info "Starting STT (faster-whisper) sidecar..."
        export AISHE_STT_DEVICE="${AISHE_STT_DEVICE:-cpu}"
        export AISHE_STT_MODEL="${AISHE_STT_MODEL:-small}"
        nohup "${SIDECAR_VENV}/bin/python" "${SCRIPT_SRC}/sidecars/stt_server.py" > /tmp/aishe_stt.log 2>&1 &
        for i in $(seq 1 60); do
            sleep 1
            if stt_running; then
                pass "STT started on http://localhost:5093 (device=${AISHE_STT_DEVICE}, model=${AISHE_STT_MODEL})"
                break
            fi
        done
        stt_running || warn "STT may not have started. Check: cat /tmp/aishe_stt.log"
    fi

    # Start TTS
    if tts_running; then
        pass "TTS already running on http://localhost:8766"
    else
        info "Starting TTS (kokoro-onnx) sidecar..."
        nohup "${SIDECAR_VENV}/bin/python" "${SCRIPT_SRC}/sidecars/tts_server.py" > /tmp/aishe_tts.log 2>&1 &
        for i in $(seq 1 30); do
            sleep 1
            if tts_running; then
                pass "TTS started on http://localhost:8766"
                break
            fi
        done
        tts_running || warn "TTS may not have started. Check: cat /tmp/aishe_tts.log"
    fi
else
    info "Skipping voice sidecars (set AISHE_INSTALL_VOICE=1 to install STT+TTS)"
    info "Voice services will be DOWN in aishe status until sidecars are running"
fi

# ── 9. Install ffmpeg ───────────────────────────────────────
echo -e "\n  ${BOLD}9. ffmpeg (for mic recording)${NC}"

if command -v ffmpeg &>/dev/null; then
    pass "ffmpeg found at $(command -v ffmpeg)"
else
    case "$OS_NAME" in
        macOS)
            if command -v brew &>/dev/null; then
                info "Installing ffmpeg via Homebrew..."
                brew install ffmpeg 2>&1 | tail -1 && pass "ffmpeg installed" || warn "ffmpeg install failed"
            else
                warn "Install ffmpeg: brew install ffmpeg"
            fi
            ;;
        Linux)
            if command -v apt-get &>/dev/null; then
                info "Installing ffmpeg via apt..."
                sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg 2>&1 | tail -1 && pass "ffmpeg installed" || warn "ffmpeg install failed"
            elif command -v pacman &>/dev/null; then
                info "Installing ffmpeg via pacman..."
                sudo pacman -S --noconfirm ffmpeg 2>&1 | tail -1 && pass "ffmpeg installed" || warn "ffmpeg install failed"
            else
                warn "Install ffmpeg: sudo apt-get install ffmpeg"
            fi
            ;;
        Windows)
            warn "ffmpeg not found. Download: https://ffmpeg.org/download.html"
            warn "  or: winget install ffmpeg"
            ;;
    esac
fi

# ── 10. Symlink to PATH ────────────────────────────────────
echo -e "\n  ${BOLD}10. Install aishe Command${NC}"

case "$OS_NAME" in
    Windows)
        TARGET_DIR="${HOME}/AppData/Local/Programs/aishe"
        TARGET="${TARGET_DIR}/aishe"
        mkdir -p "$TARGET_DIR"
        cp "${SCRIPT_SRC}/aishe" "$TARGET"
        cp -r "${SCRIPT_SRC}/aishe_pkg" "${TARGET_DIR}/aishe_pkg"
        pass "Installed aishe to ${TARGET}"
        warn "Add ${TARGET_DIR} to your PATH manually"
        ;;
    *)
        TARGET_DIR="${HOME}/.local/bin"
        mkdir -p "$TARGET_DIR"
        TARGET="${TARGET_DIR}/aishe"

        if [ -L "$TARGET" ] && [ "$(readlink "$TARGET")" = "${SCRIPT_SRC}/aishe" ]; then
            pass "aishe already linked to ${TARGET}"
        else
            ln -sf "${SCRIPT_SRC}/aishe" "$TARGET"
            chmod +x "${SCRIPT_SRC}/aishe"
            pass "Linked aishe → ${TARGET}"
        fi

        if [[ ":$PATH:" != *":${TARGET_DIR}:"* ]]; then
            warn "${TARGET_DIR} is not in your PATH"
            info "Add this to your shell config:"
            echo -e "       ${CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
        fi
        ;;
esac

# ── 11. Config directory ───────────────────────────────────
echo -e "\n  ${BOLD}11. Configuration${NC}"

case "$OS_NAME" in
    macOS)
        CONFIG_DIR="${HOME}/.config/aishe"
        DEFAULT_DATA_DIR="${HOME}/Library/Application Support/aishe"
        ;;
    Windows)
        CONFIG_DIR="${HOME}/.config/aishe"
        DEFAULT_DATA_DIR="${HOME}/AppData/Local/aishe"
        ;;
    *)
        CONFIG_DIR="${HOME}/.config/aishe"
        DEFAULT_DATA_DIR="${HOME}/.local/share/aishe"
        ;;
esac

mkdir -p "$CONFIG_DIR"

if [ ! -f "${CONFIG_DIR}/config.yaml" ]; then
    cat > "${CONFIG_DIR}/config.yaml" << YAMLEOF
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
  dir: "${DEFAULT_DATA_DIR}"
ui:
  color: true
YAMLEOF
    pass "Created default config at ${CONFIG_DIR}/config.yaml"
else
    pass "Config already exists at ${CONFIG_DIR}/config.yaml"
fi

# ── 12. Data directories ───────────────────────────────────
echo -e "\n  ${BOLD}12. Data Directories${NC}"
mkdir -p "${DEFAULT_DATA_DIR}/memory" "${DEFAULT_DATA_DIR}/threads"
pass "Created data directories at ${DEFAULT_DATA_DIR}"

# ── Done ───────────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}╭──────────────────────────────────────╮${NC}"
echo -e "  ${BOLD}│${NC}        ${GREEN}✨ Setup Complete! ✨${NC}         ${BOLD}│${NC}"
echo -e "  ${BOLD}╰──────────────────────────────────────╯${NC}"
echo ""
echo -e "  ${CYAN}Services running:${NC}"
if ollama_running; then echo -e "     ${GREEN}●${NC} Ollama      ${DIM}http://localhost:11434${NC}"; fi
if deepagent_running; then echo -e "     ${GREEN}●${NC} DeepAgent   ${DIM}http://localhost:8765${NC}"; fi
if stt_running 2>/dev/null; then echo -e "     ${GREEN}●${NC} STT (Whisper) ${DIM}http://localhost:5093${NC}"; fi
if tts_running 2>/dev/null; then echo -e "     ${GREEN}●${NC} TTS (Kokoro)  ${DIM}http://localhost:8766${NC}"; fi
echo ""
echo -e "  ${CYAN}Next steps:${NC}"
echo -e "  1. Run: ${CYAN}aishe status${NC}"
echo ""
echo -e "  2. Start chatting: ${CYAN}aishe chat \"Hello!\"${NC}"
echo -e "     Or go live:      ${CYAN}aishe live${NC}"
echo ""
if [ "${AISHE_INSTALL_VOICE:-0}" != "1" ]; then
echo -e "  ${CYAN}Enable voice (STT+TTS) anytime:${NC}"
echo -e "     ${CYAN}AISHE_INSTALL_VOICE=1 bash ${SCRIPT_SRC}/setup.sh${NC}"
echo -e "  ${DIM}This downloads Kokoro TTS (~350 MB) + faster-whisper STT.${NC}"
echo -e "  ${DIM}After that: aishe live works with full voice.${NC}"
echo ""
fi
