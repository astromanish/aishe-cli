"""Thread management — persistent conversation threads as JSON."""

from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get
from .util import bold, cyan, dim, green, red

DATA_DIR = Path(get("data.dir", str(Path.home() / ".local" / "share" / "aishe")))
THREADS_DIR = DATA_DIR / "threads"


def ensure_dirs() -> None:
    THREADS_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now().isoformat()


def _slugify(title: str) -> str:
    """Make a filesystem-safe slug from a title."""
    s = title.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s)
    return s[:40].strip("-")


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


# ─── CLI handlers ───────────────────────────────────────────────────────────

def cmd_threads(args: Any) -> None:
    if args.new:
        t = create()
        print(f"{green('Created')} {cyan(t['id'])}")
        return

    if args.delete:
        ok = delete(args.delete)
        if ok:
            print(f"{red('Deleted')} {args.delete}")
        else:
            print(red(f"Thread {args.delete} not found"))
        return

    if args.show:
        t = get(args.show)
        if not t:
            print(red(f"Thread {args.show} not found"))
            sys.exit(1)
        print(bold(f"Thread: {t['title']}"))
        print(dim(f"ID: {t['id']}  Created: {t['created_at']}  Updated: {t['updated_at']}"))
        print("─" * 50)
        for msg in t["messages"]:
            role = msg["role"]
            content = msg["content"]
            ts = msg.get("timestamp", "")
            if role == "user":
                print(cyan(f"\n> {content}"))
            else:
                print(f"\n{content}")
            if ts:
                print(dim(f"  — {ts}"))
        print()
        return

    if args.rename:
        t = get(args.rename)
        if not t:
            print(red(f"Thread {args.rename} not found"))
            sys.exit(1)
        new_title = " ".join(args.title) if isinstance(getattr(args, "title", None), list) else getattr(args, "title", "")
        if not new_title:
            print(red("Usage: aishe threads --rename <id> --title 'New title'"))
            sys.exit(1)
        t["title"] = new_title
        t["updated_at"] = _now()
        _save_thread(t)
        print(f"{green('Renamed')} {args.rename} → {new_title}")
        return

    # List all
    threads = list_all()
    if not threads:
        print(dim("No threads yet. Create one with: aishe threads --new"))
        return

    print(bold(f"Threads ({len(threads)})"))
    print("─" * 60)
    for t in threads:
        n_msgs = len(t.get("messages", []))
        title = t.get("title", "untitled")
        updated = t.get("updated_at", "")
        short_updated = updated[:19] if updated else ""
        print(f"  {cyan(t['id'][:20]):24s} {title:24s} {dim(f'{n_msgs} msgs')} {dim(short_updated)}")
