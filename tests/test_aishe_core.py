"""
Tests for aishe_core — the shared path resolver.

These exist because the alternative to a tested resolver is the bug that
prompted it: three surfaces each hardcoded their own idea of where the backend
lived, and the copies quietly diverged.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aishe_pkg import aishe_core as core  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name}{' — ' + detail if detail and not cond else ''}")


# ── Resolution ───────────────────────────────────────────────────────────────

check("deepagent dir contains server.py", (core.deepagent_dir() / "server.py").exists(),
      f"looked in {core.deepagent_dir()}")
check("deepagent venv exists", core.deepagent_venv().exists(), str(core.deepagent_venv()))

_app_dir = core.desktop_dir()
check("desktop dir has main.js", _app_dir is not None and (_app_dir / "main.js").exists(),
      str(_app_dir))
check("desktop launcher resolved", core.desktop_launcher() is not None, "run.sh or electron missing")
check("telegram bot exists", core.telegram_bot().exists(), str(core.telegram_bot()))

# ── Service registry ─────────────────────────────────────────────────────────

ports = core.ports()
for expected in ("deepagent", "ollama", "qdrant"):
    check(f"registry has {expected}", expected in ports)

check("every service carries a health endpoint",
      all(isinstance(s.get("health"), str) for s in core.SERVICES.values()))
check("service_port resolves", core.service_port("deepagent") == 8765)
check("service_port rejects unknown", core.service_port("nope") is None)

# ── AISHE_HOME override relocates the whole tree ─────────────────────────────

with tempfile.TemporaryDirectory() as tmp:
    alt = Path(tmp) / "aishe-home"
    (alt / "deepagent").mkdir(parents=True)
    (alt / "deepagent" / "server.py").write_text("# stub\n")

    old = os.environ.get("AISHE_HOME")
    os.environ["AISHE_HOME"] = str(alt)
    try:
        check("AISHE_HOME relocates deepagent", core.deepagent_dir() == alt / "deepagent",
              str(core.deepagent_dir()))
    finally:
        os.environ.pop("AISHE_HOME", None)
        if old:
            os.environ["AISHE_HOME"] = old

# ── describe() reports rather than crashes ───────────────────────────────────

d = core.describe()
check("describe() returns a dict", isinstance(d, dict))
check("describe() reports deepagent", d["deepagent"]["server_exists"] is True)
check("describe() reports services", "deepagent" in d["services"])

# ── Report ───────────────────────────────────────────────────────────────────

print()
for p in PASS:
    print(f"  ✔ {p}")
for f in FAIL:
    print(f"  ✖ {f}")
print(f"\n  {len(PASS)} passed, {len(FAIL)} failed\n")
sys.exit(1 if FAIL else 0)
