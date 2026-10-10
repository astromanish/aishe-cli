#!/usr/bin/env python3
"""
Aishe TTS sidecar — Sarvam cloud backend.

Exposes the SAME OpenAI-compatible surface as tts_server.py
(POST /v1/audio/speech returning raw WAV bytes, GET /health, GET /v1/voices)
but synthesizes with Sarvam's cloud bulbul model instead of local Kokoro ONNX.

Port swap, not a code change: the CLI (aishe_pkg/voice.py) and the Electron live
app (electron-live/lib/services.js) both hardcode :8766, so selecting Sarvam
means running this server on :8766 in place of the local one.

Supertonic voice aliases (F1-F10 / M1-M10) are mapped onto bulbul:v3 speaker
names so the existing `voice.default_voice: F4` config keeps working.

Sarvam caps input length per request, so long text is sentence-chunked, decoded
to PCM, concatenated, and returned as one WAV.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import numpy as np
import requests
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("aishe-tts-sarvam")

HOST = os.environ.get("AISHE_TTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("AISHE_TTS_PORT", "8766"))
BASE_URL = os.environ.get("SARVAM_BASE_URL", "https://api.sarvam.ai").rstrip("/")
TTS_MODEL = os.environ.get("SARVAM_TTS_MODEL", "bulbul:v3")
LANG = os.environ.get("SARVAM_TTS_LANG", "en-IN")
TIMEOUT = int(os.environ.get("SARVAM_TTS_TIMEOUT", "120"))
MAX_CHARS = int(os.environ.get("SARVAM_TTS_MAX_CHARS", "450"))

DEFAULT_VOICE = "F4"

# Supertonic alias -> bulbul:v3 speaker. F4 is the aishe default.
VOICE_ALIASES = {
    "F1": "ritu", "F2": "neha", "F3": "pooja", "F4": "priya", "F5": "simran",
    "F6": "kavya", "F7": "ishita", "F8": "shreya", "F9": "roopa", "F10": "tanya",
    "M1": "aditya", "M2": "rahul", "M3": "rohan", "M4": "amit", "M5": "dev",
    "M6": "varun", "M7": "manan", "M8": "sumit", "M9": "kabir", "M10": "shubh",
}
VALID_SPEAKERS = set(VOICE_ALIASES.values())


def _cfg() -> dict:
    try:
        import yaml  # noqa
    except ImportError:
        return {}
    p = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "aishe" / "config.yaml"
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text()) or {}
    except Exception:
        return {}


_CFG = _cfg()


def _api_key() -> str:
    """Resolve the SARVAM key specifically.

    Order matters: a generic AISHE_API_KEY may hold a *different* provider's key
    (e.g. an OpenRouter key left in the environment by the DeepAgent env file),
    and forwarding that to Sarvam yields a 403. So the provider-specific sources
    win, and a generic var is only accepted when it actually looks like a key
    Sarvam would accept.
    """
    k = os.environ.get("SARVAM_API_KEY")
    if k:
        return k
    k = ((_CFG.get("providers") or {}).get("sarvam") or {}).get("api_key") or ""
    if k:
        return k
    k = os.environ.get("AISHE_API_KEY") or ""
    if k and not k.startswith(("sk-or-", "sk-ant-", "sk-proj-")):
        return k
    return ""


def resolve_speaker(voice: str) -> str:
    """Accept 'F4', 'priya', or anything else — always return a valid speaker."""
    if not voice:
        return VOICE_ALIASES[DEFAULT_VOICE]
    if voice in VOICE_ALIASES:
        return VOICE_ALIASES[voice]
    if voice in VALID_SPEAKERS:
        return voice
    return VOICE_ALIASES[DEFAULT_VOICE]


def chunk_text(text: str, limit: int = MAX_CHARS) -> List[str]:
    """Split text into <= limit-char chunks on sentence, then word, boundaries."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    sentences = re.split(r"(?<=[.!?।…])\s+", text)
    chunks: List[str] = []
    cur = ""
    for s in sentences:
        if len(s) > limit:  # hard-wrap an over-long sentence
            if cur:
                chunks.append(cur); cur = ""
            words = s.split()
            line = ""
            for w in words:
                if len(line) + len(w) + 1 > limit:
                    chunks.append(line); line = w
                else:
                    line = f"{line} {w}".strip()
            if line:
                cur = line
            continue
        if len(cur) + len(s) + 1 > limit:
            chunks.append(cur); cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def sarvam_tts(text: str, speaker: str, pace: Optional[float] = None) -> bytes:
    """Call Sarvam TTS for one chunk, return raw WAV bytes."""
    key = _api_key()
    if not key:
        raise HTTPException(status_code=503, detail="no Sarvam API key configured")
    body = {"text": text, "target_language_code": LANG, "speaker": speaker, "model": TTS_MODEL}
    if pace and pace != 1.0:
        body["pace"] = pace
    r = requests.post(f"{BASE_URL}/text-to-speech",
                      headers={"api-subscription-key": key, "Content-Type": "application/json"},
                      json=body, timeout=TIMEOUT)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=f"sarvam error: {r.text[:400]}")
    audios = (r.json() or {}).get("audios") or []
    if not audios:
        raise HTTPException(status_code=502, detail="sarvam returned no audio")
    return base64.b64decode(audios[0])


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Sarvam TTS proxy starting — endpoint=%s model=%s speech=%s key=%s",
             BASE_URL, TTS_MODEL, resolve_speaker(DEFAULT_VOICE),
             "present" if _api_key() else "MISSING")
    if not _api_key():
        log.error("No Sarvam API key: set SARVAM_API_KEY or providers.sarvam.api_key in config.yaml")
    yield


