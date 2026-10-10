"""Voice I/O — Sarvam AI (Saaras v3 STT + Bulbul v3 TTS).

Both speech endpoints run on Sarvam's cloud API using the same subscription key
as the chat provider. No local STT/TTS sidecars are required.
"""

from __future__ import annotations

import base64
import os
import platform
import re
import subprocess
import uuid
from typing import List, Optional, Tuple

import requests

from .config import get
from .util import check, dim

SARVAM_BASE = "https://api.sarvam.ai"
SARVAM_API_KEY = get("providers.sarvam.api_key", "")

# Saaras v3 output mode: transcribe (spoken language) | translate (to English)
# | verbatim | translit | codemix. See docs.sarvam.ai STT overview.
STT_MODE = get("voice.stt_mode", "transcribe")
# Config-driven so the desktop Voice section can switch it. `aishe doctor`
# verifies the configured value really works against Sarvam.
STT_MODEL = get("voice.stt_model", "saaras:v3")
STT_TIMEOUT = 30

# Bulbul v3 TTS: 11 languages, 30+ speakers. voice.default_voice maps to the
# Bulbul speaker name (e.g. shubh, priya). language_code drives prosody.
# Config-driven for the same reason as STT_MODEL.
TTS_MODEL = get("voice.tts_model", "bulbul:v3")
TTS_TIMEOUT = 60
TTS_LANGUAGE = get("voice.language_code", "en-IN")

_SYSTEM = platform.system()


# ─── Platform audio playback ───────────────────────────────────────────────

def _play_audio(path: str) -> None:
    """Play a WAV file using the platform's native audio player."""
    if _SYSTEM == "Darwin":
        subprocess.run(["afplay", path], capture_output=True)
    elif _SYSTEM == "Windows":
        subprocess.run(["powershell", "-c", f"(New-Object Media.SoundPlayer '{path}').PlaySync();"], capture_output=True)
    elif _SYSTEM == "Linux":
        for player in ["aplay", "paplay", "ffplay", "pw-play"]:
            if subprocess.run(["which", player], capture_output=True).returncode == 0:
                subprocess.run([player, path], capture_output=True)
                return
        subprocess.run(["ffmpeg", "-i", path, "-f", "s16le", "-ar", "44100", "-ac", "1", "-"], capture_output=True)
    else:
        print(dim(f"  Audio saved to {path} (no player for {_SYSTEM})"))


def _sarvam_headers() -> dict:
    """Sarvam auth header. Raises a clear error if no key is configured."""
    if not SARVAM_API_KEY:
        raise RuntimeError(
            "Sarvam API key not set — voice needs it. "
            "Set providers.sarvam.api_key in ~/.config/aishe/config.yaml "
            "(or run: aishe setup)."
        )
    return {"api-subscription-key": SARVAM_API_KEY}


# ─── STT (Sarvam Saaras v3) ────────────────────────────────────────────────

def transcribe(filepath: str) -> str:
    """Transcribe an audio file via Sarvam Saaras v3. Returns text.

    REST endpoint handles files up to 30s. PCM must be 16kHz; WAV is fine.
    """
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{SARVAM_BASE}/speech-to-text",
            headers=_sarvam_headers(),
            files={"file": (os.path.basename(filepath), f, "audio/wav")},
            data={"model": STT_MODEL, "mode": STT_MODE},
            timeout=STT_TIMEOUT,
        )
    resp.raise_for_status()
    return resp.json().get("transcript", "").strip()


def stt_health() -> bool:
    """Voice STT is ready when a Sarvam key is configured.

    Cloud endpoints have no local health endpoint, so the key is the gate.
    """
    return bool(SARVAM_API_KEY)


# ─── TTS (Sarvam Bulbul v3) ────────────────────────────────────────────────

