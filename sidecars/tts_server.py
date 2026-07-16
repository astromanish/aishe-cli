#!/usr/bin/env python3
"""
Aishe TTS sidecar — Supertonic-compatible OpenAI /v1/audio/speech API.

Backed by Kokoro ONNX (https://github.com/thewh1teagle/kokoro-onnx),
a high-quality CPU-runnable TTS with 54 voices. We expose the
OpenAI TTS API surface that aishe-cli expects.

Voice name mapping
------------------
Supertonic defines voices as "F1".."F5" (female) and "M1".."M5" (male).
Kokoro uses named voices like "af_bella". This server maps between them
so the aishe-cli config (`default_voice: F4`) keeps working.

Endpoints
---------
GET  /health              — liveness + model info
GET  /                    — service banner
POST /v1/audio/speech
                         — json: {input, voice, model, response_format}
                         — returns: WAV bytes
GET  /v1/voices           — list known voice aliases
"""
from __future__ import annotations

import io
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("aishe-tts")

# ─── Config ─────────────────────────────────────────────────────────────
HOST = os.environ.get("AISHE_TTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("AISHE_TTS_PORT", "8766"))

MODEL_DIR = os.environ.get(
    "AISHE_TTS_MODEL_DIR",
    os.path.expanduser("~/.local/share/aishe-cli/sidecars/models/kokoro"),
)
MODEL_PATH = os.path.join(MODEL_DIR, "kokoro-v1.0.onnx")
VOICES_PATH = os.path.join(MODEL_DIR, "voices-v1.0.bin")

VOICE_ALIASES = {
    "F1": "af_bella", "F2": "af_nicole", "F3": "af_sarah", "F4": "af_sky",
    "F5": "af_nova", "F6": "bf_emma", "F7": "bf_isabella", "F8": "af_heart",
    "F9": "af_kore", "F10": "af_river",
    "M1": "am_adam", "M2": "am_michael", "M3": "am_eric", "M4": "am_liam",
    "M5": "am_fenrir", "M6": "bm_george", "M7": "bm_lewis", "M8": "am_onyx",
    "M9": "am_puck", "M10": "am_echo",
}

DEFAULT_VOICE = "af_sky"
DEFAULT_SPEED = 1.0

_kokoro = None
_known_voices: list[str] = []


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _kokoro, _known_voices
    if not os.path.exists(MODEL_PATH):
        log.error(f"Model not found: {MODEL_PATH}")
    if not os.path.exists(VOICES_PATH):
        log.error(f"Voices not found: {VOICES_PATH}")
    from kokoro_onnx import Kokoro
    log.info(f"Loading Kokoro TTS from {MODEL_DIR}")
    t0 = time.time()
    _kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
    _known_voices = _kokoro.get_voices()
    log.info(f"Kokoro loaded in {time.time() - t0:.1f}s, {len(_known_voices)} voices")
    yield
    log.info("Shutting down TTS server")


app = FastAPI(title="Aishe TTS (Kokoro)", version="0.1.0", lifespan=lifespan)


@app.get("/")
def root():
    return {
        "service": "aishe-tts",
        "backend": "kokoro-onnx",
        "model_loaded": _kokoro is not None,
        "voices": _known_voices[:10] + (["..."] if len(_known_voices) > 10 else []),
        "aliases": VOICE_ALIASES,
        "endpoints": ["/health", "/v1/audio/speech", "/v1/voices"],
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": _kokoro is not None,
        "voice_count": len(_known_voices),
    }


@app.get("/v1/voices")
def list_voices():
    return {
        "aliases": VOICE_ALIASES,
        "kokoro_voices": _known_voices,
    }


class SpeechRequest(BaseModel):
    input: str
    voice: str = "F4"
    model: str = "supertonic-3"
    response_format: str = "wav"
    speed: Optional[float] = None


@app.post("/v1/audio/speech")
def synthesize(req: SpeechRequest):
    if _kokoro is None:
        raise HTTPException(status_code=503, detail="model not loaded yet")
    if not req.input or not req.input.strip():
        raise HTTPException(status_code=400, detail="input must not be empty")

    voice = VOICE_ALIASES.get(req.voice, req.voice)
    if voice not in _known_voices:
        log.warning(f"Unknown voice {req.voice!r}, falling back to {DEFAULT_VOICE}")
        voice = DEFAULT_VOICE

    speed = req.speed if req.speed is not None else DEFAULT_SPEED
    speed = max(0.5, min(2.0, speed))

    try:
        t0 = time.time()
        samples, sample_rate = _kokoro.create(
            req.input, voice=voice, speed=speed, lang="en-us"
        )
        elapsed = time.time() - t0
        log.info(f"Synthesized {len(req.input)} chars -> {len(samples)} samples "
                 f"({len(samples)/sample_rate:.1f}s audio) in {elapsed:.2f}s, voice={voice}")

        import soundfile as sf
        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
        buf.seek(0)

        return Response(
            content=buf.read(),
            media_type="audio/wav",
            headers={
                "X-Audio-Duration": f"{len(samples)/sample_rate:.2f}",
                "X-Audio-Sample-Rate": str(sample_rate),
                "X-Voice-Used": voice,
            },
        )
    except Exception as e:
        log.exception("Synthesis failed")
        raise HTTPException(status_code=500, detail=f"synthesis failed: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("tts_server:app", host=HOST, port=PORT, log_level="info")
