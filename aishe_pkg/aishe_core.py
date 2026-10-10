"""
aishe_core — the single foundation for every Aishe surface.

CLI, desktop and Telegram all need the same four things, and until now each one
discovered them its own way:

    * where the deepagent backend lives
    * where the desktop app lives
    * where the telegram sidecar lives
    * which services exist, on which ports

That duplication is what let the copies drift: the desktop resolved deepagent
from `~/Documents/projects/aishe-cli/`, `start-all.sh` hardcoded
`~/.local/share/aishe-cli/`, and the CLI's telegram/deepagent lookups probed
paths that no longer exist on this machine. Two installs of the backend were
therefore reachable, and which one won depended on whichever tool started first.

This module is the one resolver. Every surface calls these functions instead of
hardcoding a path.

Resolution order is deliberately: explicit env override, then the repo clone
(so an editable checkout is authoritative), then the installed copy under
`~/.local/share/aishe-cli/`. `AISHE_HOME` relocates everything at once, which is
what makes tests and a second machine trivial.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

__all__ = [
    "home",
    "repo_root",
    "deepagent_dir",
    "deepagent_venv",
    "deepagent_server",
    "desktop_dir",
    "desktop_launcher",
    "sidecars_dir",
    "telegram_bot",
    "SERVICES",
    "ports",
    "service_port",
    "describe",
]

# ─── Roots ────────────────────────────────────────────────────────────────────


def home() -> Path:
    """The Aishe install root.

    Everything hangs off here. `AISHE_HOME` wins so a checkout, a container or a
    test can relocate the whole tree without editing code.
    """
    return Path(os.environ.get("AISHE_HOME") or Path.home() / ".local" / "share" / "aishe-cli")


def repo_root() -> Optional[Path]:
    """The source checkout, if this module is running from one.

    `aishe_core` lives at `<repo>/aishe_pkg/aishe_core.py`, so the repo is two
    levels up. When Aishe is installed under `~/.local/share` instead, this
    returns None and callers fall back to `home()`.
    """
    here = Path(__file__).resolve()
    # <repo>/aishe_pkg/aishe_core.py -> <repo>
    repo = here.parent.parent
    if (repo / "aishe_pkg").is_dir() and repo != home():
        return repo
    return None


def _candidates(rel: str) -> List[Path]:
    """Every place `rel` might live, most authoritative first.

    Repo clone first: a checkout you are actively editing must win over a stale
    installed copy, otherwise edits appear to do nothing.
    """
    rel = rel.strip("/")
    roots: List[Path] = []

    override = os.environ.get("AISHE_HOME")
    if override:
        roots.append(Path(override))
    else:
        repo = repo_root()
        if repo is not None:
            roots.append(repo)
        roots.append(home())
        # The app was historically launched from ~/Documents/projects; keep it as
        # a last resort so an existing setup does not silently break.
        roots.append(Path.home() / "Documents" / "projects" / "aishe-cli")

    out: List[Path] = []
    for r in roots:
        p = r / rel
        if p not in out:
            out.append(p)
    return out


def _find(rel: str, required_file: Optional[str] = None) -> Optional[Path]:
    """First existing location for `rel`, optionally requiring `required_file`."""
    for p in _candidates(rel):
        if not p.exists():
            continue
        if required_file and not (p / required_file).exists():
            continue
        return p
    return None


# ─── deepagent backend ────────────────────────────────────────────────────────


def deepagent_dir() -> Path:
    """Directory holding deepagent's `server.py`.

    Prefers a location that actually contains `server.py`, so a half-populated
    install can't win over a working one.
    """
    return (
        _find("deepagent", "server.py")
        # Nothing found — return the most authoritative candidate so error
        # messages point somewhere sensible.
        or _candidates("deepagent")[0]
    )


def deepagent_venv() -> Path:
    """deepagent's own interpreter. Not the system python: deepagent pins its
    own dependencies and must not be run with whatever python happens to be on
    PATH."""
    return deepagent_dir() / ".venv" / "bin" / "python"


def deepagent_server() -> Path:
    return deepagent_dir() / "server.py"


# ─── desktop app ──────────────────────────────────────────────────────────────


def desktop_dir() -> Optional[Path]:
    """The Electron app directory, or None if it is not installed.

    The app lives in its own project (`aishe-desktop`), not inside the CLI, so
    this is a sibling lookup rather than a subdirectory one.
    """
    found = _find("aishe-desktop", "main.js")
    if found:
        return found

    # Also check for a sibling project next to the repo.
    repo = repo_root()
    if repo is not None:
        sib = repo.parent / "aishe-desktop"
        if (sib / "main.js").exists():
            return sib
    return None


def desktop_launcher() -> Optional[Path]:
    """`run.sh` if present, else the electron binary, else None.

    run.sh is preferred: it recovers WAYLAND_DISPLAY/XDG_RUNTIME_DIR from the
    user session and applies software-rendering flags, without which launching
    from a detached shell or a systemd unit dies with "Failed to connect to
    Wayland display".
    """
    app = desktop_dir()
    if app is None:
        return None
    run = app / "run.sh"
    if run.exists() and os.access(run, os.X_OK):
        return run
    electron = app / "node_modules" / ".bin" / "electron"
    return electron if electron.exists() else None


# ─── sidecars ─────────────────────────────────────────────────────────────────


def sidecars_dir() -> Path:
    return _find("sidecars") or _candidates("sidecars")[0]


def telegram_bot() -> Path:
    return sidecars_dir() / "telegram_bot.py"


# ─── services ─────────────────────────────────────────────────────────────────

# The one authoritative list. The desktop reads it over IPC rather than keeping
# its own copy, which is how it used to end up managing a different set of
# services than `aishe doctor` reported.
SERVICES: Dict[str, Dict[str, object]] = {
    "deepagent": {"port": 8765, "label": "DeepAgent", "health": "/health"},
    "ollama": {"port": 11434, "label": "Ollama", "health": "/api/tags"},
    "qdrant": {"port": 6333, "label": "Qdrant", "health": "/readyz"},
    # Voice is Sarvam cloud — no local port to probe.
}


def ports() -> Dict[str, int]:
    return {name: int(svc["port"]) for name, svc in SERVICES.items()}  # type: ignore[index]


def service_port(name: str) -> Optional[int]:
    svc = SERVICES.get(name)
    return int(svc["port"]) if svc else None  # type: ignore[index]


def describe() -> Dict[str, object]:
    """Everything resolved, for `aishe doctor` and for debugging.

    Returns existence flags rather than raising, so a diagnostics screen can
    report "missing" instead of crashing.
    """
    d = deepagent_dir()
    app = desktop_dir()
    return {
        "aishe_home": str(home()),
        "repo_root": str(repo_root()) if repo_root() else None,
        "deepagent": {
            "dir": str(d),
            "exists": d.exists(),
            "venv": str(deepagent_venv()),
            "venv_exists": deepagent_venv().exists(),
            "server": str(deepagent_server()),
            "server_exists": deepagent_server().exists(),
        },
        "desktop": {
            "dir": str(app) if app else None,
            "exists": bool(app),
            "launcher": str(desktop_launcher()) if desktop_launcher() else None,
        },
        "sidecars": {
            "dir": str(sidecars_dir()),
            "telegram_bot": str(telegram_bot()),
            "telegram_bot_exists": telegram_bot().exists(),
        },
        "services": SERVICES,
    }
