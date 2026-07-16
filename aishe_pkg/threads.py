"""Thread management — persistent conversation threads as JSON."""

from __future__ import annotations

import json
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


def create(title: str = "CLI Chat") -> Dict[str, Any]:
    """Create a new thread. Returns the thread dict."""
    ensure_dirs()
    thread = {
        "id": f"thr_{uuid.uuid4().hex}",
        "title": title,
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
        f.write(f"*ID: {t['id']} | Created: {t['created_at']}*\n\n")
        f.write("---\n\n")
        for msg in t.get("messages", []):
            role = msg["role"]
            content = msg["content"]
            if role == "user":
                f.write(f"**You:** {content}\n\n")
            else:
                f.write(f"**Aishe:** {content}\n\n")
    return len(t.get("messages", []))


# ─── CLI handlers ───────────────────────────────────────────────────────────

def cmd_threads(args: Any) -> None:
    if args.new:
        t = create()
        print(f"{green('Created')} {t['id']}")
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
        print(dim(f"ID: {t['id']}  Created: {t['created_at']}"))
        print("─" * 50)
        for msg in t["messages"]:
            role = msg["role"]
            content = msg["content"]
            if role == "user":
                print(cyan(f"You: {content}"))
            else:
                print(f"Aishe: {content}")
            print()
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
        print(f"  {t['id']:40s} {title:20s} {dim(f'{n_msgs} msgs')}")
