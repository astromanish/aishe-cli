"""Aishe Telegram bridge — long-polling bot sidecar (minimal streaming).

Listens on Telegram via the Bot API getUpdates long-polling and routes each
chat's messages to the Aishe DeepAgent sidecar's /stream endpoint.

Tool calls (web/cli/memory) are logged to the log file but NOT posted to the
chat — the user sees a single minimal "⏳ Working…" status that is finally
replaced with the rich-formatted answer. Tool activity stays invisible in the
chat; only the last response is shown, formatted (bold, italics, code, links,
headings) via Telegram HTML.

Each Telegram chat gets its own persistent thread keyed `telegram:<chat_id>`.

Runs standalone:
    python telegram_bot.py
"""
from __future__ import annotations

import html
import json
import os
import re
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


def _send(token: str, chat_id, text: str, parse: str | None = None) -> int | None:
    try:
        res = tg(token, "sendMessage", chat_id=chat_id, text=text,
                 parse_mode=parse, disable_web_page_preview=True)
        return res.get("result", {}).get("message_id")
    except Exception as e:
        body = getattr(e, "response", None)
        detail = getattr(body, "text", "") if body is not None else ""
        print(f"[aishe-tg] send failed: {e} :: {detail}", flush=True)
        return None


def _edit(token: str, chat_id, message_id, text: str, parse: str | None = None) -> None:
    if message_id is None:
        return
    try:
        tg(token, "editMessageText", chat_id=chat_id, message_id=message_id,
           text=text, parse_mode=parse, disable_web_page_preview=True)
    except Exception as e:
        if "not modified" not in str(e):
            body = getattr(e, "response", None)
            detail = getattr(body, "text", "") if body is not None else ""
            print(f"[aishe-tg] edit failed: {e} :: {detail}", flush=True)


def _log(log_file: str | None, line: str) -> None:
    if not log_file:
        return
    try:
        with open(log_file, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ─── Markdown → Telegram HTML ──────────────────────────────────────────────

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_to_html(text: str) -> str:
    """Minimal markdown → Telegram-HTML. Telegram only supports <b> <i> <code>
    <pre> <a href> <u> <s> — headings collapse to bold. Code is HTML-escaped."""
    code_blocks: list[str] = []
    def _block(m):
        code_blocks.append(m.group(2))
        return f"\x00B{len(code_blocks)-1}\x00"
    text = re.sub(r"```[ \t]*(\w+)?[ \t]*\n?(.*?)```", _block, text, flags=re.S)

    inline: list[str] = []
    def _inl(m):
        inline.append(m.group(1))
        return f"\x00I{len(inline)-1}\x00"
    text = re.sub(r"`([^`]+)`", _inl, text)

    text = _esc(text)

    text = re.sub(r"^#{1,6}\s+(.+)$", lambda m: f"<b>{m.group(1)}</b>", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__([^_]+)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"<i>\1</i>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)

    def _inl_rest(m):
        return f"<code>{_esc(inline[int(m.group(1))])}</code>"
    text = re.sub(r"\x00I(\d+)\x00", _inl_rest, text)
    def _block_rest(m):
        return f"<pre>{_esc(code_blocks[int(m.group(1))])}</pre>"
    text = re.sub(r"\x00B(\d+)\x00", _block_rest, text)

    text = re.sub(r"^---+$", "──────────", text, flags=re.M)
    text = re.sub(r"^[ \t]*[-*]\s+", "• ", text, flags=re.M)
    return text


# ─── Streaming reply (minimal — only final answer shown) ───────────────────

def _stream_answer(token: str, chat_id, message: str, deepagent_url: str, tid: str,
                   log_file: str | None = None) -> None:
    """Consume /stream, show each tool call as a compact one-liner, deliver the
    rich-formatted final answer."""
    try:
        tg(token, "sendChatAction", chat_id=chat_id, action="typing")
    except Exception:
        pass

    try:
        r = requests.post(
            f"{deepagent_url}/stream",
            json={"message": message, "thread_id": tid},
            stream=True,
            timeout=(10, 300),
        )
        r.raise_for_status()
    except Exception as e:
        _send(token, chat_id, f"(error connecting to DeepAgent: {e})")
        return

    status_id = _send(token, chat_id, "⏳ Working…")
    answer_buf = ""
    final_answer = ""
    tool_count = 0

    try:
        for raw in r.iter_lines(decode_unicode=True):
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue

            etype = ev.get("event")

            if etype == "tool_call":
                tool_count += 1
                name = ev.get("name", "?")
                _log(log_file, f"[tool] {name} args={ev.get('args', {})}")
                # one compact line per tool call — name only, no args spam
                _send(token, chat_id, f"🔧 <code>{html.escape(name)}</code>", parse="HTML")

            elif etype == "tool_result":
                _log(log_file, f"[result] {str(ev.get('result', ''))[:300]}")

            elif etype == "token":
                answer_buf += ev.get("content", "")

            elif etype == "final":
                final_answer = ev.get("answer", "") or answer_buf

        done = (final_answer or answer_buf or "(empty response)").strip()
        rich = _md_to_html(done)
        _log(log_file, f"[done] tools={tool_count} answer_len={len(done)}")
        print(f"[aishe-tg] ← {len(done)} chars, {tool_count} tool calls → {chat_id}", flush=True)

        # Edit the status message into the rich answer (chunked for Telegram's 4096 limit).
        _deliver_rich(token, chat_id, status_id, rich)

    except Exception as e:
        _edit(token, chat_id, status_id, html.escape(answer_buf[-4000:] or f"(stream error: {e})"))
        print(f"[aishe-tg] stream error: {e}", flush=True)


def _deliver_rich(token: str, chat_id, status_id: int, rich_html: str) -> None:
    """Send rich HTML in ≤4096-char chunks; first chunk edits the status msg."""
    MAX = 4000
    if len(rich_html) <= MAX:
        _edit(token, chat_id, status_id, rich_html, parse="HTML")
        return
    # chunk on paragraph boundaries so HTML tags never split
    chunks: list[str] = []
    current = ""
    for para in re.split(r"(\n{2,})", rich_html):
        if len(current) + len(para) > MAX and current:
            chunks.append(current)
            current = para
        else:
            current += para
    if current:
        chunks.append(current)
    # safety: split any overlong chunk mid-text
    final_chunks: list[str] = []
    for c in chunks:
        while len(c) > MAX:
            final_chunks.append(c[:MAX])
            c = c[MAX:]
        if c:
            final_chunks.append(c)
    _edit(token, chat_id, status_id, final_chunks[0], parse="HTML")
    for c in final_chunks[1:]:
        _send(token, chat_id, c, parse="HTML")


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
    log_file = _deep_get(cfg, "telegram.log_file", None)

    if not token:
        print("[aishe-tg] No bot token. Set telegram.token in config or AISHE_TELEGRAM_TOKEN.", flush=True)
        sys.exit(1)

    print(f"[aishe-tg] Starting bridge (allow_all={allow_all}, allowed={allowed})", flush=True)

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
            if user.get("is_bot"):
                continue
            if not allow_all and user_id not in allowed:
                print(f"[aishe-tg] Denied user {user_id} ({user.get('username', '?')})", flush=True)
                continue

            tid = f"telegram:{chat_id}"
            print(f"[aishe-tg] {user_id}: {text!r}", flush=True)
            _log(log_file, f"[user {user_id}] {text}")
            _stream_answer(token, chat_id, text, deepagent_url, tid, log_file)

    print("[aishe-tg] Stopped.", flush=True)


if __name__ == "__main__":
    main()
