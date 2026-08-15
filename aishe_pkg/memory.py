"""Memory management for Aishe CLI.

Three layers (Aug 2026 upgrade):
  1. Vector memory (primary) — mem0 over Qdrant (collection aishe_memories),
     proxied through the DeepAgent sidecar HTTP API (:8765 /memory/*) so the
     CLI needs no mem0ai dependency. Semantic search + dedup + hybrid BM25.
  2. Legacy JSONL fallback — facts.jsonl when the sidecar/Qdrant is down.
  3. Short-term memory files — soul.md (assistant identity) and user.md
     (user facts), plain markdown, editable, injected into the agent.

Long-term semantic facts live in the vector store; short-term context lives
in soul.md / user.md.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get
from .util import bold, cyan, dim, green, red

DATA_DIR = Path(get("data.dir", str(Path.home() / "aishe")))
MEMORY_DIR = DATA_DIR / "memory"
MEMORY_FILE = MEMORY_DIR / "facts.jsonl"
SOUL_FILE = MEMORY_DIR / "soul.md"
USER_FILE = MEMORY_DIR / "user.md"

DEEPAGENT_URL = get("services.deepagent", "http://localhost:8765")


def ensure_dirs() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now().isoformat()


# ─── Vector memory via DeepAgent HTTP (primary) ────────────────────────────

def _deepagent_up() -> bool:
    try:
        import requests
        return requests.get(f"{DEEPAGENT_URL}/health", timeout=2).status_code == 200
    except Exception:
        return False


def _http_json(method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 15):
    """Call the DeepAgent sidecar. Returns parsed JSON or None on failure."""
    import requests
    try:
        if method == "GET":
            r = requests.get(f"{DEEPAGENT_URL}{path}", timeout=timeout)
        else:
            r = requests.post(f"{DEEPAGENT_URL}{path}", json=payload or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ─── Legacy JSONL helpers ──────────────────────────────────────────────────

def _read_legacy() -> List[Dict[str, Any]]:
    if not MEMORY_FILE.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for line in MEMORY_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _write_legacy(entries: List[Dict[str, Any]]) -> None:
    ensure_dirs()
    MEMORY_FILE.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


# ─── Public API ────────────────────────────────────────────────────────────

def add(fact: str) -> str:
    """Add a fact to memory (vector store via DeepAgent; JSONL fallback). Returns the entry ID."""
    if _deepagent_up():
        data = _http_json("POST", "/memory/add", {"fact": fact})
        if data and data.get("id"):
            return data["id"]
    # Legacy JSONL fallback
    ensure_dirs()
    entry = {"id": f"mem_{uuid.uuid4().hex}", "fact": fact, "timestamp": _now()}
    with open(MEMORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry["id"]


def search(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search memories (semantic via vector store; substring fallback)."""
    if _deepagent_up():
        data = _http_json("POST", "/memory/search", {"query": query, "limit": limit})
        if data and "results" in data:
            return data["results"]
    # Legacy JSONL fallback
    ql = query.lower()
    results: List[Dict[str, Any]] = []
    for entry in _read_legacy():
        if ql in entry.get("fact", "").lower():
            results.append(entry)
    return results[:limit]


def update(memory_id: str, text: str) -> bool:
    """Update a memory's text by ID (vector store; JSONL fallback)."""
    if _deepagent_up():
        data = _http_json("POST", "/memory/update", {"memory_id": memory_id, "text": text})
        if data is not None:
            return data.get("status") == "ok"
    entries = _read_legacy()
    changed = False
    for e in entries:
        if e.get("id") == memory_id:
            e["fact"] = text
            e["updated_at"] = _now()
            changed = True
            break
    if changed:
        _write_legacy(entries)
    return changed


def delete(memory_id: str) -> bool:
    """Delete a memory by ID (vector store; JSONL fallback)."""
    if _deepagent_up():
        data = _http_json("POST", "/memory/delete", {"memory_id": memory_id})
        if data is not None:
            return data.get("status") == "ok"
    entries = _read_legacy()
    kept = [e for e in entries if e.get("id") != memory_id]
    if len(kept) != len(entries):
        _write_legacy(kept)
        return True
    return False


def list_all() -> List[Dict[str, Any]]:
    """List all memories (vector store via DeepAgent; JSONL fallback)."""
    if _deepagent_up():
        data = _http_json("GET", "/memory/list")
        if data and "results" in data:
            return data["results"]
    return _read_legacy()


def clear() -> int:
    """Delete all memories. Returns count of deleted entries."""
    entries = list_all()
    n = len(entries)
    if _deepagent_up():
        # Clear vector store per-user, then legacy file (server does both).
        for e in entries:
            _http_json("POST", "/memory/delete", {"memory_id": e.get("id", "")})
    if MEMORY_FILE.exists():
        MEMORY_FILE.unlink()
    return n


def count() -> int:
    """Count memory entries (vector store when reachable)."""
    try:
        return len(list_all())
    except Exception:
        return len(_read_legacy())


