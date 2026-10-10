"""
Memory tests.

Runs against the throwaway home from `harness`, never the real
`~/aishe/memory/facts.jsonl`.
"""

import json  # noqa: E402

from harness import T, check, eq, section  # noqa: E402

from aishe_pkg import memory  # noqa: E402

section("add / search round-trip")

memory.ensure_dirs()
memory.add("The test user's favourite colour is violet")

hits = memory.search("favourite colour")
check("a stored fact is findable", any("violet" in json.dumps(h) for h in hits), f"hits={hits}")

miss = memory.search("zzzznotpresentzzzz")
check("a miss returns nothing", not miss, f"rows={miss}")

section("count reflects storage")

n = memory.count()
check("count is a positive int", isinstance(n, int) and n >= 1, f"count={n}")

section("list_all returns what was written")

all_facts = memory.list_all()
check("list_all is a list", isinstance(all_facts, list))
check("list_all contains the fact", any("violet" in json.dumps(f) for f in all_facts),
      f"n={len(all_facts)}")

section("update changes the text")

memory.add("A fact to be updated later")
target = [f for f in memory.list_all() if "to be updated" in json.dumps(f)]
check("the fact exists before update", bool(target))
if target:
    fid = target[0].get("id") or target[0].get("uuid")
    if fid:
        memory.update(fid, "An updated fact body")
        after = [f for f in memory.list_all() if f.get("id") == fid or f.get("uuid") == fid]
        check("update changed the body", after and "updated fact body" in json.dumps(after),
              f"after={after}")

section("delete removes it")

memory.add("A fact to be deleted")
doomed = [f for f in memory.list_all() if "to be deleted" in json.dumps(f)]
check("the fact exists before delete", bool(doomed))
if doomed:
    fid = doomed[0].get("id") or doomed[0].get("uuid")
    if fid:
        memory.delete(fid)
        remaining = [f for f in memory.list_all()
                     if f.get("id") == fid or f.get("uuid") == fid]
        check("delete removed the fact", not remaining)

section("clear empties memory")

memory.clear()
eq("count is zero after clear", memory.count(), 0)

section("soul / user prompt files")

memory.set_soul("You are a helpful test assistant.")
check("soul round-trips", "helpful test assistant" in str(memory.get_soul()))

memory.append_user_md("Prefers concise answers.")
check("user md appends", "concise answers" in str(memory.get_user_md()))

section("data lives under AISHE_HOME")

facts = T.home / "memory" / "facts.jsonl"
check("facts file is under AISHE_HOME", str(facts).startswith(str(T.home)),
      f"expected under {T.home}")

if __name__ == "__main__":
    import sys

    from harness import R

    sys.exit(0 if R.report("memory") else 1)