def synthesize(text: str, voice: str = "shubh", play: bool = True) -> Optional[str]:
    """Synthesize text to speech via Sarvam Bulbul v3.

    `voice` is a Bulbul speaker name (defaults to shubh). Returns the path to
    the WAV file if play=False, else plays it and returns None.
    """
    resp = requests.post(
        f"{SARVAM_BASE}/text-to-speech",
        headers=_sarvam_headers(),
        json={
            "text": text,
            "language_code": TTS_LANGUAGE,
            "speaker": voice,
            "model": TTS_MODEL,
        },
        timeout=TTS_TIMEOUT,
    )
    resp.raise_for_status()
    audios = resp.json().get("audios") or []
    if not audios:
        raise RuntimeError("Sarvam TTS returned no audio")

    outpath = f"/tmp/aishe_tts_{uuid.uuid4().hex[:8]}.wav"
    with open(outpath, "wb") as f:
        f.write(base64.b64decode(audios[0]))

    if play:
        _play_audio(outpath)
        os.unlink(outpath)
        return None
    return outpath


# ─── Streaming (sentence-chunked) TTS ───────────────────────────────────────

_SENT_SPLIT = re.compile(r'(?<=[.!?…])\s+')
_MIN_CHUNK_CHARS = 8


def split_sentences(text: str) -> List[str]:
    """Split text into sentence chunks suitable for incremental TTS.

    Keeps trailing punctuation attached to its sentence and drops empty chunks.
    Long run-on segments (no sentence punctuation, e.g. lists/code) are still
    broken up by commas or at a hard length cap so we never wait too long.
    """
    parts = _SENT_SPLIT.split(text.strip())
    chunks: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Break any remaining over-long fragment without sentence punctuation
        while len(p) > 160:
            cut = -1
            for sep in (",", ";", "—", "-", " ", " "):
                cut = p.rfind(sep, 0, 161)
                if cut >= 80:
                    break
                cut = -1
            if cut == -1:
                cut = 160
            chunks.append(p[:cut + 1].strip())
            p = p[cut + 1:].strip()
        chunks.append(p)
    return chunks


def speak_stream(text: str, voice: str = "shubh") -> None:
    """Synthesize + play a full response, but speak it sentence-by-sentence.

    This lowers time-to-first-audio vs. waiting for the whole blob, and lets
    a caller stream tokens and speak each completed sentence as it arrives.
    For a single-shot string, it still chunks so long replies start sooner.

    Note: Bulbul v3 accepts long-form text in one request, so chunking is now
    purely for perceived latency, not an API requirement.
    """
    for chunk in split_sentences(text):
        if not chunk:
            continue
        synthesize(chunk, voice=voice, play=True)



def tts_health() -> bool:
    """Voice TTS is ready when a Sarvam key is configured (cloud, no local health)."""
    return bool(SARVAM_API_KEY)


def tts_info() -> str:
    """TTS model info — which Sarvam voice/speaker is in use."""
    return f"bulbul:v3/{get('voice.default_voice', 'shubh')}"


# ─── Mic recording ──────────────────────────────────────────────────────────

def list_mic_devices() -> List[Tuple[int, str]]:
    """List available microphone devices via ffmpeg."""
    try:
        if _SYSTEM == "Darwin":
            r = subprocess.run(
                ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                capture_output=True, text=True, timeout=5,
            )
            devices: List[Tuple[int, str]] = []
            in_audio = False
            for line in (r.stdout + r.stderr).splitlines():
                if "AVFoundation audio devices:" in line:
                    in_audio = True
                    continue
                if in_audio and line.strip().startswith("["):
                    m = re.search(r'\[(\d+)\]\s*(.+)', line.strip())
                    if m:
                        devices.append((int(m.group(1)), m.group(2).strip()))
            return devices
        elif _SYSTEM == "Linux":
            # Linux: list ALSA/PulseAudio devices
            r = subprocess.run(
                ["ffmpeg", "-f", "alsa", "-list_devices", "true", "-i", ""],
                capture_output=True, text=True, timeout=5,
            )
            devices = []
            for line in (r.stdout + r.stderr).splitlines():
                m = re.search(r'\[(\d+)\]\s*(.+)', line.strip())
                if m:
                    devices.append((int(m.group(1)), m.group(2).strip()))
            if not devices:
                # Try PulseAudio
                r2 = subprocess.run(
                    ["ffmpeg", "-f", "pulse", "-list_devices", "true", "-i", ""],
                    capture_output=True, text=True, timeout=5,
                )
                for line in (r2.stdout + r2.stderr).splitlines():
                    m = re.search(r'\[(\d+)\]\s*(.+)', line.strip())
                    if m:
                        devices.append((int(m.group(1)), m.group(2).strip()))
            return devices
        elif _SYSTEM == "Windows":
            # Windows: list DirectShow devices
            r = subprocess.run(
                ["ffmpeg", "-f", "dshow", "-list_devices", "true", "-i", ""],
                capture_output=True, text=True, timeout=5,
            )
            devices = []
            in_audio = False
            for line in (r.stdout + r.stderr).splitlines():
                if "DirectShow audio devices" in line or "Audio devices" in line:
                    in_audio = True
                    continue
                if in_audio and '"' in line:
                    m = re.search(r'"([^"]+)"', line)
                    if m:
                        devices.append((len(devices), m.group(1)))
            return devices
        else:
            return []
    except Exception:
        return []


