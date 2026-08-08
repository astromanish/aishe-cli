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


def list_running_models() -> List[Dict[str, Any]]:
    """List currently running Ollama models."""
    r = requests.get(f"{OLLAMA_URL}/api/ps", timeout=5)
    r.raise_for_status()
    return r.json().get("models", [])


def pull(model: str) -> bool:
    """Pull a model via ollama CLI."""
    result = subprocess.run(["ollama", "pull", model])
    return result.returncode == 0


def remove(model: str) -> bool:
    """Remove a model via ollama CLI."""
    result = subprocess.run(["ollama", "rm", model])
    return result.returncode == 0


def stop(model: str) -> bool:
    """Stop a running model via ollama CLI."""
    result = subprocess.run(["ollama", "stop", model])
    return result.returncode == 0


def is_signed_in() -> bool:
    """Check whether Ollama is signed in by reading the public key it prints."""
    result = subprocess.run(["ollama", "signin"], capture_output=True, text=True)
    # `ollama signin` with no args prints the public key and exits 0 when
    # already signed in; otherwise it prompts or exits non-zero.
    return result.returncode == 0 and "Ollama" in (result.stdout + result.stderr)


def signin() -> bool:
    """Sign in to Ollama using its CLI."""
    result = subprocess.run(["ollama", "signin"])
    return result.returncode == 0


def signout() -> bool:
    """Sign out from Ollama using its CLI."""
    result = subprocess.run(["ollama", "signout"])
    return result.returncode == 0


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

    if action == "running":
        try:
            models = list_running_models()
        except requests.exceptions.ConnectionError:
            print(red("Ollama not reachable on :11434"))
            sys.exit(1)
        if not models:
            print(dim("No models currently running."))
            return
        print(bold(f"Running Models ({len(models)})"))
        print("─" * 60)
        for m in models:
            name = m.get("name", "?")
            expires = m.get("expires_at", "?")
            size_mb = m.get("size", 0) / 1024 / 1024
            print(f"  {name:40s} {dim(f'{size_mb:.0f} MB')} {dim(f'expires {expires}')}")
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

    if action == "rm":
        model = args.model
        if not model:
            print(red("Specify model: aishe ollama rm <model>"))
            sys.exit(1)
        if remove(model):
            print(f"{green('Removed')} {model}")
        else:
            print(red(f"Failed to remove {model}"))
        return

    if action == "stop":
        model = args.model
        if not model:
            print(red("Specify model: aishe ollama stop <model>"))
            sys.exit(1)
        if stop(model):
            print(f"{green('Stopped')} {model}")
        else:
            print(red(f"Failed to stop {model}"))
        return

    if action == "signin":
        if signin():
            print(green("Signed in to Ollama"))
        else:
            print(red("Ollama sign-in failed or was cancelled"))
            sys.exit(1)
        return

    if action == "signout":
        if signout():
            print(green("Signed out from Ollama"))
        else:
            print(red("Ollama sign-out failed"))
            sys.exit(1)
        return

    if action == "status":
        try:
            models = list_models()
            running = list_running_models()
        except requests.exceptions.ConnectionError:
            print(red("Ollama not reachable on :11434"))
            sys.exit(1)
        print(bold("Ollama Status"))
        print(f"  {green('●')} Server reachable on {cyan(OLLAMA_URL)}")
        print(f"  {cyan(str(len(models)))} pulled model(s)")
        print(f"  {cyan(str(len(running)))} running model(s)")
        return
