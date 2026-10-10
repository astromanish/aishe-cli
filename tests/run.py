#!/usr/bin/env python3
"""
Run every Aishe test suite.

    python tests/run.py          # everything
    python tests/run.py config   # just one suite

Suites live in tests/test_*.py and each is runnable on its own; this runner just
executes them as subprocesses so a crash in one cannot take the others down, and
reports a single pass/fail across the whole project.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = [
    "test_aishe_core.py",
    "test_config.py",
    "test_threads.py",
    "test_memory.py",
]


def main() -> int:
    wanted = sys.argv[1:] or SUITES
    unknown = [s for s in wanted if s not in SUITES]
    if unknown:
        print(f"Unknown suite(s): {', '.join(unknown)}")
        print(f"Available: {', '.join(SUITES)}")
        return 2

    results = {}
    for suite in wanted:
        print(f"\n\033[1m▶ {suite}\033[0m")
        proc = subprocess.run(
            [sys.executable, str(HERE / suite)],
            cwd=str(HERE),  # so `from harness import …` resolves
        )
        results[suite] = proc.returncode == 0

    print("\n" + "═" * 60)
    failed = [s for s, ok in results.items() if not ok]
    for s, ok in results.items():
        mark = "\033[32m✔\033[0m" if ok else "\033[31m✖\033[0m"
        print(f"  {mark} {s}")
    print("═" * 60)

    if failed:
        print(f"\033[31m{len(failed)} suite(s) failed\033[0m")
        return 1
    print(f"\033[32mAll {len(results)} suite(s) passed\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