app = FastAPI(title="Aishe TTS (Sarvam cloud)", version="0.1.0", lifespan=lifespan)


class SpeechRequest(BaseModel):
    input: str
    voice: str = DEFAULT_VOICE
    model: str = "supertonic-3"
    response_format: str = "wav"
    speed: Optional[float] = None


@app.get("/")
def root():
    return {"service": "aishe-tts-sarvam", "backend": "sarvam-cloud", "endpoint": BASE_URL,
            "model": TTS_MODEL, "default_voice": resolve_speaker(DEFAULT_VOICE)}


@app.get("/health")
def health():
    return {"status": "ok", "backend": "sarvam", "model_loaded": True,
            "model": TTS_MODEL, "voice_count": len(VALID_SPEAKERS),
            "key_present": bool(_api_key())}


@app.get("/v1/voices")
def voices():
    return {"voices": [{"alias": a, "speaker": s} for a, s in VOICE_ALIASES.items()]}


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    text = (req.input or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty input")

    speaker = resolve_speaker(req.voice)
    pace = None
    if req.speed and req.speed != 1.0:
        pace = max(0.5, min(2.0, float(req.speed)))

    chunks = chunk_text(text)
    t0 = time.time()
    pieces: List[np.ndarray] = []
    sr: Optional[int] = None
    for c in chunks:
        wav = sarvam_tts(c, speaker, pace)
        data, this_sr = sf.read(io.BytesIO(wav), dtype="float32", always_2d=False)
        if sr is None:
            sr = this_sr
        pieces.append(data)

    if not pieces:
        raise HTTPException(status_code=502, detail="no audio produced")

    merged = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]
    buf = io.BytesIO()
    sf.write(buf, merged, sr, format="WAV", subtype="PCM_16")
    out = buf.getvalue()

    log.info("synth ok voice=%s speaker=%s chunks=%d chars=%d bytes=%d elapsed=%.2fs",
             req.voice, speaker, len(chunks), len(text), len(out), time.time() - t0)
    return Response(content=out, media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("sarvam_tts_server:app", host=HOST, port=PORT, log_level="info")