def seed() -> int:
    """Push legacy JSONL facts into the vector store. Returns number added."""
    if not _deepagent_up():
        return 0
    data = _http_json("POST", "/memory/seed")
    return (data or {}).get("seeded", 0)


def status() -> str:
    """Human-readable backend status."""
    if _deepagent_up():
        data = _http_json("GET", "/memory/status")
        if data and data.get("status"):
            return data["status"]
    return "legacy JSONL (DeepAgent sidecar unreachable)"


# ─── Short-term memory files (soul.md / user.md) ───────────────────────────

def _ensure_files() -> None:
    ensure_dirs()
    if not SOUL_FILE.exists():
        SOUL_FILE.write_text(
            "# Aishe — Soul\n\n"
            "You are Aishe, a warm, efficient voice-first AI assistant for India.\n"
            "Be concise, helpful, and honest. Use memory to remember the user.\n"
        )
    if not USER_FILE.exists():
        USER_FILE.write_text(
            "# User — Short-term memory\n\n"
            "_Edit me or ask Aishe to remember things. Long-term facts live in vector memory._\n"
        )


def get_soul() -> str:
    _ensure_files()
    return SOUL_FILE.read_text()


def set_soul(text: str) -> None:
    ensure_dirs()
    SOUL_FILE.write_text(text)


def get_user_md() -> str:
    _ensure_files()
    return USER_FILE.read_text()


def append_user_md(fact: str) -> None:
    """Append a fact line to user.md (short-term memory)."""
    _ensure_files()
    with open(USER_FILE, "a") as f:
        f.write(f"\n- {fact} ({_now()[:10]})")


def export_csv(path: str) -> int:
    """Export all memories to CSV. Returns count."""
    import csv
    entries = list_all()
    if not entries:
        return 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "fact", "timestamp"])
        for e in entries:
            w.writerow([e.get("id", ""), e.get("fact", ""), e.get("timestamp", "")])
    return len(entries)


def export_markdown(path: str) -> int:
    """Export all memories to Markdown. Returns count."""
    entries = list_all()
    if not entries:
        return 0
    with open(path, "w") as f:
        f.write("# Aishe Memory Export\n\n")
        for e in entries:
            f.write(f"- **{e.get('id', '')}** ({e.get('timestamp', '')}): {e.get('fact', '')}\n")
    return len(entries)


# ─── CLI handlers ───────────────────────────────────────────────────────────

def _print_results(results: List[Dict[str, Any]]) -> None:
    if not results:
        print(dim("No matches found."))
        return
    print(bold(f"Found {len(results)} match(es):"))
    for r in results:
        print(f"  {cyan(r.get('id', '?'))}  {r.get('fact', '')}")
        ts = r.get("timestamp", "") or ""
        score = r.get("score")
        extra = f"  score={score:.3f}" if isinstance(score, (int, float)) else ""
        print(f"  {dim(ts)}{dim(extra)}")


def cmd_memory(args: Any) -> None:
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
        _print_results(search(query))
        return

    if action == "list":
        entries = list_all()
        if not entries:
            print(dim("No memories stored."))
            return
        print(bold(f"Memories ({len(entries)})"))
        print("─" * 60)
        for e in entries:
            print(f"  {cyan(e.get('id', '?'))}  {e.get('fact', '')}")
            print(f"  {dim(e.get('timestamp', ''))}")
        return

    if action == "update":
        memory_id = args.value[0] if isinstance(args.value, list) and args.value else None
        text = " ".join(args.value[1:]) if isinstance(args.value, list) and len(args.value) > 1 else ""
        if not memory_id or not text:
            print(red("Usage: aishe memory update <id> <new text>"))
            sys.exit(1)
        if update(memory_id, text):
            print(f"{green('Updated')} {memory_id}")
        else:
            print(red(f"Memory {memory_id} not found"))
        return

    if action == "delete":
        memory_id = args.value[0] if isinstance(args.value, list) and args.value else None
        if not memory_id:
            print(red("Usage: aishe memory delete <id>"))
            sys.exit(1)
        if delete(memory_id):
            print(f"{green('Deleted')} {memory_id}")
        else:
            print(red(f"Memory {memory_id} not found"))
        return

    if action == "clear":
        n = clear()
        print(f"{red('Cleared')} {n} memories")
        return

    if action == "seed":
        n = seed()
        print(f"{green('Seeded')} {n} legacy facts into vector memory")
        return

    if action == "status":
        print(status())
        return

    if action == "soul":
        text = " ".join(args.value) if isinstance(args.value, list) else args.value
        if text:
            set_soul(text)
            print(f"{green('Soul updated')} ({len(text)} chars)")
        else:
            print(get_soul())
        return

    if action == "user":
        text = " ".join(args.value) if isinstance(args.value, list) else args.value
        # Allow either `aishe memory user add "fact"` or `aishe memory user "fact"`
        if isinstance(text, str) and text.lower().startswith("add "):
            text = text[4:].strip()
        if text:
            append_user_md(text)
            print(f"{green('Added to user.md')}: {text}")
        else:
            print(get_user_md())
        return
