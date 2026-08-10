#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
#  Aishe CLI — One-Command Setup
#  "Voice-first AI assistant for your terminal"
#
#  Streamlined install: Python deps + DeepAgent sidecar + CLI.
#  Ollama and voice (STT/TTS) are NOT installed here — install
#  them separately beforehand (see README).
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

# ── 3. Prereq check: Ollama (not installed here) ───────────
echo -e "\n  ${BOLD}3. Prerequisites${NC}"
if command -v ollama &>/dev/null; then
    pass "ollama found at $(command -v ollama)"
else
    warn "ollama not found — install it first: https://ollama.com/download"
    warn "  then pull a model: ollama pull deepseek-v4-flash:cloud"
fi
if curl -s --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
    pass "Ollama running on http://localhost:11434"
else
    warn "Ollama not running — start it: ollama serve"
fi

# ── 4. Install DeepAgent sidecar ────────────────────────────
echo -e "\n  ${BOLD}4. DeepAgent Sidecar${NC}"

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
mem0ai>=0.1.0
qdrant-client>=1.9.0
fastembed>=0.3.0
EOF

    cat > "${DEEPAGENT_DIR}/mem0_memory.py" << 'PYEOF'
"""Semantic memory for Aishe — mem0 over Qdrant with local Ollama embeddings.

Provides richer memory than plain JSONL: automatic fact extraction, deduplication,
and hybrid BM25 + semantic (vector) search.

Stack:
  - Vector store: Qdrant local at http://localhost:6333
  - Embeddings:  local Ollama qwen3-embedding:0.6b (1024-dim)
  - LLM (extraction): deepseek-v4-flash:cloud via Ollama OpenAI-compatible endpoint

Falls back to the legacy JSONL store if mem0 / Qdrant are unavailable, so the
CLI keeps working even without the vector backend.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ─── Config ────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("AISHE_DATA_DIR", str(Path.home() / "aishe")))
MEMORY_DIR = DATA_DIR / "memory"
LEGACY_FILE = MEMORY_DIR / "facts.jsonl"

QDRANT_URL = os.environ.get("AISHE_QDRANT_URL", "http://localhost:6333")
COLLECTION = os.environ.get("AISHE_MEMORY_COLLECTION", "aishe_memories")
EMBED_MODEL = os.environ.get("AISHE_EMBED_MODEL", "qwen3-embedding:0.6b")
EMBED_DIMS = int(os.environ.get("AISHE_EMBED_DIMS", "1024"))
LLM_MODEL = os.environ.get("AISHE_MODEL", "deepseek-v4-flash:cloud")
OLLAMA_URL = os.environ.get("AISHE_OLLAMA_URL", "http://localhost:11434")
# Normalize: strip a trailing /v1 so the base works whether the env var has it or not.
OLLAMA_BASE = OLLAMA_URL.rstrip("/")
if OLLAMA_BASE.endswith("/v1"):
    OLLAMA_BASE = OLLAMA_BASE[:-3]
LLM_API_KEY = os.environ.get("AISHE_API_KEY", "ollama")

os.environ.setdefault("MEM0_TELEMETRY", "false")

_mem0 = None
_mem0_error: Optional[str] = None


def _build_mem0():
    """Lazily build the mem0 client. Returns None (with _mem0_error set) on failure."""
    global _mem0, _mem0_error
    if _mem0 is not None or _mem0_error is not None:
        return _mem0

    try:
        from mem0 import Memory
    except ImportError:
        _mem0_error = "mem0 not installed (pip install mem0ai)"
        return None

    config = {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "url": QDRANT_URL,
                "collection_name": COLLECTION,
                "embedding_model_dims": EMBED_DIMS,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": LLM_MODEL,
                "temperature": 0.1,
                "max_tokens": 1024,
                "api_key": LLM_API_KEY,
                "openai_base_url": f"{OLLAMA_BASE}/v1",
            },
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": EMBED_MODEL,
                "embedding_dims": EMBED_DIMS,
                "ollama_base_url": OLLAMA_BASE,
            },
        },
    }

    try:
        _mem0 = Memory.from_config(config)
    except Exception as exc:  # noqa: BLE001
        _mem0_error = f"mem0 init failed: {exc}"
        return None
    return _mem0


def _qdrant_up() -> bool:
    """Check whether Qdrant is reachable."""
    try:
        import requests
        return requests.get(f"{QDRANT_URL}/collections", timeout=2).status_code == 200
    except Exception:
        return False


def _now() -> str:
    return datetime.now().isoformat()


# ─── Public API ────────────────────────────────────────────────────────────

def add(fact: str) -> str:
    """Add a fact to semantic memory. Returns the entry ID."""
    m = _build_mem0()
    if m is not None and _qdrant_up():
        try:
            m.add(fact, user_id="aishe")
            return f"mem_{uuid.uuid4().hex}"
        except Exception as exc:  # noqa: BLE001
            _mem0_error = f"mem0 add failed: {exc}"
            # fall through to legacy

    # Legacy JSONL fallback
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"id": f"mem_{uuid.uuid4().hex}", "fact": fact, "timestamp": _now()}
    with open(LEGACY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry["id"]


def search(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Semantic search over memories. Returns list of {id, fact, timestamp}."""
    m = _build_mem0()
    if m is not None and _qdrant_up():
        try:
            results = m.search(query, user_id="aishe", limit=limit)
            out: List[Dict[str, Any]] = []
            for r in results:
                mem = r.get("memory", "")
                meta = r.get("metadata", {}) or {}
                out.append({
                    "id": meta.get("id", f"mem_{uuid.uuid4().hex}"),
                    "fact": mem,
                    "timestamp": meta.get("created_at", ""),
                    "score": r.get("score"),
                })
            return out
        except Exception as exc:  # noqa: BLE001
            _mem0_error = f"mem0 search failed: {exc}"
            # fall through to legacy

    # Legacy JSONL fallback (substring)
    if not LEGACY_FILE.exists():
        return []
    ql = query.lower()
    results: List[Dict[str, Any]] = []
    for line in LEGACY_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ql in entry.get("fact", "").lower():
            results.append(entry)
    return results[:limit]


