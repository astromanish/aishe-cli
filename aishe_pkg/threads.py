"""Thread management — persistent conversation threads in SQLite with FTS5.

Aug 2026: migrated from per-thread JSON files to a single SQLite database
(`~/aishe/aishe.db`) with full-text search (messages_fts) across ALL sessions.

On first init, existing JSON thread files are imported automatically
(originals are kept as a backup, never deleted).
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get

DATA_DIR = Path(get("data.dir", str(Path.home() / "aishe")))
THREADS_DIR = DATA_DIR / "threads"
DB_PATH = DATA_DIR / "aishe.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New thread',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    thread_id UNINDEXED, role UNINDEXED, content
);
"""


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db() -> None:
    """Create schema (idempotent) and migrate legacy JSON threads once."""
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        _migrate_json_threads(conn)
    finally:
        conn.close()


def _migrate_json_threads(conn: sqlite3.Connection) -> None:
    """Import legacy per-thread JSON files into SQLite (one-time)."""
    if not THREADS_DIR.exists():
        return
    row = conn.execute("SELECT COUNT(*) AS c FROM threads").fetchone()
    if row and row["c"] > 0:
        return  # already migrated

    imported = 0
    for f in sorted(THREADS_DIR.glob("*.json")):
        try:
            t = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        tid = t.get("id") or f.stem
        title = t.get("title") or "New thread"
        created_at = t.get("created_at") or datetime.now().isoformat()
        updated_at = t.get("updated_at") or created_at
        conn.execute(
            "INSERT OR IGNORE INTO threads (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (tid, title, created_at, updated_at),
        )
        for msg in t.get("messages", []):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if not content:
                continue
            ts = msg.get("timestamp") or updated_at
            cur = conn.execute(
                "INSERT INTO messages (thread_id, role, content, timestamp) VALUES (?,?,?,?)",
                (tid, role, content, ts),
            )
            _fts_insert(conn, cur.lastrowid, tid, role, content)
        imported += 1
    conn.commit()
    if imported:
        print(f"Migrated {imported} JSON thread(s) into {DB_PATH.name} (originals kept)")


def _fts_insert(conn: sqlite3.Connection, rowid: int, thread_id: str, role: str, content: str) -> None:
    try:
        conn.execute(
            "INSERT INTO messages_fts (rowid, thread_id, role, content) VALUES (?,?,?,?)",
            (rowid, thread_id, role, content),
        )
    except sqlite3.OperationalError:
        pass  # FTS5 unavailable — LIKE fallback still works


def _now() -> str:
    return datetime.now().isoformat()


def _first_user_message(messages: List[Dict[str, Any]]) -> str:
    """Return the first user message content, truncated for a title."""
    for msg in messages:
        if msg.get("role") == "user":
            text = msg.get("content", "").strip().replace("\n", " ")
            return text[:60] + ("..." if len(text) > 60 else "")
    return "Untitled"


def _escape_fts(query: str) -> str:
    """Build a safe FTS5 MATCH expression from a plain-text query."""
    tokens = re.findall(r'"[^"]+"|\S+', query)
    cleaned = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        if t.startswith('"') and t.endswith('"'):
            t = t[1:-1]
        cleaned.append('"' + t.replace('"', '""') + '"')
    return " AND ".join(cleaned)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def create(title: str = "") -> Dict[str, Any]:
    """Create a new thread. Returns the thread dict."""
    _init_db()
    thread = {
        "id": f"thr_{uuid.uuid4().hex}",
        "title": title or "New thread",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
    }
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO threads (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (thread["id"], thread["title"], thread["created_at"], thread["updated_at"]),
        )
        conn.commit()
    finally:
        conn.close()
    return thread


