"""Ollama integration — model management."""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict, List, Optional

import requests

from .config import get
from .util import bold, check, cyan, dim, green, red

OLLAMA_URL = get("services.ollama", "http://localhost:11434")


def list_models() -> List[Dict[str, Any]]:
    """List pulled Ollama models."""
    r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
    r.raise_for_status()
    return r.json().get("models", [])


def pull(model: str) -> bool:
    """Pull a model via ollama CLI."""
    result = subprocess.run(["ollama", "pull", model])
    return result.returncode == 0


def whoami() -> Optional[str]:
    """Check Ollama login status."""
    result = subprocess.run(["ollama", "whoami"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def health() -> bool:
    return check(f"{OLLAMA_URL}/api/tags")


# ─── CLI handlers ───────────────────────────────────────────────────────────

def cmd_ollama(args: Any) -> None:
    action = args.action

    if action == "models":
        try:
            models = list_models()
        except requests.exceptions.ConnectionError:
            print(red("Ollama not reachable on :11434"))
            sys.exit(1)
        if not models:
            print(dim("No models pulled. Use: aishe ollama pull <model>"))
            return
        print(bold(f"Ollama Models ({len(models)})"))
        print("─" * 60)
        for m in models:
            size_mb = m.get("size", 0) / 1024 / 1024
            print(f"  {m['name']:40s} {dim(f'{size_mb:.0f} MB')}")
        return

    if action == "pull":
        model = args.model
        if not model:
            print(red("Specify model: aishe ollama pull <model>"))
            sys.exit(1)
        print(f"Pulling {model}...")
        if pull(model):
            print(f"{green('Pulled')} {model}")
        else:
            print(red(f"Failed to pull {model}"))
        return

    if action == "whoami":
        user = whoami()
        if user:
            print(f"Logged in as: {green(user)}")
        else:
            print(red("Not logged in to Ollama"))
        return

    if action == "signin":
        import webbrowser
        webbrowser.open("https://ollama.com/login")
        print("Opening browser for Ollama login...")
        return
