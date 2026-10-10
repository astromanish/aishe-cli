"""
Thread / persistence tests.

BUG-009 lived here: `add_message` looked for `<tid>.json` while `create()`
always generated a fresh `thr_{uuid}`, so every turn landed in its own
one-message thread and history was never actually preserved. The fix was the
single shared `main` thread.

These tests run against the throwaway home in `harness`, never the real
`~/aishe/aishe.db`.
"""

from harness import T, check, eq, section  # noqa: E402

from aishe_pkg import threads  # noqa: E402

section("one shared thread")

check("resolve_thread_id exists", hasattr(threads, "resolve_thread_id"))
tid = threads.resolve_thread_id()
eq("resolve returns 'main'", tid, "main")
eq("resolve is stable across calls", threads.resolve_thread_id(), tid)

section("add_message persists under the shared thread")

threads.ensure_dirs()
threads.add_message("main", "user", "what is 2+2?")
threads.add_message("main", "assistant", "4")

rows = threads.search_sessions("2+2", limit=10)
check("user turn is searchable", any("2+2" in (r.get("content") or "") for r in rows),
      f"rows={rows}")

all_rows = threads.search_sessions("4", limit=10)
check("assistant turn is searchable", any((r.get("content") or "").strip() == "4" for r in all_rows),
      f"rows={all_rows}")

section("no thread fragmentation (BUG-009)")

# The original bug produced one thread file per turn. Assert the opposite: a
# user turn and the assistant reply to it must share a thread id.
import sqlite3  # noqa: E402

conn = sqlite3.connect(threads.DB_PATH)
thread_ids = [r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM messages")]
conn.close()
eq("every message is in one thread", thread_ids, ["main"])

section("count reflects the shared-thread design")

# count() counts THREADS, not messages. Under the single-thread design that is
# always 1 — which is exactly the regression guard for BUG-009: if thread
# fragmentation ever came back, this would climb above 1.
n = threads.count()
eq("count() counts threads, and there is exactly one", n, 1)

section("delete removes a thread's messages")

threads.add_message("scratch", "user", "temporary note")
threads.delete("scratch")
rows = threads.search_sessions("temporary note", limit=10)
check("deleted thread's messages are gone", not rows, f"rows={rows}")
check("deleting one thread leaves 'main' intact", threads.search_sessions("2+2", limit=1))

section("FTS search matches on content, not just exact strings")

threads.add_message("main", "user", "the quick brown fox jumps")
hits = threads.search_sessions("brown", limit=10)
check("substring search works", any("brown" in (h.get("content") or "") for h in hits),
      f"hits={hits}")

miss = threads.search_sessions("zzzznotpresentzzzz", limit=10)
check("a miss returns no rows", not miss, f"rows={miss}")

section("sqlite-special characters do not break search (FTS escaping)")

threads.add_message("main", "user", 'quotes " and (parens) and :colons')
check("quotes are searchable", threads.search_sessions("quotes", limit=5))
check("FTS metacharacters do not raise", isinstance(threads.search_sessions('"(', limit=5), list))

section("db lives under the configured data dir")

check("DB_PATH is under AISHE_HOME", str(T.home) in str(threads.DB_PATH),
      f"DB_PATH={threads.DB_PATH}")

section("everything written survives")

conn = sqlite3.connect(threads.DB_PATH)
msg_rows = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
conn.close()
check("all messages persisted", msg_rows >= 4, f"messages={msg_rows}")
check("still exactly one thread", threads.count() == 1)

if __name__ == "__main__":
    import sys

    from harness import R

    sys.exit(0 if R.report("threads") else 1)
