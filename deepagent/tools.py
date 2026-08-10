"""Custom tools for the Aishe DeepAgent."""
from __future__ import annotations
import ast
import json
import operator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from langchain_core.tools import tool

_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

def _safe_eval(expr: str) -> float:
    tree = ast.parse(expr, mode="eval")
    def _eval(node):
        if isinstance(node, ast.Expression): return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)): return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS: return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS: return _UNARY_OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"unsupported expression node: {type(node).__name__}")
    return _eval(tree)

@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression safely. Supports + - * / // % ** and parentheses."""
    try: return str(_safe_eval(expression))
    except Exception as exc: return f"error: {exc}"

@tool
def get_current_time(timezone: str = "UTC") -> str:
    """Return the current local time in a given IANA timezone. Examples: UTC, Asia/Kolkata, America/New_York."""
    try: tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError: return f"error: unknown timezone '{timezone}'"
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

@tool
def word_stats(text: str) -> str:
    """Count words, characters, and sentences in a piece of text."""
    words = [w for w in text.split() if w]
    sentences = [s for s in text.replace("?", ".").replace("!", ".").split(".") if s.strip()]
    avg = sum(len(w) for w in words) / len(words) if words else 0
    return f"words={len(words)} chars={len(text)} sentences={len(sentences)} avg_word_len={avg:.1f}"

MEMORY_FILE = Path.home() / ".local" / "share" / "aishe" / "memory" / "facts.jsonl"

# ─── Semantic memory (mem0 + Qdrant) ───────────────────────────────────────
# Richer than JSONL: auto-extraction, dedup, hybrid BM25 + semantic search.
# Falls back to the legacy JSONL store if mem0/Qdrant are unavailable.

def _mem0_add(fact: str) -> str:
    """Add a fact to semantic memory. Returns a confirmation string."""
    try:
        from mem0_memory import add as _add
        _add(fact)
        return f"Saved: {fact}"
    except Exception as exc:
        # Fallback to legacy JSONL
        import uuid
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {"id": f"mem_{uuid.uuid4().hex}", "fact": fact, "timestamp": datetime.now().isoformat()}
        with open(MEMORY_FILE, "a") as f: f.write(json.dumps(entry) + "\n")
        return f"Saved (legacy): {fact}"


def _mem0_search(query: str) -> str:
    """Search semantic memory. Returns formatted results."""
    try:
        from mem0_memory import search as _search
        results = _search(query, limit=10)
        if results:
            lines = [f"• {r['fact']} (saved {r.get('timestamp', '?')[:10]})" for r in results]
            return "Relevant memories:\n" + "\n".join(lines)
        return "No matching memories found."
    except Exception:
        # Fallback to legacy JSONL substring search
        if not MEMORY_FILE.exists(): return "No memories stored yet."
        query_words = [w for w in query.lower().split() if len(w) > 2]
        results = []
        for line in MEMORY_FILE.read_text().splitlines():
            if not line.strip(): continue
            try: entry = json.loads(line)
            except: continue
            fact_lower = entry.get("fact", "").lower()
            if any(word in fact_lower for word in query_words):
                results.append(f"• {entry['fact']} (saved {entry.get('timestamp', '?')[:10]})")
        if results: return "Relevant memories:\n" + "\n".join(results[:10])
        return "No matching memories found."


@tool
def memory_search(query: str) -> str:
    """Search the user's personal memory store for facts about them. Use this when the user asks about themselves."""
    return _mem0_search(query)


@tool
def memory_add(fact: str) -> str:
    """Save a new fact about the user to their personal memory store."""
    return _mem0_add(fact)


# ─── Web search ─────────────────────────────────────────────────────────────

import re as _re
import urllib.parse as _urlparse

@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for a query and return the top results (title, url, snippet). Use this to look up current information, news, or anything you don't know."""
    try:
        import requests as _req
        from html import unescape as _unescape
        url = "https://html.duckduckgo.com/html/?q=" + _urlparse.quote(query)
        r = _req.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        html = r.text
        # Each result is a <div class="result"> ... <a class="result__a" href="...">title</a> ... <a class="result__snippet">snippet</a>
        blocks = _re.findall(r'<div class="result[^"]*">(.*?)</div>\s*</div>', html, _re.S)
        results = []
        for block in blocks[:max_results]:
            m = _re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, _re.S)
            s = _re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, _re.S)
            if not m:
                continue
            href = m.group(1)
            # DDG wraps real URLs in a redirect; extract the uddg param
            uddg = _re.search(r'uddg=([^&]+)', href)
            real = _urlparse.unquote(uddg.group(1)) if uddg else href
            title = _re.sub(r'<[^>]+>', '', m.group(2))
            snippet = _re.sub(r'<[^>]+>', '', s.group(1)) if s else ""
            results.append(f"• {_unescape(title.strip())}\n  {real}\n  {_unescape(snippet.strip())}")
        if not results:
            return f"No results for '{query}'."
        return "\n\n".join(results)
    except Exception as exc:
        return f"web_search error: {exc}"


@tool
def web_extract(url: str, max_chars: int = 4000) -> str:
    """Fetch a web page and return its readable text content. Use this to read the full content of a URL (e.g. a search result, article, or page)."""
    try:
        import requests as _req
        from html import unescape as _unescape
        r = _req.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        html = r.text
        # Strip scripts/styles, then tags, then collapse whitespace
        html = _re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=_re.S | _re.I)
        text = _re.sub(r'<[^>]+>', ' ', html)
        text = _unescape(text)
        text = _re.sub(r'\s+', ' ', text).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…[truncated]"
        return text or "(no readable text on page)"
    except Exception as exc:
        return f"web_extract error: {exc}"


# ─── CLI / shell execution ─────────────────────────────────────────────────

import subprocess as _sp

@tool
def run_command(command: str, timeout: int = 30) -> str:
    """Run a shell command on the host machine and return its output. Use for file listing, system info, git, etc. Example: 'ls -la', 'date', 'pwd', 'git status'."""
    try:
        r = _sp.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        out = out.strip()
        if len(out) > 4000:
            out = out[:4000] + "\n…[truncated]"
        if r.returncode != 0:
            return f"exit {r.returncode}:\n{out or '(no output)'}"
        return out or "(no output)"
    except _sp.TimeoutExpired:
        return f"error: command timed out after {timeout}s"
    except Exception as exc:
        return f"run_command error: {exc}"