def delete(thread_id: str) -> bool:
    """Delete a thread. Returns True if deleted."""
    _init_db()
    conn = _connect()
    try:
        cur = conn.execute("SELECT id FROM threads WHERE id = ?", (thread_id,))
        if not cur.fetchone():
            return False
        conn.execute(
            "DELETE FROM messages_fts WHERE rowid IN (SELECT id FROM messages WHERE thread_id = ?)",
            (thread_id,),
        )
        conn.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
        conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def get(thread_id: str) -> Optional[Dict[str, Any]]:
    """Get a thread by ID (with messages)."""
    _init_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if not row:
            return None
        msgs = conn.execute(
            "SELECT role, content, timestamp FROM messages WHERE thread_id = ? ORDER BY id",
            (thread_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "messages": [dict(m) for m in msgs],
        }
    finally:
        conn.close()


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
    _init_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if not row:
            title = "" if role == "assistant" else content
            conn.execute(
                "INSERT INTO threads (id, title, created_at, updated_at) VALUES (?,?,?,?)",
                (thread_id, title[:60] if title else "New thread", _now(), _now()),
            )
            row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()

        title = row["title"]
        # Auto-title on first user message if still default
        if role == "user" and (title == "New thread" or title == thread_id):
            cnt = conn.execute(
                "SELECT content FROM messages WHERE thread_id = ? AND role='user' ORDER BY id LIMIT 1",
                (thread_id,),
            ).fetchone()
            first = cnt["content"] if cnt else content
            title = _first_user_message([{"role": "user", "content": first}])
            conn.execute("UPDATE threads SET title = ? WHERE id = ?", (title, thread_id))

        now = _now()
        cur = conn.execute(
            "INSERT INTO messages (thread_id, role, content, timestamp) VALUES (?,?,?,?)",
            (thread_id, role, content, now),
        )
        _fts_insert(conn, cur.lastrowid, thread_id, role, content)
        conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, thread_id))
        conn.commit()
        return get(thread_id)
    finally:
        conn.close()


def list_all() -> List[Dict[str, Any]]:
    """List all threads (with message count), sorted by updated_at descending."""
    _init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM messages m WHERE m.thread_id = t.id) AS message_count "
            "FROM threads t ORDER BY t.updated_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["messages"] = []
            out.append(d)
        return out
    finally:
        conn.close()


def count() -> int:
    """Count threads."""
    _init_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM threads").fetchone()
        return row["c"] if row else 0
    finally:
        conn.close()


def search(query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Full-text search across all thread messages (FTS5; LIKE fallback)."""
    _init_db()
    conn = _connect()
    try:
        rows = []
        try:
            q = _escape_fts(query)
            if q:
                rows = conn.execute(
                    "SELECT m.id, m.thread_id, m.role, m.content, m.timestamp, t.title "
                    "FROM messages_fts "
                    "JOIN messages m ON m.id = messages_fts.rowid "
                    "JOIN threads t ON t.id = m.thread_id "
                    "WHERE messages_fts MATCH ? ORDER BY m.id DESC LIMIT ?",
                    (q, limit),
                ).fetchall()
        except sqlite3.OperationalError:
            pass
        if not rows:
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT m.id, m.thread_id, m.role, m.content, m.timestamp, t.title "
                "FROM messages m JOIN threads t ON t.id = m.thread_id "
                "WHERE m.content LIKE ? ORDER BY m.id DESC LIMIT ?",
                (like, limit),
            ).fetchall()

        # Group matches by thread, mirroring the old JSON API shape.
        threads: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            tid = r["thread_id"]
            if tid not in threads:
                threads[tid] = {
                    "thread": {"id": tid, "title": r["title"], "updated_at": r["timestamp"]},
                    "matches": [],
                }
            threads[tid]["matches"].append(
                {"role": r["role"], "content": r["content"], "timestamp": r["timestamp"]}
            )
        return list(threads.values())
    finally:
        conn.close()


def search_sessions(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search past sessions for a fact/topic. Returns flat list of message hits."""
    _init_db()
    conn = _connect()
    try:
        rows = []
        try:
            q = _escape_fts(query)
            if q:
                rows = conn.execute(
                    "SELECT m.id, m.thread_id, m.role, m.content, m.timestamp, t.title "
                    "FROM messages_fts "
                    "JOIN messages m ON m.id = messages_fts.rowid "
                    "JOIN threads t ON t.id = m.thread_id "
                    "WHERE messages_fts MATCH ? ORDER BY m.id DESC LIMIT ?",
                    (q, limit),
                ).fetchall()
        except sqlite3.OperationalError:
            pass
        if not rows:
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT m.id, m.thread_id, m.role, m.content, m.timestamp, t.title "
                "FROM messages m JOIN threads t ON t.id = m.thread_id "
                "WHERE m.content LIKE ? ORDER BY m.id DESC LIMIT ?",
                (like, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


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
            role = msg.get("role", "user")
            content = msg.get("content", "")
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
