"""Terminal utilities — colors, formatting, service checks, and aesthetic UI."""

from __future__ import annotations

import itertools
import os
import shutil
import sys
import threading
import time
from typing import Any, Callable, Optional

import requests

from .config import get

# ─── Color helpers ─────────────────────────────────────────────────────────

def _color_enabled() -> bool:
    return get("ui.color", True) and sys.stdout.isatty()


def green(s: str) -> str:
    return f"\033[32m{s}\033[0m" if _color_enabled() else s


def red(s: str) -> str:
    return f"\033[31m{s}\033[0m" if _color_enabled() else s


def yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m" if _color_enabled() else s


def cyan(s: str) -> str:
    return f"\033[36m{s}\033[0m" if _color_enabled() else s


def magenta(s: str) -> str:
    return f"\033[35m{s}\033[0m" if _color_enabled() else s


def bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if _color_enabled() else s


def dim(s: str) -> str:
    return f"\033[2m{s}\033[0m" if _color_enabled() else s


def italic(s: str) -> str:
    return f"\033[3m{s}\033[0m" if _color_enabled() else s


def underline(s: str) -> str:
    return f"\033[4m{s}\033[0m" if _color_enabled() else s


# ─── Aesthetic UI helpers ──────────────────────────────────────────────────

def header(title: str, subtitle: str = "") -> None:
    """Print a beautiful boxed header."""
    width = 50
    print()
    print(f"  {bold('╭' + '─' * (width - 2) + '╮')}")
    # Center the title
    title_pad = (width - 2 - len(title)) // 2
    print(f"  {bold('│')}{' ' * title_pad}{cyan(bold(title))}{' ' * (width - 2 - len(title) - title_pad)}{bold('│')}")
    if subtitle:
        sub_pad = (width - 2 - len(subtitle)) // 2
        print(f"  {bold('│')}{' ' * sub_pad}{dim(subtitle)}{' ' * (width - 2 - len(subtitle) - sub_pad)}{bold('│')}")
    print(f"  {bold('╰' + '─' * (width - 2) + '╯')}")
    print()


def section(title: str) -> None:
    """Print a section heading with a line."""
    print(f"\n  {bold(cyan('▸'))} {bold(title)}")
    print(f"  {dim('─' * 40)}")


def bullet(text: str, color: str = "") -> str:
    """Format a bullet point."""
    dot = cyan("•") if not color else color("•")
    return f"  {dot} {text}"


def status_dot(ok: bool) -> str:
    """Green dot if ok, red dot if not."""
    return green("●") if ok else red("○")


def key_value(key: str, value: str, val_color: Callable = green) -> str:
    """Format a key: value pair."""
    return f"  {dim(key + ':')} {val_color(value)}"


def divider() -> None:
    """Print a dim divider line."""
    print(f"  {dim('─' * 50)}")


def box(text: str, color: str = "") -> str:
    """Wrap text in a subtle box."""
    lines = text.split("\n")
    width = max(len(l) for l in lines) + 4
    top = f"  {dim('┌' + '─' * (width - 2) + '┐')}"
    bottom = f"  {dim('└' + '─' * (width - 2) + '┘')}"
    middle = "\n".join(f"  {dim('│')} {l:<{width - 4}} {dim('│')}" for l in lines)
    return f"{top}\n{middle}\n{bottom}"


def welcome() -> None:
    """Print the Aishe welcome banner."""
    print()
    print(f"  {bold('╭──────────────────────────────────────────╮')}")
    print(f"  {bold('│')}        {cyan(bold('✨ Aishe CLI ✨'))}            {bold('│')}")
    print(f"  {bold('│')}  {dim('Voice-first AI for your terminal')}    {bold('│')}")
    print(f"  {bold('╰──────────────────────────────────────────╯')}")
    print()
    print(f"  {dim('Try: aishe pet · aishe status · aishe live')}")
    print()


def prompt_text() -> str:
    """Return the styled prompt prefix."""
    return f"{cyan(bold('You'))} {dim('▶')} "


def assistant_prefix() -> str:
    """Return the styled assistant prefix."""
    return f"{magenta(bold('Aishe'))} {dim('▶')} "


# ─── Service helpers ────────────────────────────────────────────────────────

def check(url: str, timeout: float = 3) -> bool:
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code < 300
    except Exception:
        return False


def get_service_url(name: str) -> str:
    return get(f"services.{name}", f"http://localhost:{name}")


# ─── Spinner ────────────────────────────────────────────────────────────────


class Spinner:
    """A simple terminal spinner for long-running operations."""

    def __init__(self, message: str = "", stream=sys.stderr):
        self._message = message
        self._stream = stream
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()

    def start(self) -> None:
        if not sys.stdout.isatty():
            return
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=0.5)
        if _color_enabled():
            self._stream.write("\r" + " " * 60 + "\r")
            self._stream.flush()

    def _spin(self) -> None:
        for c in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if not self._running:
                break
            self._stream.write(f"\r  {c} {self._message}")
            self._stream.flush()
            time.sleep(0.08)


# ─── Progress bar ───────────────────────────────────────────────────────────

class ProgressBar:
    """A simple progress bar for file operations."""

    def __init__(self, total: int, width: int = 30, prefix: str = ""):
        self._total = total
        self._width = width
        self._prefix = prefix
        self._current = 0

    def update(self, n: int = 1) -> None:
        self._current += n
        if not sys.stdout.isatty():
            return
        pct = self._current / self._total if self._total > 0 else 0
        filled = int(self._width * pct)
        bar = "█" * filled + "░" * (self._width - filled)
        sys.stderr.write(f"\r  {self._prefix} [{bar}] {int(pct * 100)}%")
        sys.stderr.flush()
        if self._current >= self._total:
            sys.stderr.write("\n")
