"""
Shared test harness.

Every test runs against a throwaway `AISHE_HOME`, never the real `~/aishe` or
`~/.config/aishe`. Without this, a test that writes a thread or a memory entry
would corrupt real conversation data — the opposite of what a test suite is for.

`sys.path` is set up here once so each test module can just
`from harness import T, check, section`.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# A pristine temp home, created before any aishe module reads config at import
# time. Imported here rather than inside each test so the env var is already set.
_TMP = tempfile.mkdtemp(prefix="aishe-test-")
os.environ["AISHE_HOME"] = _TMP

class TempHome:
    """A pristine throwaway Aishe home. Tests never touch the real `~/aishe`."""

    def __init__(self):
        self.home = Path(tempfile.mkdtemp(prefix="aishe-test-"))
        (self.home / "threads").mkdir(parents=True, exist_ok=True)
        (self.home / "memory").mkdir(parents=True, exist_ok=True)


_TMPHOME = TempHome()

# Set before any aishe module is imported, since config paths are resolved at
# import time.
os.environ["AISHE_HOME"] = str(_TMPHOME.home)

# Force the JSONL memory fallback. Without this, memory.add()/search()/clear()
# talk to the live DeepAgent → mem0 → Qdrant, so a test run would POST junk facts
# into real memory and `clear()` would wipe it.
os.environ["AISHE_MEMORY_OFFLINE"] = "1"
os.environ["XDG_CONFIG_HOME"] = str(_TMPHOME.home / "config")
os.environ["XDG_STATE_HOME"] = str(_TMPHOME.home / "state")

T = _TMPHOME


class Results:
    """Collects pass/fail so every test runs instead of aborting on the first."""

    def __init__(self):
        self.passed = []
        self.failed = []

    def check(self, name, cond, detail=""):
        if cond:
            self.passed.append(name)
        else:
            self.failed.append(f"{name}{' — ' + str(detail) if detail else ''}")
        return bool(cond)

    def eq(self, name, got, want):
        return self.check(name, got == want, f"got {got!r}, want {want!r}")

    def raises(self, name, fn, exc=Exception):
        try:
            fn()
        except exc:
            return self.check(name, True)
        return self.check(name, False, f"did not raise {exc.__name__}")

    def report(self, label):
        print()
        for p in self.passed:
            print(f"  ✔ {p}")
        for f in self.failed:
            print(f"  ✖ {f}")
        status = "PASS" if not self.failed else "FAIL"
        print(f"\n  {label}: {len(self.passed)} passed, {len(self.failed)} failed  [{status}]\n")
        return not self.failed


R = Results()
check = R.check
eq = R.eq
raises = R.raises


def section(title):
    print(f"\n  ── {title} " + "─" * max(0, 56 - len(title)))
