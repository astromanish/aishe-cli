"""FastAPI server for Aishe DeepAgent."""
from __future__ import annotations
import json, os
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from agent import agent, extract_final_answer

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
    return {"service": "aishe-deepagent", "model": os.environ.get("AISHE_MODEL", "qwen2.5:3b"), "endpoints": ["/health", "/tools", "/invoke", "/stream"]}

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
        yield json.dumps({"event": "final", "answer": last_answer}) + "\n"
    return StreamingResponse(_gen(), media_type="application/x-ndjson")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=HOST, port=PORT, log_level="info")
