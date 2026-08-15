"""FastAPI server for Aishe DeepAgent."""
from __future__ import annotations
import json, os
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from agent import agent, extract_final_answer
from mem0_memory import (
    add as mem_add,
    search as mem_search,
    update as mem_update,
    delete as mem_delete,
    list_all as mem_list,
    status as mem_status,
)

HOST = os.environ.get("AISHE_HOST", "127.0.0.1")
PORT = int(os.environ.get("AISHE_PORT", "8765"))

class InvokeRequest(BaseModel):
    message: str = Field(..., description="The user's request")
    thread_id: str = Field(default="default", description="Conversation thread id")

class InvokeResponse(BaseModel):
    thread_id: str
    answer: str
    steps: int
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

class StreamRequest(InvokeRequest): pass

app = FastAPI(title="Aishe DeepAgent", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def root():
    return {"service": "aishe-deepagent", "model": os.environ.get("AISHE_MODEL", "deepseek-v4-flash:cloud"), "endpoints": ["/health", "/tools", "/invoke", "/stream"]}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/tools")
def list_tools():
    try: nodes = agent.get_graph().nodes; return {"nodes": sorted(nodes.keys())}
    except: return {"nodes": []}

def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}

def _summarize_tool_calls(messages) -> list[dict[str, Any]]:
    calls = []
    for msg in messages:
        if getattr(msg, "type", None) == "ai":
            for tc in getattr(msg, "tool_calls", []) or []:
                calls.append({"name": tc.get("name"), "args": tc.get("args"), "id": tc.get("id")})
    return calls

@app.post("/invoke", response_model=InvokeResponse)
def invoke(req: InvokeRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    state = agent.invoke({"messages": [{"role": "user", "content": req.message}]}, config=_config(req.thread_id))
    answer = extract_final_answer(state)
    messages = state.get("messages", [])
    return InvokeResponse(thread_id=req.thread_id, answer=answer, steps=len(messages), tool_calls=_summarize_tool_calls(messages))

@app.post("/stream")
def stream(req: StreamRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    def _gen():
        last_answer = ""
        try:
            for event in agent.stream({"messages": [{"role": "user", "content": req.message}]}, config=_config(req.thread_id), stream_mode="messages"):
                runnable, raw = event
                chunk = runnable
                if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                    for tc in chunk.tool_calls:
                        yield json.dumps({"event": "tool_call", "id": tc.get("id"), "name": tc.get("name"), "args": tc.get("args")}, default=str) + "\n"
                    continue
                if hasattr(chunk, "type") and chunk.type == "tool":
                    content = getattr(chunk, "content", "")
                    yield json.dumps({"event": "tool_result", "result": str(content)[:500]}, default=str) + "\n"
                    continue
                if hasattr(chunk, "type") and chunk.type in ("AIMessageChunk", "ai"):
                    content = getattr(chunk, "content", None)
                    if isinstance(content, str) and content:
                        last_answer += content
                        yield json.dumps({"event": "token", "content": content}) + "\n"
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                if text:
                                    last_answer += text
                                    yield json.dumps({"event": "token", "content": text}) + "\n"
        except Exception as e:
            # A provider/agent error mid-stream must not kill the connection —
            # emit it as an NDJSON event so the client can show it cleanly.
            print(f"[stream error] {type(e).__name__}: {e}", flush=True)
            yield json.dumps({"event": "error", "message": f"{type(e).__name__}: {e}"}) + "\n"
        # Always end the stream with a terminal event — a missing final is what
        # makes clients see "Response ended prematurely".
        yield json.dumps({"event": "final", "answer": last_answer}) + "\n"
    return StreamingResponse(_gen(), media_type="application/x-ndjson")

# ─── Memory endpoints (proxied to mem0_memory) ─────────────────────────────

class MemoryAddRequest(BaseModel):
    fact: str

class MemorySearchRequest(BaseModel):
    query: str
    limit: int = 10

class MemoryUpdateRequest(BaseModel):
    memory_id: str
    text: str

class MemoryDeleteRequest(BaseModel):
    memory_id: str

@app.post("/memory/add")
def memory_add(req: MemoryAddRequest):
    if not req.fact.strip():
        raise HTTPException(status_code=400, detail="fact must not be empty")
    return {"id": mem_add(req.fact)}

@app.post("/memory/search")
def memory_search(req: MemorySearchRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")
    return {"results": mem_search(req.query, limit=req.limit)}

@app.post("/memory/update")
def memory_update(req: MemoryUpdateRequest):
    ok = mem_update(req.memory_id, req.text)
    return {"status": "ok" if ok else "not_found"}

@app.post("/memory/delete")
def memory_delete(req: MemoryDeleteRequest):
    ok = mem_delete(req.memory_id)
    return {"status": "ok" if ok else "not_found"}

@app.get("/memory/list")
def memory_list():
    return {"results": mem_list()}

@app.get("/memory/status")
def memory_status():
    return {"status": mem_status()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=HOST, port=PORT, log_level="info")
