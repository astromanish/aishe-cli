"""Aishe Telegram bridge — long-polling bot sidecar.

Listens on Telegram via the Bot API getUpdates long-polling and routes each
chat's messages to the Aishe DeepAgent sidecar, then replies. Each Telegram
chat gets its own persistent thread keyed `telegram:<chat_id>`.

Runs standalone:
    python telegram_bot.py
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import requests

# ─── Config ────────────────────────────────────────────────────────────────

CONFIG_FILE = Path(os.environ.get("AISHE_CONFIG", "~/.config/aishe/config.yaml")).expanduser()


def _load_config() -> dict:
    try:
        import yaml
        if CONFIG_FILE.exists():
            return yaml.safe_load(CONFIG_FILE.read_text()) or {}
    except Exception:
        pass
    return {}


def _deep_get(cfg: dict, key: str, default=None):
    cur = cfg
    for part in key.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
        if cur is None:
            return default
    return cur


# ─── Telegram API helpers ──────────────────────────────────────────────────

API = "https://api.telegram.org/bot{token}/{method}"


def tg(token: str, method: str, **params):
    r = requests.post(API.format(token=token, method=method), json=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _send(token: str, chat_id, text: str) -> None:
    try:
        tg(token, "sendMessage", chat_id=chat_id, text=text)
        print(f"[aishe-tg] → sent {len(text)} chars to {chat_id}", flush=True)
    except Exception as e:
        print(f"[aishe-tg] send failed: {e}", flush=True)


def _answer(token: str, chat_id, message: str, deepagent_url: str, tid: str) -> None:
    """Ask DeepAgent and send the reply."""
    try:
        r = requests.post(
            f"{deepagent_url}/invoke",
            json={"message": message, "thread_id": tid},
            timeout=120,
        )
        r.raise_for_status()
        answer = r.json().get("answer", "")
        print(f"[aishe-tg] ← DeepAgent answered {len(answer)} chars", flush=True)
    except Exception as e:
        answer = f"(error: {e})"
        print(f"[aishe-tg] ← DeepAgent error: {e}", flush=True)
    _send(token, chat_id, answer or "(empty response)")


# ─── Main loop ─────────────────────────────────────────────────────────────

RUNNING = True


def _stop(*_):
    global RUNNING
    RUNNING = False


def main() -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    cfg = _load_config()
    token = _deep_get(cfg, "telegram.token", os.environ.get("AISHE_TELEGRAM_TOKEN", ""))
    allowed = _deep_get(cfg, "telegram.allowed_users", []) or []
    if isinstance(allowed, str):
        allowed = [int(x) for x in allowed.split(",") if x.strip().isdigit()]
    allow_all = bool(_deep_get(cfg, "telegram.allow_all", False))
    deepagent_url = _deep_get(cfg, "services.deepagent", "http://localhost:8765")

    if not token:
        print("[aishe-tg] No bot token. Set telegram.token in config or AISHE_TELEGRAM_TOKEN.", flush=True)
        sys.exit(1)

    print(f"[aishe-tg] Starting bridge (allow_all={allow_all}, allowed={allowed})", flush=True)

    # Validate token + get bot identity
    try:
        me = tg(token, "getMe")
        print(f"[aishe-tg] Connected as @{me.get('result', {}).get('username', '?')}", flush=True)
    except Exception as e:
        print(f"[aishe-tg] Bad token: {e}", flush=True)
        sys.exit(1)

    offset = 0
    while RUNNING:
        try:
            up = tg(token, "getUpdates", offset=offset, timeout=30)
        except requests.exceptions.RequestException as e:
            print(f"[aishe-tg] getUpdates error: {e}", flush=True)
            time.sleep(5)
            continue

        for update in up.get("result", []):
            offset = update.get("update_id", offset) + 1
            msg = update.get("message") or update.get("edited_message")
            if not msg:
                continue
            chat = msg.get("chat", {})
            chat_id = chat.get("id")
            user = msg.get("from", {})
            user_id = user.get("id")
            text = msg.get("text")

            if not text or not text.strip():
                continue
            # ignore bot's own messages
            if user.get("is_bot"):
                continue
            # allow-list gate
            if not allow_all and user_id not in allowed:
                print(f"[aishe-tg] Denied user {user_id} ({user.get('username', '?')})", flush=True)
                continue

            tid = f"telegram:{chat_id}"
            print(f"[aishe-tg] {user_id}: {text!r}", flush=True)
            _answer(token, chat_id, text, deepagent_url, tid)

    print("[aishe-tg] Stopped.", flush=True)


if __name__ == "__main__":
    main()
