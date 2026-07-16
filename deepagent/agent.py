"""Deep agent setup for Aishe CLI."""
from __future__ import annotations
import os
from pathlib import Path
from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_openai import ChatOpenAI
from tools import calculator, get_current_time, word_stats, memory_search, memory_add

MODEL_NAME = os.environ.get("AISHE_MODEL", "qwen2.5:3b")
BASE_URL = os.environ.get("AISHE_OLLAMA_URL", "http://localhost:11434/v1")
API_KEY = os.environ.get("AISHE_API_KEY", "ollama")

_model = ChatOpenAI(model=MODEL_NAME, base_url=BASE_URL, api_key=API_KEY, temperature=0.1)
WORKSPACE = Path(__file__).parent / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

SYSTEM_PROMPT = f"""You are a helpful research and analysis assistant.

You have these custom tools:
- `calculator`: evaluate arithmetic expressions like "(15 * 4) / 3"
- `get_current_time`: get the current time in any IANA timezone
- `word_stats`: count words, characters, and sentences in a piece of text
- `memory_search`: search the user's personal memory for facts about them
- `memory_add`: save a new fact about the user to their personal memory

IMPORTANT — always check memory first:
Whenever the user asks about themselves, call `memory_search` with a relevant keyword BEFORE answering.
When the user tells you something personal, call `memory_add` to save it.

You also have built-in tools to manage a todo list, read/write files in {WORKSPACE}, and delegate to a sub-agent named `researcher`.
Be concise in your final answer — short paragraphs, no preamble.
"""

RESEARCHER_SUBAGENT = {
    "name": "researcher",
    "description": "A focused research sub-agent for digging into specific sub-questions.",
    "system_prompt": "You are a focused research sub-agent. Answer concisely.",
}

def build_agent():
    return create_deep_agent(
        model=_model,
        tools=[calculator, get_current_time, word_stats, memory_search, memory_add],
        system_prompt=SYSTEM_PROMPT,
        subagents=[RESEARCHER_SUBAGENT],
        backend=FilesystemBackend(root_dir=WORKSPACE, virtual_mode=True),
        name="aishe-deepagent",
    )

agent = build_agent()

def extract_final_answer(state: dict) -> str:
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            content = msg.content
            if isinstance(content, str): return content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text": parts.append(block.get("text", ""))
                    elif isinstance(block, str): parts.append(block)
                return "\n".join(parts).strip()
    return ""