def record_audio(duration: int, device_index: int, outpath: str) -> bool:
    """Record audio from mic via ffmpeg."""
    if _SYSTEM == "Darwin":
        input_spec = f":{device_index}"
        fmt = "avfoundation"
    elif _SYSTEM == "Linux":
        input_spec = f"default:{device_index}"
        fmt = "pulse"
    elif _SYSTEM == "Windows":
        input_spec = f"audio={device_index}"
        fmt = "dshow"
    else:
        return False

    cmd = [
        "ffmpeg", "-y",
        "-f", fmt,
        "-i", input_spec,
        "-t", str(duration),
        "-ar", "16000",
        "-ac", "1",
        outpath,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 5)
    return r.returncode == 0


# ─── VAD-based recording ───────────────────────────────────────────────────

def _vad_available() -> bool:
    """Check if webrtcvad is installed."""
    try:
        import webrtcvad  # type: ignore
        return True
    except ImportError:
        return False


def record_with_vad(
    device_index: int,
    outpath: str,
    threshold: float = 0.5,
    min_speech_ms: int = 250,
    min_silence_ms: int = 500,
    max_duration: int = 30,
) -> bool:
    """Record audio with voice activity detection.

    Uses webrtcvad to detect when the user starts and stops speaking.
    Falls back to fixed-duration recording if webrtcvad is not installed.
    """
    if not _vad_available():
        return record_audio(5, device_index, outpath)

    import webrtcvad  # type: ignore

    import struct
    import time

    vad = webrtcvad.Vad(2)
    sample_rate = 16000
    frame_duration_ms = 30
    frame_size = int(sample_rate * frame_duration_ms / 1000) * 2

    # Determine ffmpeg input format based on platform
    if _SYSTEM == "Darwin":
        fmt = "avfoundation"
        input_spec = f":{device_index}"
    elif _SYSTEM == "Linux":
        fmt = "pulse"
        input_spec = f"default:{device_index}"
    elif _SYSTEM == "Windows":
        fmt = "dshow"
        input_spec = f"audio={device_index}"
    else:
        return False

    cmd = [
        "ffmpeg", "-y",
        "-f", fmt,
        "-i", input_spec,
        "-f", "s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-t", str(max_duration),
        "pipe:1",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    speech_frames = 0
    silence_frames = 0
    speech_detected = False
    recording = False
    audio_chunks: List[bytes] = []

    frames_per_speech_ms = min_speech_ms // frame_duration_ms
    frames_per_silence_ms = min_silence_ms // frame_duration_ms

    start_time = time.time()

    while True:
        raw = proc.stdout.read(frame_size) if proc.stdout else b""
        if not raw or len(raw) < frame_size:
            break

        is_speech = vad.is_speech(raw, sample_rate)

        if is_speech:
            speech_frames += 1
            silence_frames = 0
            if not recording and speech_frames >= frames_per_speech_ms:
                recording = True
                speech_detected = True
                audio_chunks = []
        else:
            silence_frames += 1
            if recording and silence_frames >= frames_per_silence_ms:
                break

        if recording:
            audio_chunks.append(raw)

        if time.time() - start_time > max_duration:
            break

    proc.terminate()

    if not speech_detected or not audio_chunks:
        return False

    import wave
    with wave.open(outpath, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(audio_chunks))

    return True
