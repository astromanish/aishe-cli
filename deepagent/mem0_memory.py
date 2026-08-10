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
