"""Terminal utilities — colors, formatting, service checks."""

from __future__ import annotations

import os
import sys
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


def bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if _color_enabled() else s


def dim(s: str) -> str:
    return f"\033[2m{s}\033[0m" if _color_enabled() else s


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

import itertools
import threading
import time


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
