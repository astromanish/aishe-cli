"""Memory management — persistent facts stored as JSONL."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get
from .util import bold, cyan, dim, green, red

DATA_DIR = Path(get("data.dir", str(Path.home() / ".local" / "share" / "aishe")))
MEMORY_DIR = DATA_DIR / "memory"
MEMORY_FILE = MEMORY_DIR / "facts.jsonl"


def ensure_dirs() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now().isoformat()


def add(fact: str) -> str:
    """Add a fact to memory. Returns the entry ID."""
    ensure_dirs()
    entry = {
        "id": f"mem_{uuid.uuid4().hex}",
        "fact": fact,
        "timestamp": _now(),
    }
    with open(MEMORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry["id"]


def search(query: str) -> List[Dict[str, Any]]:
    """Search memories by substring match."""
    if not MEMORY_FILE.exists():
        return []
    ql = query.lower()
    results: List[Dict[str, Any]] = []
    for line in MEMORY_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ql in entry.get("fact", "").lower():
            results.append(entry)
    return results


def list_all() -> List[Dict[str, Any]]:
    """List all memories."""
    if not MEMORY_FILE.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for line in MEMORY_FILE.read_text().splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def clear() -> int:
    """Delete all memories. Returns count of deleted entries."""
    if not MEMORY_FILE.exists():
        return 0
    count = sum(1 for l in MEMORY_FILE.read_text().splitlines() if l.strip())
    MEMORY_FILE.unlink()
    return count


def count() -> int:
    """Count memory entries."""
    if not MEMORY_FILE.exists():
        return 0
    return sum(1 for l in MEMORY_FILE.read_text().splitlines() if l.strip())


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
            w.writerow([e["id"], e["fact"], e["timestamp"]])
    return len(entries)


def export_markdown(path: str) -> int:
    """Export all memories to Markdown. Returns count."""
    entries = list_all()
    if not entries:
        return 0
    with open(path, "w") as f:
        f.write("# Aishe Memory Export\n\n")
        for e in entries:
            f.write(f"- **{e['id']}** ({e['timestamp']}): {e['fact']}\n")
    return len(entries)


# ─── CLI handlers ───────────────────────────────────────────────────────────

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
        results = search(query)
        if results:
            print(bold(f"Found {len(results)} match(es):"))
            for r in results:
                print(f"  {cyan(r['id'])}  {r['fact']}")
                print(f"  {dim(r['timestamp'])}")
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
