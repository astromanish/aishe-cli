"""Thread management — persistent conversation threads as JSON."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get

DATA_DIR = Path(get("data.dir", str(Path.home() / "aishe")))
THREADS_DIR = DATA_DIR / "threads"


def ensure_dirs() -> None:
    THREADS_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now().isoformat()


def _first_user_message(messages: List[Dict[str, Any]]) -> str:
    """Return the first user message content, truncated for a title."""
    for msg in messages:
        if msg.get("role") == "user":
            text = msg.get("content", "").strip().replace("\n", " ")
            return text[:60] + ("..." if len(text) > 60 else "")
    return "Untitled"


def create(title: str = "") -> Dict[str, Any]:
    """Create a new thread. Returns the thread dict."""
    ensure_dirs()
    thread = {
        "id": f"thr_{uuid.uuid4().hex}",
        "title": title or "New thread",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
    }
    path = THREADS_DIR / f"{thread['id']}.json"
    path.write_text(json.dumps(thread, indent=2))
    return thread


def delete(thread_id: str) -> bool:
    """Delete a thread. Returns True if deleted."""
    path = THREADS_DIR / f"{thread_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def get(thread_id: str) -> Optional[Dict[str, Any]]:
    """Get a thread by ID."""
    path = THREADS_DIR / f"{thread_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _save_thread(thread: Dict[str, Any]) -> None:
    """Persist a thread dict atomically."""
    ensure_dirs()
    path = THREADS_DIR / f"{thread['id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(thread, indent=2))
    tmp.replace(path)


def ensure_thread(thread_id: str, title: str = "") -> Dict[str, Any]:
    """Get an existing thread or create it on first use."""
    t = get(thread_id)
    if t:
        return t
    return create(title=title or thread_id)


def add_message(thread_id: str, role: str, content: str) -> Optional[Dict[str, Any]]:
    """Append a message to a thread and auto-title from first user message."""
    if not content:
        return None
    t = ensure_thread(thread_id)
    t["messages"].append({
        "role": role,
        "content": content,
        "timestamp": _now(),
    })
    # Auto-title on first user message if still default
    if role == "user" and (t["title"] == "New thread" or t["title"] == thread_id):
        t["title"] = _first_user_message(t["messages"])
    t["updated_at"] = _now()
    _save_thread(t)
    return t


def list_all() -> List[Dict[str, Any]]:
    """List all threads, sorted by updated_at descending."""
    ensure_dirs()
    threads: List[Dict[str, Any]] = []
    for f in THREADS_DIR.glob("*.json"):
        try:
            t = json.loads(f.read_text())
            threads.append(t)
        except (json.JSONDecodeError, OSError):
            pass
    threads.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
    return threads


def count() -> int:
    """Count threads."""
    return len(list(THREADS_DIR.glob("*.json")))


def search(query: str) -> List[Dict[str, Any]]:
    """Full-text search across all thread messages."""
    ql = query.lower()
    results: List[Dict[str, Any]] = []
    for f in THREADS_DIR.glob("*.json"):
        try:
            t = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        matches = []
        for msg in t.get("messages", []):
            if ql in msg.get("content", "").lower():
                matches.append(msg)
        if matches:
            results.append({"thread": t, "matches": matches})
    return results


def export_markdown(thread_id: str, path: str) -> int:
    """Export a thread to Markdown. Returns message count."""
    t = get(thread_id)
    if not t:
        return 0
    with open(path, "w") as f:
        f.write(f"# {t['title']}\n\n")
        f.write(f"*ID: `{t['id']}` | Created: {t['created_at']} | Updated: {t['updated_at']}*\n\n")
        f.write("---\n\n")
        for msg in t.get("messages", []):
            role = msg["role"]
            content = msg["content"]
            ts = msg.get("timestamp", "")
            if role == "user":
                f.write(f"## User\n\n{content}\n\n")
            elif role == "assistant":
                f.write(f"## Aishe\n\n{content}\n\n")
            else:
                f.write(f"## {role.capitalize()}\n\n{content}\n\n")
            if ts:
                f.write(f"*{ts}*\n\n")
    return len(t.get("messages", []))
