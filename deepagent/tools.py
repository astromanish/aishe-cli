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

@tool
def memory_search(query: str) -> str:
    """Search the user's personal memory store for facts about them. Use this when the user asks about themselves."""
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
def memory_add(fact: str) -> str:
    """Save a new fact about the user to their personal memory store."""
    import uuid
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {"id": f"mem_{uuid.uuid4().hex}", "fact": fact, "timestamp": datetime.now().isoformat()}
    with open(MEMORY_FILE, "a") as f: f.write(json.dumps(entry) + "\n")
    return f"Saved: {fact}"
