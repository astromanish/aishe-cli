"""Pet attach — wrap the pet in a tmux pane or separate terminal window.

Three surfaces:
  - tmux split pane (preferred) — `aishe pet attach`
  - separate Terminal.app / iTerm2 window (fallback) — `aishe pet attach --window`
  - persistent tmux.conf hook — `aishe pet watch install`

The pet itself doesn't know about any of this — we just spawn a child
process that runs `aishe pet` (foreground animation) in the right pane
or window, and the existing signal channel keeps it in sync.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

from .util import bold, cyan, dim, green, red, yellow, status_dot


# ─── Capability detection ──────────────────────────────────────────────────

def _has_tmux() -> bool:
    return shutil.which("tmux") is not None


def _in_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def _tmux_socket() -> str:
    return os.environ.get("TMUX", "")


def _tmux_session() -> Optional[str]:
    """Get the current tmux session name (only when called from inside tmux)."""
    if not _in_tmux():
        return None
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "#{session_name}"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _mac_terminal_app() -> Optional[str]:
    """Detect macOS terminal app. Prefer iTerm2 if available."""
    if platform.system() != "Darwin":
        return None
    if shutil.which("osascript"):
        # Check for iTerm2
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of every process'],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0 and "iTerm" in r.stdout:
                return "iterm"
        except Exception:
            pass
        return "terminal"  # default to Terminal.app
    return None


# ─── Tmux attach ────────────────────────────────────────────────────────────

PET_PANE_TITLE = "◉ aishe pet"  # marker we can find later for detach

def _tmux_new_pane(
    direction: str = "right",  # "right" or "bottom"
    ratio: int = 30,           # % width for the pet pane (if right) or % height (if bottom)
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Tuple[bool, str]:
    """Create a tmux pane running `aishe pet`. Returns (success, pane_id)."""
    if not _in_tmux():
        return False, ""

    # Build the command that will run in the new pane
    cmd_parts = ["aishe", "pet"]
    if width:
        cmd_parts += ["--width", str(width)]
    if height:
        cmd_parts += ["--height", str(height)]
    cmd = " ".join(cmd_parts)

    # tmux split-window flags
    if direction == "bottom":
        split_flag = "-v"   # vertical split = top/bottom
    else:
        split_flag = "-h"   # horizontal split = left/right (default)

    try:
        # Create the split. -d = don't focus, -p = pane size %.
        # Title the pane so we can find it later.
        r = subprocess.run(
            ["tmux", "split-window", split_flag, "-d", "-p", str(ratio),
             "-P", "-F", "#{pane_id}", cmd],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return False, r.stderr.strip()
        pane_id = r.stdout.strip()
        # Set pane title via the @pane_title variable (cosmetic; works in
        # some status-line configs and helps users identify the pane)
        subprocess.run(
            ["tmux", "select-pane", "-t", pane_id, "-T", PET_PANE_TITLE],
            capture_output=True, timeout=2,
        )
        return True, pane_id
    except Exception as e:
        return False, str(e)


def _tmux_find_pet_pane() -> Optional[str]:
    """Find the pet pane by title. Returns pane_id or None."""
    if not _in_tmux():
        return None
    try:
        r = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{pane_id}\t#{pane_title}"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2 and parts[1] == PET_PANE_TITLE:
                    return parts[0]
    except Exception:
        pass
    return None


def _tmux_kill_pane(pane_id: str) -> bool:
    try:
        r = subprocess.run(
            ["tmux", "kill-pane", "-t", pane_id],
            capture_output=True, text=True, timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


# ─── Window fallback (macOS Terminal.app / iTerm2) ──────────────────────────

def _spawn_terminal_window(
    width: int, height: int,
    app: Optional[str] = None,
) -> Tuple[bool, str]:
    """Open a new terminal window running `aishe pet`. Returns (success, msg)."""
    if platform.system() != "Darwin":
        return False, "Window fallback only implemented for macOS. Use tmux."

    app = app or _mac_terminal_app()
    if not app:
        return False, "No macOS terminal app found"

    # Build a command that runs the pet. We use a here-doc to keep the
    # window open if the pet exits cleanly.
    cmd = f"aishe pet --width {width} --height {height}"

    if app == "iterm":
        # Use AppleScript to create a new iTerm2 window
        ascript = f'''
tell application "iTerm"
    create window with default profile
    tell current session of current window
        write text "{cmd}"
    end tell
end tell
'''
        try:
            r = subprocess.run(
                ["osascript", "-e", ascript],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return True, "Opened iTerm2 window"
            return False, f"osascript failed: {r.stderr.strip()}"
        except Exception as e:
            return False, str(e)
    else:
        # Terminal.app
        ascript = f'''
tell application "Terminal"
    do script "{cmd}"
    activate
end tell
'''
        try:
            r = subprocess.run(
                ["osascript", "-e", ascript],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return True, "Opened Terminal.app window"
            return False, f"osascript failed: {r.stderr.strip()}"
        except Exception as e:
            return False, str(e)


# ─── Commands ───────────────────────────────────────────────────────────────

def cmd_pet_attach(args: Any) -> None:
    """`aishe pet attach` — split current tmux window or open a new window."""
    if getattr(args, "no_pane", False) or getattr(args, "window", False):
        # Window fallback path
        ok, msg = _spawn_terminal_window(
            width=args.width or 36,
            height=args.height or 12,
        )
        if ok:
            print(green(msg))
            print(dim("  Pet is now running in a separate window."))
            print(dim("  Close the window or Ctrl+C in it to stop."))
        else:
            print(red(f"Window fallback failed: {msg}"))
            sys.exit(1)
        return

    # tmux path
    if not _has_tmux():
        print(red("tmux not installed."))
        print(dim("  Install: brew install tmux"))
        print(dim("  Or run with --window to open a separate Terminal window"))
        print(dim("  (macOS only)."))
        sys.exit(1)

    if not _in_tmux():
        print(red("Not running inside a tmux session."))
        print(dim("  Options:"))
        print(dim("    • Start tmux:   tmux new -s aishe"))
        print(dim("    • Or:           aishe pet attach --window  (macOS)"))
        sys.exit(1)

    # Check if already attached
    existing = _tmux_find_pet_pane()
    if existing:
        print(yellow(f"Pet pane already attached ({existing})."))
        print(dim("  Use `aishe pet detach` first to reattach elsewhere."))
        sys.exit(0)

    direction = getattr(args, "direction", "right")
    ratio = getattr(args, "ratio", 30) or 30
    if not (5 <= ratio <= 95):
        print(red(f"Ratio must be between 5 and 95 (got {ratio})"))
        sys.exit(1)

    width = getattr(args, "width", None)
    height = getattr(args, "height", None)
    # If neither given, use defaults tuned for the side pane
    if not width and not height:
        width = 36
        height = 12

    ok, info = _tmux_new_pane(
        direction=direction, ratio=ratio,
        width=width, height=height,
    )
    if ok:
        print(green(f"Pet attached: pane {info}"))
        print(dim(f"  direction: {direction}  ·  ratio: {ratio}%"))
        print(dim(f"  frame: {width or 'auto'} × {height or 'auto'}"))
        print(dim("  Detach with: aishe pet detach"))
    else:
        print(red(f"Failed to create pane: {info}"))
        sys.exit(1)


def cmd_pet_detach(args: Any) -> None:
    """`aishe pet detach` — kill the pet pane if one is attached."""
    if not _in_tmux():
        print(red("Not running inside a tmux session."))
        sys.exit(1)

    pane_id = _tmux_find_pet_pane()
    if not pane_id:
        print(yellow("No pet pane found to detach."))
        return

    if _tmux_kill_pane(pane_id):
        print(green(f"Detached pet pane {pane_id}."))
    else:
        print(red(f"Failed to kill pane {pane_id}."))
        sys.exit(1)


# ─── Watch mode (persistent tmux hook) ─────────────────────────────────────

TMUX_CONF_LINE = (
    "# Aishe pet — auto-attach pet pane in every new window.\n"
    "# Remove these lines to disable.\n"
    'set-hook -g after-new-window "split-window -h -d -p 30 \\"aishe pet\\""\n'
    "# End aishe pet hook"
)

def cmd_pet_watch(args: Any) -> None:
    """`aishe pet watch {install|uninstall|status}`."""
    if not _has_tmux():
        print(red("tmux not installed. Watch mode requires tmux."))
        sys.exit(1)

    action = getattr(args, "watch_action", "status")
    conf_path = Path.home() / ".tmux.conf"

    if action == "status":
        if not conf_path.exists():
            print(yellow("No ~/.tmux.conf found."))
            return
        content = conf_path.read_text()
        if "Aishe pet" in content and "split-window" in content and "aishe pet" in content:
            print(green("✓ Aishe pet hook is installed in ~/.tmux.conf"))
            # Show the lines
            for line in content.splitlines():
                if "aishe" in line.lower() or "Aishe" in line:
                    print(dim(f"  {line}"))
        else:
            print(yellow("Aishe pet hook is NOT installed."))
            print(dim("  Run: aishe pet watch install"))
        return

    if action == "install":
        if not conf_path.exists():
            conf_path.touch()
        content = conf_path.read_text()
        if "Aishe pet" in content and "aishe pet" in content:
            print(yellow("Hook already installed in ~/.tmux.conf"))
            return
        # Append the hook (with a newline before to be safe)
        with open(conf_path, "a") as f:
            if content and not content.endswith("\n"):
                f.write("\n")
            f.write("\n" + TMUX_CONF_LINE + "\n")
        print(green("✓ Installed pet hook in ~/.tmux.conf"))
        print(dim("  Reload tmux config: tmux source-file ~/.tmux.conf"))
        print(dim("  Every new tmux window will auto-attach a pet pane."))
        return

    if action == "uninstall":
        if not conf_path.exists():
            print(yellow("No ~/.tmux.conf to clean up."))
            return
        content = conf_path.read_text()
        if "Aishe pet" not in content:
            print(yellow("Hook not installed (no Aishe pet marker found)."))
            return
        # Remove the block (from "# Aishe pet" to "# End aishe pet hook")
        lines = content.splitlines()
        out: List[str] = []
        skip = False
        for line in lines:
            if line.startswith("# Aishe pet"):
                skip = True
                continue
            if skip and line.startswith("# End aishe pet hook"):
                skip = False
                continue
            if not skip:
                out.append(line)
        # Remove trailing blank line if we left one
        while out and not out[-1].strip():
            out.pop()
        conf_path.write_text("\n".join(out) + "\n")
        print(green("✓ Removed pet hook from ~/.tmux.conf"))
        print(dim("  Reload: tmux source-file ~/.tmux.conf"))
        return

    print(red(f"Unknown watch action: {action}"))
    sys.exit(1)