def list_all() -> List[Dict[str, Any]]:
    """List all memories (legacy JSONL only — mem0 has no simple list-all)."""
    if not LEGACY_FILE.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for line in LEGACY_FILE.read_text().splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def clear() -> int:
    """Delete all memories. Returns count of deleted entries."""
    if not LEGACY_FILE.exists():
        return 0
    count = sum(1 for l in LEGACY_FILE.read_text().splitlines() if l.strip())
    LEGACY_FILE.unlink()
    return count


def count() -> int:
    """Count memory entries (legacy JSONL)."""
    if not LEGACY_FILE.exists():
        return 0
    return sum(1 for l in LEGACY_FILE.read_text().splitlines() if l.strip())


def status() -> str:
    """Human-readable backend status."""
    m = _build_mem0()
    if m is not None and _qdrant_up():
        return f"mem0 + Qdrant ({COLLECTION}) — embeddings {EMBED_MODEL}"
    if _mem0_error:
        return f"legacy JSONL (mem0 unavailable: {_mem0_error})"
    return "legacy JSONL (Qdrant not running)"


# ─── CLI handlers ───────────────────────────────────────────────────────────

def cmd_memory(args: Any) -> None:
    from .util import bold, cyan, dim, green, red

    action = args.action

    if action == "add":
        fact = " ".join(args.value) if isinstance(args.value, list) else args.value
        if not fact:
            fact = sys.stdin.read().strip()
        if not fact:
            print(red("No fact provided"))
            sys.exit(1)
        mid = add(fact)
        print(f"{green('Added')} {mid}: {fact}")
        return

    if action == "search":
        query = " ".join(args.value) if isinstance(args.value, list) else args.value
        if not query:
            print(red("No query provided"))
            sys.exit(1)
        results = search(query)
        if results:
            print(bold(f"Found {len(results)} match(es):"))
            for r in results:
                print(f"  {cyan(r['id'])}  {r['fact']}")
                print(f"  {dim(r.get('timestamp', ''))}")
        else:
            print(dim("No matches found."))
        return

    if action == "list":
        entries = list_all()
        if not entries:
            print(dim("No memories stored."))
            return
        print(bold(f"Memories ({len(entries)})"))
        print("─" * 60)
        for e in entries:
            print(f"  {cyan(e['id'])}  {e['fact']}")
            print(f"  {dim(e['timestamp'])}")
        return

    if action == "clear":
        count = clear()
        print(f"{red('Cleared')} {count} memories")
        return

    if action == "status":
        print(status())
        return
PYEOF

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

# ─── Semantic memory (mem0 + Qdrant) ───────────────────────────────────────
# Richer than JSONL: auto-extraction, dedup, hybrid BM25 + semantic search.
# Falls back to the legacy JSONL store if mem0/Qdrant are unavailable.

def _mem0_add(fact: str) -> str:
    """Add a fact to semantic memory. Returns a confirmation string."""
    try:
        from mem0_memory import add as _add
        _add(fact)
        return f"Saved: {fact}"
    except Exception as exc:
        # Fallback to legacy JSONL
        import uuid
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {"id": f"mem_{uuid.uuid4().hex}", "fact": fact, "timestamp": datetime.now().isoformat()}
        with open(MEMORY_FILE, "a") as f: f.write(json.dumps(entry) + "\n")
        return f"Saved (legacy): {fact}"


