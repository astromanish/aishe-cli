# Aishe voice sidecar services

OpenAI-API-compatible HTTP servers for Speech-to-Text (faster-whisper) and
Text-to-Speech (Kokoro). Implemented as standalone FastAPI services that
the aishe CLI can talk to via the standard OpenAI /v1/audio/* endpoints.

## Why a sidecar?

The aishe CLI is intentionally thin — it just makes HTTP calls. Splitting
heavy ML models into separate processes keeps the CLI responsive, lets you
swap backends, and avoids model state leaking into the CLI's address space.

## Components

| File | Port | Purpose |
|------|------|---------|
| `stt_server.py` | 5093 | OpenAI `/v1/audio/transcriptions` (faster-whisper) |
| `tts_server.py` | 8766 | OpenAI `/v1/audio/speech` (Kokoro) |

## Endpoints

- `GET /` — service banner
- `GET /healthz` (STT) or `/health` (TTS) — liveness
- `POST /v1/audio/transcriptions` (STT) — multipart upload
- `POST /v1/audio/speech` (TTS) — JSON body
- `GET /v1/voices` (TTS) — list voice aliases

## Voice alias mapping (TTS)

The aishe CLI uses Supertonic-style voice names (`F1`–`F10`, `M1`–`M10`).
`tts_server.py` maps these to Kokoro's named voices:

| Alias | Kokoro voice | Notes |
|-------|--------------|-------|
| F1 | af_bella | American female, warm |
| F2 | af_nicole | American female, news |
| F3 | af_sarah | American female, calm |
| F4 (default) | af_sky | American female, neutral |
| F5 | af_nova | American female, upbeat |
| M1 | am_adam | American male, deep |
| M2 | am_michael | American male, narrator |
| M3 | am_eric | American male, casual |
| M4 | am_liam | American male, young |
| M5 | am_fenrir | American male, gruff |
| F6-F10 / M6-M10 | bf_*/bm_*/am_* | British + American alternatives |

You can also pass any raw Kokoro voice name (`af_bella`, `bm_george`, etc.) —
the server passes through unknown voices as-is.

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `AISHE_STT_HOST` | 127.0.0.1 | STT bind address |
| `AISHE_STT_PORT` | 5093 | STT port |
| `AISHE_STT_MODEL` | small | faster-whisper model: tiny/base/small/medium/large-v3 |
| `AISHE_STT_DEVICE` | cpu | cuda or cpu |
| `AISHE_STT_COMPUTE` | int8 | float16 (cuda) or int8 (cpu) |
| `AISHE_TTS_HOST` | 127.0.0.1 | TTS bind address |
| `AISHE_TTS_PORT` | 8766 | TTS port |
| `AISHE_TTS_MODEL_DIR` | ~/.local/share/aishe-cli/sidecars/models/kokoro | Where Kokoro ONNX lives |

## Running standalone

```bash
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python \
    faster-whisper "kokoro-onnx>=0.5.0" "fastapi>=0.110.0" \
    "uvicorn[standard]" python-multipart soundfile

mkdir -p models/kokoro
curl -fsSL -o models/kokoro/kokoro-v1.0.onnx \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -fsSL -o models/kokoro/voices-v1.0.bin \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

.venv/bin/python stt_server.py &  # → :5093
.venv/bin/python tts_server.py &  # → :8766
```

## Benchmarks (on 4 GB VRAM GTX 1650 / i5-8400)

- STT (`small` model, CPU, int8): 2-3s for 5s of audio
- STT (`small` model, CUDA, float16): ~0.5s for 5s of audio (requires libcublas)
- TTS (Kokoro, CPU): 3-5s for a typical sentence
- TTS first-call latency: ~5s (model load), subsequent: 1-3s
