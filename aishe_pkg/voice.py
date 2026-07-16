"""Voice I/O — STT (Parakeet) and TTS (Supertonic) integration."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import get
from .util import bold, check, cyan, dim, green, red, yellow

STT_URL = get("services.stt", "http://localhost:5093")
TTS_URL = get("services.tts", "http://localhost:8766")

_SYSTEM = platform.system()


# ─── Platform audio playback ───────────────────────────────────────────────

def _play_audio(path: str) -> None:
    """Play a WAV file using the platform's native audio player."""
    if _SYSTEM == "Darwin":
        subprocess.run(["afplay", path], capture_output=True)
    elif _SYSTEM == "Windows":
        # Use PowerShell's SoundPlayer or built-in media player
        subprocess.run(["powershell", "-c", f"(New-Object Media.SoundPlayer '{path}').PlaySync();"], capture_output=True)
    elif _SYSTEM == "Linux":
        # Try common Linux audio players
        for player in ["aplay", "paplay", "ffplay", "pw-play"]:
            if subprocess.run(["which", player], capture_output=True).returncode == 0:
                subprocess.run([player, path], capture_output=True)
                return
        # Fallback: try ffmpeg
        subprocess.run(["ffmpeg", "-i", path, "-f", "s16le", "-ar", "44100", "-ac", "1", "-"], capture_output=True)
    else:
        # Unknown OS — just log
        print(dim(f"  Audio saved to {path} (no player for {_SYSTEM})"))


# ─── STT ────────────────────────────────────────────────────────────────────

def transcribe(filepath: str) -> str:
    """Transcribe an audio file via Parakeet STT. Returns text."""
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{STT_URL}/v1/audio/transcriptions",
            files={"file": (os.path.basename(filepath), f, "audio/wav")},
            data={"model": "parakeet-tdt-0.6b-v3"},
            timeout=30,
        )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


def stt_health() -> bool:
    return check(f"{STT_URL}/healthz")


# ─── TTS ────────────────────────────────────────────────────────────────────

def synthesize(text: str, voice: str = "F4", play: bool = True) -> Optional[str]:
    """Synthesize text to speech. Returns path to audio file if saved, None if played."""
    resp = requests.post(
        f"{TTS_URL}/v1/audio/speech",
        json={
            "input": text,
            "voice": voice,
            "model": "supertonic-3",
            "response_format": "wav",
        },
        timeout=60,
    )
    resp.raise_for_status()

    outpath = f"/tmp/aishe_tts_{uuid.uuid4().hex[:8]}.wav"
    with open(outpath, "wb") as f:
        f.write(resp.content)

    if play:
        _play_audio(outpath)
        os.unlink(outpath)
        return None
    return outpath


def tts_health() -> bool:
    return check(f"{TTS_URL}/health")


def tts_info() -> str:
    """Get TTS model info."""
    try:
        r = requests.get(f"{TTS_URL}/health", timeout=3)
        j = r.json()
        return f"loaded={j.get('model_loaded', '?')}"
    except Exception:
        return ""


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


# ─── CLI handlers ───────────────────────────────────────────────────────────

def cmd_voice(args: Any) -> None:
    action = args.action

    if action == "status":
        stt_ok = stt_health()
        tts_ok = tts_health()
        print(bold("Voice Services"))
        print(f"  Parakeet STT  :5093  {green('✓ UP') if stt_ok else red('✗ DOWN')}")
        print(f"  Supertonic TTS :8766  {green('✓ UP') if tts_ok else red('✗ DOWN')}")
        return

    if action == "transcribe":
        path = args.file
        if not path or not os.path.exists(path):
            print(red(f"Audio file not found: {path}"))
            sys.exit(1)
        try:
            text = transcribe(path)
        except requests.exceptions.ConnectionError:
            print(red("Cannot reach Parakeet STT on :5093"))
            sys.exit(1)
        print(text)
        return

    if action == "speak":
        parts: List[str] = []
        if args.file:
            parts.append(args.file)
        if args.text:
            parts.extend(args.text)
        text = " ".join(parts)
        if not text:
            text = sys.stdin.read().strip()
        if not text:
            print(red("No text provided"))
            sys.exit(1)
        voice = args.voice or get("voice.default_voice", "F4")
        try:
            outpath = synthesize(text, voice=voice, play=not args.no_play)
        except requests.exceptions.ConnectionError:
            print(red("Cannot reach Supertonic TTS on :8766"))
            sys.exit(1)
        if outpath:
            print(f"{green('Synthesized')} → {outpath}")
        return