def _mem0_search(query: str) -> str:
    """Search semantic memory. Returns formatted results."""
    try:
        from mem0_memory import search as _search
        results = _search(query, limit=10)
        if results:
            lines = [f"• {r['fact']} (saved {r.get('timestamp', '?')[:10]})" for r in results]
            return "Relevant memories:\n" + "\n".join(lines)
        return "No matching memories found."
    except Exception:
        # Fallback to legacy JSONL substring search
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
def memory_search(query: str) -> str:
    """Search the user's personal memory store for facts about them. Use this when the user asks about themselves."""
    return _mem0_search(query)


@tool
def memory_add(fact: str) -> str:
    """Save a new fact about the user to their personal memory store."""
    return _mem0_add(fact)
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

MODEL_NAME = os.environ.get("AISHE_MODEL", "deepseek-v4-flash:cloud")
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
    return {"service": "aishe-deepagent", "model": os.environ.get("AISHE_MODEL", "deepseek-v4-flash:cloud"), "endpoints": ["/health", "/tools", "/invoke", "/stream"]}

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

# ── 5. Start DeepAgent sidecar ──────────────────────────────
echo -e "\n  ${BOLD}5. Start DeepAgent${NC}"

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

# ── 6. Qdrant (semantic memory backend) ─────────────────────
echo -e "\n  ${BOLD}6. Qdrant (Memory)${NC}"

QDRANT_BIN="${HOME}/.local/bin/qdrant"
QDRANT_DATA="${HOME}/.local/share/aishe-cli/qdrant"

qdrant_running() {
    curl -s --max-time 2 http://localhost:6333/collections >/dev/null 2>&1
}

if qdrant_running; then
    pass "Qdrant already running on http://localhost:6333"
else
    if [ -f "$QDRANT_BIN" ]; then
        info "Starting Qdrant (data: ${QDRANT_DATA})..."
        mkdir -p "$QDRANT_DATA"
        # Qdrant stores data in its working directory — cd into the data dir
        (cd "$QDRANT_DATA" && nohup "$QDRANT_BIN" > /tmp/qdrant.log 2>&1 &)
        for i in $(seq 1 20); do
            sleep 1
            if qdrant_running; then
                pass "Qdrant started on http://localhost:6333"
                break
            fi
        done
        if ! qdrant_running; then
            warn "Qdrant may not have started. Check: cat /tmp/qdrant.log"
        fi
    else
        warn "Qdrant binary not found at ${QDRANT_BIN}"
        warn "  Install it: https://qdrant.tech/documentation/guides/installation/"
        warn "  Semantic memory will fall back to legacy JSONL until Qdrant runs."
    fi
fi

# ── 7. Symlink to PATH ────────────────────────────────────
echo -e "\n  ${BOLD}7. Install aishe Command${NC}"

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

# ── 7. Config directory ───────────────────────────────────
echo -e "\n  ${BOLD}8. Configuration${NC}"

CONFIG_DIR="${HOME}/.config/aishe"
DEFAULT_DATA_DIR="${HOME}/aishe"

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

# ── 8. Data directories ───────────────────────────────────
echo -e "\n  ${BOLD}9. Data Directories${NC}"
mkdir -p "${DEFAULT_DATA_DIR}/memory" "${DEFAULT_DATA_DIR}/threads"
pass "Created data directories at ${DEFAULT_DATA_DIR}"

# ── Done ───────────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}╭──────────────────────────────────────╮${NC}"
echo -e "  ${BOLD}│${NC}        ${GREEN}✨ Setup Complete! ✨${NC}         ${BOLD}│${NC}"
echo -e "  ${BOLD}╰──────────────────────────────────────╯${NC}"
echo ""
echo -e "  ${CYAN}Services running:${NC}"
if deepagent_running; then echo -e "     ${GREEN}●${NC} DeepAgent   ${DIM}http://localhost:8765${NC}"; fi
if qdrant_running; then echo -e "     ${GREEN}●${NC} Qdrant      ${DIM}http://localhost:6333${NC}"; fi
echo ""
echo -e "  ${CYAN}Next steps:${NC}"
echo -e "  1. Run: ${CYAN}aishe status${NC}"
echo ""
echo -e "  2. Start chatting: ${CYAN}aishe${NC}"
echo -e "     Or go live:      ${CYAN}aishe live${NC}"
echo ""
echo -e "  ${DIM}Note: Ollama + voice (STT/TTS) are installed separately.${NC}"
echo -e "  ${DIM}Ensure Ollama is running (ollama serve) with a model pulled.${NC}"
echo -e "  ${DIM}For voice, install STT/TTS sidecars per the README.${NC}"
echo -e "  ${DIM}Memory uses mem0 + Qdrant (semantic). Falls back to JSONL if Qdrant is down.${NC}"
echo ""
