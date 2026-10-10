#!/usr/bin/env python3
"""
Aishe STT sidecar — Sarvam cloud backend.

Exposes the SAME OpenAI-compatible surface as stt_server.py
(POST /v1/audio/transcriptions, GET /healthz, GET /v1/models) but proxies the
transcription to Sarvam's cloud Speech-to-Text API instead of running a local
whisper model.

This exists so the voice backend becomes a *port swap*: the CLI
(aishe_pkg/voice.py), the Electron live app (electron-live/lib/services.js) and
the agent all address :5093 unconditionally, so selecting Sarvam means running
this server on :5093 instead of the local one. Nothing downstream changes.

Config resolution order (first hit wins):
  1. explicit env vars
  2. ~/.config/aishe/config.yaml  -> providers.sarvam.api_key  (+ sarvam_voice.*)
  3. defaults
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("aishe-stt-sarvam")

HOST = os.environ.get("AISHE_STT_HOST", "127.0.0.1")
PORT = int(os.environ.get("AISHE_STT_PORT", "5093"))
BASE_URL = os.environ.get("SARVAM_BASE_URL", "https://api.sarvam.ai").rstrip("/")
DEFAULT_MODEL = os.environ.get("SARVAM_STT_MODEL", "saarika:v2.5")
DEFAULT_LANG = os.environ.get("SARVAM_STT_LANG", "en-IN")
TIMEOUT = int(os.environ.get("SARVAM_STT_TIMEOUT", "120"))


def _cfg() -> dict:
    """Read aishe config.yaml if PyYAML + the file are available."""
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


def _voice_cfg(key: str, default: str) -> str:
    return str((_CFG.get("sarvam_voice") or {}).get(key, default))


VALID_MODELS = {
    "saarika:v1", "saarika:v2", "saarika:v2.5", "saarika:flash",
    "saaras:v3", "saaras:v3-realtime", "saaras:v4", "saaras:v4-multispk",
}


def resolve_model(model: str) -> str:
    """The aishe client hardcodes its LOCAL whisper model name
    ('parakeet-tdt-0.6b-v3'), which Sarvam rejects. Only pass a model through
    when it is genuinely one of Sarvam's; otherwise use the configured default."""
    if model and model in VALID_MODELS:
        return model
    return DEFAULT_MODEL


@asynccontextmanager
async def lifespan(app: FastAPI):
    key = _api_key()
    log.info("Sarvam STT proxy starting — endpoint=%s model=%s key=%s",
             BASE_URL, DEFAULT_MODEL, "present" if key else "MISSING")
    if not key:
        log.error("No Sarvam API key: set SARVAM_API_KEY or providers.sarvam.api_key in config.yaml")
    yield


app = FastAPI(title="Aishe STT (Sarvam cloud)", version="0.1.0", lifespan=lifespan)


@app.get("/")
def root():
    return {"service": "aishe-stt-sarvam", "backend": "sarvam-cloud", "endpoint": BASE_URL,
            "model": DEFAULT_MODEL, "default_language": DEFAULT_LANG}


@app.get("/healthz")
def healthz():
    return {"status": "ok", "backend": "sarvam", "model": DEFAULT_MODEL,
            "key_present": bool(_api_key())}


@app.get("/v1/models")
def models():
    return {"object": "list",
            "data": [{"id": DEFAULT_MODEL, "object": "model", "owned_by": "sarvam"}]}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default=DEFAULT_MODEL),
    language: Optional[str] = Form(default=None),
    response_format: str = Form(default="json"),
):
    key = _api_key()
    if not key:
        raise HTTPException(status_code=503, detail="no Sarvam API key configured")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="empty file")

    # Sarvam wants a language_code; the CLI sends `language` (whisper style),
    # so map a bare "en" to the region-tagged form Sarvam expects.
    lang = language or _voice_cfg("language_code", DEFAULT_LANG)
    if lang and ":" not in lang and "-" not in lang:
        lang = {"en": "en-IN", "hi": "hi-IN", "bn": "bn-IN", "ta": "ta-IN",
                "te": "te-IN", "mr": "mr-IN", "gu": "gu-IN", "kn": "kn-IN",
                "ml": "ml-IN", "pa": "pa-IN"}.get(lang, f"{lang}-IN")

    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    t0 = time.time()
    try:
        with open(tmp_path, "rb") as fh:
            resp = requests.post(
                f"{BASE_URL}/speech-to-text",
                headers={"api-subscription-key": key},
                files={"file": (os.path.basename(tmp_path), fh, "audio/wav")},
                data={"model": resolve_model(model), "language_code": lang},
                timeout=TIMEOUT,
            )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code,
                            detail=f"sarvam error: {resp.text[:400]}")

    try:
        text = (resp.json() or {}).get("transcript", "")
    except ValueError:
        raise HTTPException(status_code=502, detail=f"bad upstream json: {resp.text[:200]}")

    elapsed = round(time.time() - t0, 3)
    used_model = resolve_model(model)
    if response_format == "text":
        return JSONResponse(content={"text": text})
    return {"text": text, "transcript": text, "language_code": lang,
            "model": used_model, "requested_model": model,
            "backend": "sarvam", "elapsed_s": elapsed}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("sarvam_stt_server:app", host=HOST, port=PORT, log_level="info")
