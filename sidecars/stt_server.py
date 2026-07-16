#!/usr/bin/env python3
"""
Aishe STT sidecar — Parakeet-compatible OpenAI /v1/audio/transcriptions API.

Backed by faster-whisper (https://github.com/SYSTRAN/faster-whisper), a
CTranslate2 port of OpenAI Whisper that runs on CPU or GPU. We expose
the OpenAI Whisper API surface that aishe-cli expects, so any client
that targets OpenAI /v1/audio/transcriptions works against us.

Endpoints
---------
GET  /healthz           — liveness probe
GET  /                  — service banner
POST /v1/audio/transcriptions
                        — multipart upload: file=@<wav>, model=parakeet-tdt-0.6b-v3
                        — returns: {"text": "..."}
POST /v1/models         — list available models
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("aishe-stt")

# ─── Config ─────────────────────────────────────────────────────────────
HOST = os.environ.get("AISHE_STT_HOST", "127.0.0.1")
PORT = int(os.environ.get("AISHE_STT_PORT", "5093"))
MODEL_SIZE = os.environ.get("AISHE_STT_MODEL", "small")  # tiny/base/small/medium/large-v3
DEVICE = os.environ.get("AISHE_STT_DEVICE", "cpu")        # cuda or cpu
COMPUTE_TYPE = os.environ.get("AISHE_STT_COMPUTE", "int8")   # int8 for CPU; float16 for cuda
BEAM_SIZE = int(os.environ.get("AISHE_STT_BEAM", "5"))

_model = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _model
    from faster_whisper import WhisperModel
    log.info(f"Loading faster-whisper model={MODEL_SIZE!r} device={DEVICE!r} compute={COMPUTE_TYPE!r}")
    t0 = time.time()
    _model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    log.info(f"Model loaded in {time.time() - t0:.1f}s")
    yield
    log.info("Shutting down STT server")


app = FastAPI(title="Aishe STT (faster-whisper)", version="0.1.0", lifespan=lifespan)


@app.get("/")
def root():
    return {
        "service": "aishe-stt",
        "backend": "faster-whisper",
        "model": MODEL_SIZE,
        "device": DEVICE,
        "endpoints": ["/healthz", "/v1/audio/transcriptions", "/v1/models"],
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok", "model_loaded": _model is not None, "model": MODEL_SIZE}


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "parakeet-tdt-0.6b-v3", "object": "model", "owned_by": "aishe-fake"},
            {"id": MODEL_SIZE, "object": "model", "owned_by": "faster-whisper"},
        ],
    }


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default="parakeet-tdt-0.6b-v3"),
    language: Optional[str] = Form(default=None),
    response_format: str = Form(default="json"),
):
    if _model is None:
        raise HTTPException(status_code=503, detail="model not loaded yet")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="empty file")

    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        t0 = time.time()
        segments, info = _model.transcribe(
            tmp_path,
            beam_size=BEAM_SIZE,
            language=language,
            vad_filter=True,
        )
        text_parts = [seg.text.strip() for seg in segments]
        text = " ".join(p for p in text_parts if p).strip()
        elapsed = time.time() - t0
        log.info(f"Transcribed {len(contents)} bytes in {elapsed:.2f}s "
                 f"(lang={info.language}, prob={info.language_probability:.2f}): {text[:80]!r}")
        return {"text": text}
    except Exception as e:
        log.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=f"transcription failed: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("stt_server:app", host=HOST, port=PORT, log_level="info")
