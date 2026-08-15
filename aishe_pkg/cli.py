"""Aishe CLI — main entry point and command dispatch."""

from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from . import __version__
from .config import cmd_config, get, get_config_path, load
from .memory import cmd_memory, count as mem_count
from .threads import add_message as thr_add_message, count as thr_count
from .util import (
    bold, check, cyan, dim, green, red, yellow,
    header, section, bullet, status_dot, key_value,
)
from .voice import (
    list_mic_devices,
    record_audio,
    record_with_vad,
    speak_stream,
    split_sentences,
    stt_health,
    synthesize,
    transcribe,
    tts_health,
    tts_info,
)
from .dashboard import cmd_dashboard

DEEPAGENT_URL = get("services.deepagent", "http://localhost:8765")


def _stream_once(msg: str, tid: str) -> None:
    """Send one message and stream the reply. Persists both turns."""
    if not msg:
        print(red("No message provided"))
        sys.exit(1)

    # Ensure thread exists and save user message
    thr_add_message(tid, "user", msg)

    # A dropped chunked stream mid-response (server restart, provider hiccup,
    # timeout) used to crash with an unhandled ChunkedEncodingError. Retry only
    # when nothing has been received yet; keep a partial answer otherwise.
    MAX_ATTEMPTS = 3

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = requests.post(
                f"{DEEPAGENT_URL}/stream",
                json={"message": msg, "thread_id": tid},
                stream=True,
                timeout=120,
            )
            r.raise_for_status()
        except requests.exceptions.ConnectionError:
            print(red("Cannot reach DeepAgent on :8765."))
            sys.exit(1)

        answer = ""
        saw_event = False
        try:
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("event", "")
                if etype == "token":
                    tok = ev.get("content", "")
                    print(tok, end="", flush=True)
                    answer += tok
                    saw_event = True
                elif etype == "tool_call":
                    print(yellow(f"\n  [tool: {ev.get('name', '?')}]"), flush=True)
                    saw_event = True
                elif etype == "tool_result":
                    print(dim(f"  [result: {ev.get('result', '')[:100]}]\n"), flush=True)
                    saw_event = True
                elif etype == "error":
                    print(red(f"\n  [stream error: {ev.get('message', 'unknown')}]"), flush=True)
                elif etype == "final":
                    if not answer:
                        answer = ev.get("answer", "")
            break  # clean end of stream
        except requests.exceptions.ChunkedEncodingError:
            # Server closed the chunked stream before sending a final event.
            if saw_event and answer:
                print(red("\n  [connection dropped — reply may be incomplete]"), flush=True)
                break
            if attempt < MAX_ATTEMPTS:
                print(dim(f"\n  [connection dropped, retrying ({attempt}/{MAX_ATTEMPTS - 1})…]"), flush=True)
                continue
            print(red("\n  [connection dropped — no reply received]"), flush=True)
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
            if attempt < MAX_ATTEMPTS:
                print(dim(f"\n  [connection lost, retrying ({attempt}/{MAX_ATTEMPTS - 1})…]"), flush=True)
                continue
            print(red("\n  [connection lost — no reply received]"), flush=True)
            break

    if not answer:
        print(dim("(empty response)"))
    else:
        thr_add_message(tid, "assistant", answer)
    print()


# ─── Bare `aishe` — one-shot or interactive streaming ──────────────────────

def cmd_repl_stream(args: Any) -> None:
    """Bare `aishe`: with a message → one-shot stream; without → interactive loop."""
    if not check(f"{DEEPAGENT_URL}/health"):
        print(red("DeepAgent is down on :8765"))
        sys.exit(1)

    tid = getattr(args, "thread", None) or "cli"

    # One-shot: message provided as arg, or piped via stdin (non-TTY)
    msg = " ".join(args.message) if isinstance(getattr(args, "message", None), list) else getattr(args, "message", "")
    if not msg and not sys.stdin.isatty():
        msg = sys.stdin.read().strip()
    if msg:
        _stream_once(msg, tid)
        return

    # Interactive loop
    print(bold("💬 Aishe — streaming chat"))
    print(dim("Type a message and press Enter. Ctrl+C or /exit to quit."))
    print(dim("─" * 50))

    while True:
        try:
            user_input = input(bold("\nYou ▶ ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{dim('Goodbye!')}")
            break

        if not user_input:
            continue
        if user_input.lower() in ("/exit", "/quit"):
            print(dim("Goodbye!"))
            break

        _stream_once(user_input, tid)
        print(dim("─" * 50))


# ─── Live Voice ──────────────────────────────────────────────────────────────

def cmd_live(args: Any) -> None:
    if not stt_health():
        print(red("Parakeet STT is down on :5093"))
        sys.exit(1)
    if not tts_health():
        print(red("Supertonic TTS is down on :8766"))
        sys.exit(1)
    if not check(f"{DEEPAGENT_URL}/health"):
        print(red("DeepAgent is down on :8765"))
        sys.exit(1)

    devices = list_mic_devices()
    if not devices:
        print(red("No microphone devices found"))
        sys.exit(1)

    device_idx = args.device
    if device_idx is None:
        for idx, name in devices:
            if "macbook" in name.lower() or "built-in" in name.lower() or "microphone" in name.lower():
                device_idx = idx
                break
        if device_idx is None:
            device_idx = devices[0][0]

    vad_enabled = get("voice.vad_enabled", True) and not args.no_vad
    duration = args.duration or get("voice.recording_duration", 5)
    voice = args.voice or get("voice.default_voice", "F4")

    print(bold("🎤 Live Voice Mode"))
    print(f"  Mic: {cyan(f'[{device_idx}]')} {dict(devices).get(device_idx, '?')}")
    print(f"  STT: {green('Parakeet :5093')}  TTS: {green('Supertonic :8766')}  LLM: {green('DeepAgent :8765')}")
    print(f"  Voice: {cyan(voice)}  VAD: {cyan('on' if vad_enabled else 'off')}  Thread: {args.thread}")
    print()
    print(dim("Press Enter to record and speak. Ctrl+C to exit."))
    print("─" * 50)

    tid = args.thread or "live"
    consecutive_errors = 0

    while True:
        try:
            user_input = input(bold("\nYou ▶ ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{dim('Goodbye!')}")
            break

        if not user_input:
            print(dim("  🔴 Recording..."), end="", flush=True)
            recpath = f"/tmp/aishe_live_rec_{datetime.now().strftime('%H%M%S%f')}.wav"

            if vad_enabled:
                ok = record_with_vad(
                    device_idx, recpath,
                    threshold=get("voice.vad_threshold", 0.5),
                    min_speech_ms=get("voice.vad_min_speech_duration_ms", 250),
                    min_silence_ms=get("voice.vad_min_silence_duration_ms", 500),
                )
            else:
                ok = record_audio(duration, device_idx, recpath)

            if not ok:
                print(f"\r  {red('No speech detected')}                    ")
                continue

            print(f"\r  {green('● Recorded')} → transcribing...", end="", flush=True)
            try:
                user_text = transcribe(recpath)
            except Exception as e:
                print(f"\r  {red(f'STT error: {e}')}                    ")
                if os.path.exists(recpath):
                    os.unlink(recpath)
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    print(red("Too many STT errors. Check Parakeet server."))
                    break
                continue
            finally:
                if os.path.exists(recpath):
                    os.unlink(recpath)

            if not user_text:
                print(f"\r  {yellow('(empty transcription)')}                    ")
                continue

            print(f"\r  {green('>')} {green(user_text)}                    ")
            consecutive_errors = 0
        else:
            user_text = user_input
            print(f"\n{green('>')} {green(user_input)}")

        # Persist user turn
        thr_add_message(tid, "user", user_text)

        try:
            r = requests.post(
                f"{DEEPAGENT_URL}/stream",
                json={"message": user_text, "thread_id": tid},
                stream=True,
                timeout=120,
            )
            r.raise_for_status()
        except Exception as e:
            print(f"\n  {red(f'DeepAgent error: {e}')}")
            continue

        answer = ""
        tool_calls: List[str] = []
        in_tool = False
        spoken = set()

        # ─── Voice-only response ───────────────────────────────────────────
        # No chat text on screen. Tokens accumulate into a sentence buffer;
        # each completed sentence is pushed to a single FIFO playback queue.
        # ONE consumer thread drains it serially — sentences play one at a
        # time, never overlapping. TTS still overlaps with the LLM streaming.
        speak_voice = voice if (not args.no_tts) else None

        audio_q: "queue.Queue[str]" = queue.Queue()

        def _speak(sentence: str) -> None:
            audio_q.put(sentence)

        def _player() -> None:
            # single consumer: pop one sentence, speak it fully, then the next
            while True:
                sentence = audio_q.get()
                if sentence is None:
                    audio_q.task_done()
                    break
                try:
                    speak_stream(sentence, voice=voice)
                except Exception:
                    pass
                finally:
                    audio_q.task_done()

        if speak_voice is not None:
            threading.Thread(target=_player, daemon=True).start()

        buf = ""
        queued = 0
        for line in r.iter_lines():
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = ev.get("event", "")
            if etype == "tool_call":
                tool_calls.append(ev.get("name", "?"))
                in_tool = True
            elif etype == "tool_result":
                in_tool = True
            elif etype == "token":
                tok = ev.get("content", "")
                if not tok:
                    continue
                answer += tok
                # live progress indicator
                print(f"\r  {dim('▸ thinking')} {green(bold(str(len(answer))))} chars", end="", flush=True)
                if speak_voice is None:
                    continue
                buf += tok
                # Flush completed sentences. Guard: skip the final chunk so we
                # don't double-speak it, and never buffer forever.
                if len(buf) >= 300:
                    _speak(buf)
                    queued += 1
                    buf = ""
                else:
                    for s in split_sentences(buf):
                        if s in spoken:
                            continue
                        if buf.endswith(s):
                            break  # incomplete — still buffering
                        spoken.add(s)
                        _speak(s)
                        queued += 1
                        buf = buf[len(s):]
            elif etype == "final":
                if not answer:
                    answer = ev.get("answer", "")

        # Speak any remaining tail (final partial sentence) if not already spoken
        if speak_voice is not None:
            for s in split_sentences(buf):
                if s and s not in spoken:
                    _speak(s)
                    queued += 1
            # if the model's answer arrived only via `final` (no tokens),
            # speak the whole thing once
            if not spoken and answer.strip():
                _speak(answer)
                queued += 1

        # Persist assistant turn
        if answer:
            thr_add_message(tid, "assistant", answer)

        # final progress indicator (thinking → speaking), clear the line
        if speak_voice is not None:
            audio_q.put(None)  # graceful stop signal for the player
            print(f"\r  {dim('▸ speaking')} {green(bold(str(queued)))} phrases{' ' * 20}", end="", flush=True)
            audio_q.join()     # wait for the queue to finish playing (silent drain)
            print(f"\r  {green('✓')} replied — {green(bold(str(queued)))} phrases{' ' * 20}")
        else:
            print(f"\r  {green('✓')} replied — {dim(str(len(answer)) + ' chars')}{' ' * 20}")


# ─── Doctor ─────────────────────────────────────────────────────────────────

def cmd_doctor(args: Any) -> None:
    """Comprehensive diagnostics — check all services, test roundtrips."""
    header("Aishe Diagnostics", "Comprehensive system check")

    # 1. Python environment
    section("Environment")
    print(f"  {key_value('Python', sys.version.split()[0])}")
    print(f"  {key_value('Platform', sys.platform)}")
    print(f"  {key_value('Version', __version__)}")
    print(f"  {key_value('Config', str(get_config_path()))}")

    # 2. Dependencies
    section("Dependencies")
    deps = {
        "requests": False,
        "yaml (PyYAML)": False,
        "webrtcvad": False,
    }
    try:
        import requests as _  # noqa
        deps["requests"] = True
    except ImportError:
        pass
    try:
        import yaml as _  # noqa
        deps["yaml (PyYAML)"] = True
    except ImportError:
        pass
    try:
        import webrtcvad as _  # noqa
        deps["webrtcvad"] = True
    except ImportError:
        pass

    for dep, ok in deps.items():
        print(f"  {status_dot(ok)} {dep:20s} {green('installed') if ok else red('missing')}")

    # 3. External tools
    section("External Tools")
    audio_players = {
        "Darwin": "afplay",
        "Windows": "powershell",
        "Linux": "aplay",
    }
    audio_tool = audio_players.get(platform.system(), "aplay")
    for tool in ["ffmpeg", audio_tool, "ollama"]:
        which = shutil.which(tool)
        print(f"  {status_dot(which is not None)} {tool:20s} {green(which) if which else red('not found')}")

    # 4. Services
    section("Services")
    services = [
        ("DeepAgent", get("services.deepagent", ":8765"), "/health"),
        ("Ollama", get("services.ollama", ":11434"), "/api/tags"),
        ("Parakeet STT", get("services.stt", ":5093"), "/healthz"),
        ("Supertonic TTS", get("services.tts", ":8766"), "/health"),
    ]
    for name, base, endpoint in services:
        url = f"{base}{endpoint}"
        ok = check(url)
        extra = ""
        if ok and name == "Ollama":
            try:
                r = requests.get(f"{base}/api/tags", timeout=3)
                models = r.json().get("models", [])
                extra = f" ({len(models)} models)"
            except Exception:
                pass
        elif ok and name == "Supertonic TTS":
            extra = f" ({tts_info()})"
        print(f"  {status_dot(ok)} {name:20s} {green('UP') if ok else red('DOWN')}{dim(extra)}")

    # 5. Data
    section("Data")
    mem_cnt = mem_count()
    thr_cnt = thr_count()
    print(f"  {bullet('Memory entries')} {cyan(str(mem_cnt))}")
    print(f"  {bullet('Threads')} {cyan(str(thr_cnt))}")

    # 6. STT roundtrip test
    section("STT Roundtrip")
    if stt_health():
        test_wav = "/tmp/aishe_test_stt.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
             "-ar", "16000", "-ac", "1", test_wav],
            capture_output=True, timeout=5,
        )
        try:
            text = transcribe(test_wav)
            preview = text[:60]
            print(f"  {status_dot(True)} {green('STT responds')} {dim('(got: ' + repr(preview) + ')')}")
        except Exception as e:
            print(f"  {status_dot(False)} {red(f'STT error: {e}')}")
        finally:
            if os.path.exists(test_wav):
                os.unlink(test_wav)
    else:
        print(f"  {yellow('⚠ Skipped (STT down)')}")

    # 7. TTS roundtrip test
    section("TTS Roundtrip")
    if tts_health():
        try:
            out = synthesize("test", play=False)
            if out and os.path.exists(out):
                size = os.path.getsize(out)
                os.unlink(out)
                print(f"  {status_dot(True)} {green('TTS responds')} {dim(f'({size} bytes)')}")
            else:
                print(f"  {status_dot(True)} {green('TTS responds')}")
        except Exception as e:
            print(f"  {status_dot(False)} {red(f'TTS error: {e}')}")
    else:
        print(f"  {yellow('⚠ Skipped (TTS down)')}")

    # 8. DeepAgent roundtrip
    section("DeepAgent Roundtrip")
    if check(f"{DEEPAGENT_URL}/health"):
        try:
            r = requests.post(
                f"{DEEPAGENT_URL}/invoke",
                json={"message": "Say 'ok'", "thread_id": "aishe_doctor"},
                timeout=30,
            )
            r.raise_for_status()
            answer = r.json().get("answer", "")
            preview = answer[:60]
            print(f"  {status_dot(True)} {green('DeepAgent responds')} {dim('(answer: ' + repr(preview) + ')')}")
        except Exception as e:
            print(f"  {status_dot(False)} {red(f'DeepAgent error: {e}')}")
    else:
        print(f"  {yellow('⚠ Skipped (DeepAgent down)')}")

    print()
    print(f"  {green(bold('✓ Diagnostics complete'))}")


# ─── Version ────────────────────────────────────────────────────────────────

def cmd_version(args: Any) -> None:
    """Show version information."""
    print(f"Aishe CLI v{__version__}")
    print(dim(f"Python {sys.version.split()[0]} on {sys.platform}"))
    # Try to get git hash
    try:
        git_dir = Path(__file__).resolve().parent.parent / ".git"
        if git_dir.exists():
            r = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=2,
                cwd=str(git_dir.parent),
            )
            if r.returncode == 0:
                print(dim(f"Commit: {r.stdout.strip()}"))
    except Exception:
        pass


# ─── Setup ──────────────────────────────────────────────────────────────────

# Provider registry: config key → display info.
#   - sarvam       Sarvam cloud API (OpenAI-compatible)
#   - ollama-cloud Ollama :cloud models via the local Ollama server
#   - local        llama.cpp llama-server (GGUF, OpenAI-compatible)
#   - openrouter   OpenRouter (OpenAI-compatible)
#   - opencode-go  OpenCode relay (OpenAI-compatible)
SETUP_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "sarvam": {
        "label": "Sarvam (cloud API)",
        "base_url": "https://api.sarvam.ai/v1",
        "needs_key": True,
        "key_hint": "Get a key from https://dashboard.sarvam.ai (Settings → API Keys)",
        "models_url": "/v1/models",
        # Real Sarvam model IDs (fetched live when possible). The -conversations
        # variant returns content directly; the base model is a reasoning model
        # that puts output in reasoning_content and breaks OpenAI chat clients.
        "fallback_models": ["sarvam-105b-conversations", "sarvam-105b"],
    },
    "ollama-cloud": {
        "label": "Ollama cloud (:cloud models via local Ollama)",
        "base_url": "http://localhost:11434",
        "needs_key": False,
        "key_default": "ollama",
        "models_url": "/api/tags",
        "ollama": True,
    },
    "local": {
        "label": "Local llama.cpp (GGUF via llama-server)",
        "base_url": "http://localhost:8080",
        "needs_key": False,
        "models_url": "/v1/models",
        "llamacpp": True,
    },
    "openrouter": {
        "label": "OpenRouter (cloud API)",
        "base_url": "https://openrouter.ai/api/v1",
        "needs_key": True,
        "key_hint": "Get a key from https://openrouter.ai/keys",
        "models_url": "/models",
    },
    "opencode-go": {
        "label": "OpenCode (opencode.ai relay)",
        "base_url": "https://opencode.ai/zen/go/v1",
        "needs_key": True,
        "key_hint": "Get a key from https://opencode.ai",
        "models_url": "/models",
    },
}

_PROVIDER_ORDER = ["sarvam", "ollama-cloud", "local", "openrouter", "opencode-go"]


def _setup_input(prompt: str) -> str:
    """input() wrapper that exits cleanly on Ctrl+C/EOF."""
    try:
        return input(bold(prompt)).strip()
    except (EOFError, KeyboardInterrupt):
        print(red("\n  Setup cancelled."))
        sys.exit(1)


def _ensure_api_key(provider: str, info: Dict[str, Any]) -> str:
    """Ensure an API key exists for a cloud provider; return the key in use."""
    from .config import get as cfg_get, set_key as cfg_set

    key = cfg_get(f"providers.{provider}.api_key", "")
    if not info.get("needs_key"):
        return key or info.get("key_default", "")
    if key:
        masked = (key[:4] + "…" + key[-4:]) if len(key) > 10 else "set"
        print(f"  {green('✓')} API key already set: {dim(masked)}")
        resp = _setup_input("  Change API key? (leave blank to keep) ▶ ")
        if resp:
            key = resp
            cfg_set(f"providers.{provider}.api_key", key)
            print(f"  {green('✓')} API key updated.")
        return key
    print(f"  {info.get('key_hint', 'Enter your API key')}")
    key = _setup_input("  API key ▶ ")
    if not key:
        print(red("  API key required for this provider."))
        sys.exit(1)
    cfg_set(f"providers.{provider}.api_key", key)
    print(f"  {green('✓')} API key saved.")
    return key


def _fetch_models(provider: str, info: Dict[str, Any], prov_cfg: Dict[str, Any]) -> List[str]:
    """Fetch model IDs for a provider. Returns [] on any failure."""
    base = (prov_cfg.get("base_url") or info["base_url"]).rstrip("/")
    key = prov_cfg.get("api_key", "") or info.get("key_default", "")
    try:
        if info.get("ollama"):
            r = requests.get(f"{base}/api/tags", timeout=6)
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []))
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        url = f"{base}{info.get('models_url', '/models')}"
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        data = r.json().get("data", [])
        ids = [m.get("id", "") for m in data if m.get("id")]
        return sorted(set(ids))
    except Exception:
        return []


def _find_ggufs() -> List[str]:
    """Find .gguf files in common model directories (bounded)."""
    dirs = [
        Path.home() / "models",
        Path.home() / "Downloads",
        Path.home() / "llama.cpp" / "models",
        Path.home() / ".cache" / "llama.cpp",
        Path("/opt/local/models"),
        Path("/usr/local/models"),
    ]
    found: List[str] = []
    for d in dirs:
        if not d.is_dir():
            continue
        try:
            for p in d.rglob("*.gguf"):
                found.append(str(p))
                if len(found) >= 15:
                    break
        except Exception:
            continue
        if len(found) >= 15:
            break
    return found


def _find_llama_server() -> Optional[str]:
    exe = shutil.which("llama-server")
    if exe:
        return exe
    for p in [
        "/opt/homebrew/bin/llama-server",
        "/usr/local/bin/llama-server",
        str(Path.home() / "llama.cpp" / "build" / "bin" / "llama-server"),
    ]:
        if os.path.exists(p):
            return p
    return None


def _setup_models() -> None:
    """Provider → API key → model selection, then save + restart DeepAgent."""
    from .config import get as cfg_get, set_key as cfg_set

    current_provider = cfg_get("provider", "") or ""
    current_model = cfg_get("model", "deepseek-v4-flash")

    header("Aishe Setup", "Choose a provider")

    # 1. Provider
    section("Provider")
    for i, name in enumerate(_PROVIDER_ORDER, 1):
        info = SETUP_PROVIDERS[name]
        marker = "●" if name == current_provider else "○"
        print(f"  {marker} [{i}] {cyan(info['label'])}")
    print(dim(f"\n  Current provider: {current_provider or 'unset'} · model: {current_model}"))

    choice = _setup_input("  Provider ▶ ")
    if not choice.isdigit() or not (1 <= int(choice) <= len(_PROVIDER_ORDER)):
        print(red("  Invalid choice."))
        sys.exit(1)
    provider = _PROVIDER_ORDER[int(choice) - 1]
    info = SETUP_PROVIDERS[provider]

    # 2. API key (cloud providers)
    section("API Key")
    api_key = _ensure_api_key(provider, info)

    # 3. Models
    section("Available Models")
    prov_cfg = dict(cfg_get(f"providers.{provider}", {}) or {})
    prov_cfg.setdefault("base_url", info["base_url"])
    prov_cfg.setdefault("api_key", api_key)

    models: List[str] = []

    if info.get("llamacpp"):
        # llama.cpp: list the loaded model + GGUF files on disk
        loaded = _fetch_models(provider, info, prov_cfg)
        if loaded:
            print(f"  Loaded in llama-server: {cyan(loaded[0])}")
        ggufs = _find_ggufs()
        if not ggufs:
            print(dim("  No .gguf files found in common model dirs."))
            gguf = _setup_input("  GGUF path (e.g. ~/models/qwen2.5-7b-q4.gguf) ▶ ")
            if gguf:
                models = [os.path.expanduser(gguf)]
        else:
            print("  GGUF files found:")
            for i, g in enumerate(ggufs, 1):
                print(f"    [{i}] {dim(os.path.basename(g))}  ({os.path.dirname(g)})")
            gguf_choice = _setup_input("  Pick a GGUF, or paste a path ▶ ")
            if gguf_choice.isdigit() and 1 <= int(gguf_choice) <= len(ggufs):
                models = [ggufs[int(gguf_choice) - 1]]
            elif gguf_choice:
                models = [os.path.expanduser(gguf_choice.strip())]
    else:
        models = _fetch_models(provider, info, prov_cfg)
        if models:
            # Cloud providers can list hundreds (OpenRouter) — show a capped list,
            # but any id can still be typed directly.
            shown = models if len(models) <= 40 else models[:40]
            for i, m in enumerate(shown, 1):
                marker = "●" if m == current_model else "○"
                print(f"  {marker} [{i}] {cyan(m)}")
            if len(models) > 40:
                print(dim(f"  … and {len(models) - 40} more — you can type any model id."))
        else:
            print(dim("  Could not fetch models — enter a model id manually."))

    # 4. Select model
    section("Select Model")
    if models:
        sel = _setup_input("  Model ▶ ")
        if sel.isdigit() and 1 <= int(sel) <= len(models):
            selected = models[int(sel) - 1]
        elif sel:
            selected = sel
        else:
            print(red("  No model chosen."))
            sys.exit(1)
    else:
        # Manual entry for unreachable servers / typed ids
        selected = _setup_input("  Model id (e.g. deepseek/deepseek-chat) ▶ ")
        if not selected:
            print(red("  No model chosen."))
            sys.exit(1)

    # 5. Save
    section("Save")
    cfg_set("provider", provider)
    cfg_set("model", selected)
    cfg_set(f"providers.{provider}.base_url", prov_cfg["base_url"])
    cfg_set(f"providers.{provider}.api_key", api_key)
    print(f"  {green('✓')} provider = {cyan(provider)}")
    print(f"  {green('✓')} model    = {cyan(selected)}")

    # 6. Local llama.cpp — start/restart llama-server if needed
    if info.get("llamacpp") and models:
        server_bin = _find_llama_server()
        if server_bin:
            base = prov_cfg["base_url"].rstrip("/")
            port = base.rsplit(":", 1)[-1] if ":" in base else "8080"
            section("llama-server")
            print(f"  Starting {cyan(server_bin)} with {dim(os.path.basename(selected))}…")
            try:
                lsof = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True, timeout=5)
                for pid in lsof.stdout.split():
                    subprocess.run(["kill", pid], check=False)
            except Exception:
                pass
            env = dict(os.environ)
            env["LLAMA_SERVER_PORT"] = port
            subprocess.Popen(
                [server_bin, "-m", selected, "-c", "4096", "--port", port, "--host", "127.0.0.1"],
                stdout=open("/tmp/llamaserver.log", "a"),
                stderr=subprocess.STDOUT,
                env=env,
            )
            print(f"  {green('✓')} llama-server starting on :{port} (log: /tmp/llamaserver.log)")
        else:
            print(yellow("  llama-server binary not found — start it yourself, e.g.:"))
            print(dim(f"    llama-server -m {selected} -c 4096 --port 8080"))

    # 7. Restart DeepAgent to apply the provider + model
    _restart_deepagent(provider, selected)

    print()
    print(f"  {green(bold('✓ Setup complete'))}  {cyan(provider)} / {cyan(selected)}")


def _deepagent_env(provider: str, model: str, prov_cfg: Dict[str, Any]) -> Dict[str, str]:
    """Build env vars for the DeepAgent sidecar from a provider config."""
    from .config import get as cfg_get

    env = dict(os.environ)
    env["AISHE_MODEL"] = model
    base = (prov_cfg.get("base_url") or SETUP_PROVIDERS[provider]["base_url"]).rstrip("/")
    api_key = prov_cfg.get("api_key", "") or SETUP_PROVIDERS[provider].get("key_default", "")
    if provider == "ollama-cloud":
        env["AISHE_BASE_URL"] = f"{base}/v1"
        env["AISHE_API_KEY"] = api_key or "ollama"
    elif provider == "local":
        env["AISHE_BASE_URL"] = f"{base}/v1"
        env["AISHE_API_KEY"] = api_key or ""
    else:
        # OpenAI-compatible cloud providers: the SDK appends /chat/completions,
        # so the base must end in /v1 (Sarvam needs it added explicitly).
        env["AISHE_BASE_URL"] = base if base.endswith("/v1") else f"{base}/v1"
        env["AISHE_API_KEY"] = api_key
    # Backward compat for anything still reading the old variable
    env["AISHE_OLLAMA_URL"] = env["AISHE_BASE_URL"]
    # Embeddings always run on the local Ollama server, whatever the chat provider
    env["AISHE_EMBED_URL"] = cfg_get("services.ollama", "http://localhost:11434")
    return env


def _restart_deepagent(provider: str, model: str) -> None:
    """Stop and relaunch the DeepAgent sidecar with the new provider/model."""
    from .config import get as cfg_get
    from .util import check as util_check

    section("Restart DeepAgent")
    print("  Restarting DeepAgent to apply the model...")
    deepagent_dir = os.path.expanduser("~/.local/share/aishe-cli/deepagent")
    server_py = os.path.join(deepagent_dir, "server.py")
    venv_py = os.path.join(deepagent_dir, ".venv", "bin", "python")

    if os.path.exists(server_py) and os.path.exists(venv_py):
        # Stop the running DeepAgent by matching its working directory (avoids
        # killing the STT sidecar, whose cmdline is also `server.py`).
        try:
            lsof = subprocess.run(
                ["lsof", "-t", "+d", deepagent_dir],
                capture_output=True, text=True, timeout=5,
            )
            pids = [p for p in lsof.stdout.split() if p]
            for pid in pids:
                cmd = subprocess.run(
                    ["ps", "-p", pid, "-o", "command="],
                    capture_output=True, text=True,
                ).stdout
                if "server.py" in cmd:
                    subprocess.run(["kill", pid], check=False)
        except Exception:
            pass

        prov_cfg = dict(cfg_get(f"providers.{provider}", {}) or {})
        env = _deepagent_env(provider, model, prov_cfg)
        subprocess.Popen(
            [venv_py, server_py],
            stdout=open("/tmp/deepagent.log", "a"),
            stderr=subprocess.STDOUT,
            cwd=deepagent_dir,
            env=env,
        )
        for _ in range(20):
            if util_check(f"{cfg_get('services.deepagent', 'http://localhost:8765')}/health"):
                break
            import time as _t
            _t.sleep(1)
        print(f"  {green('✓')} DeepAgent restarted with {cyan(provider)} / {cyan(model)}")
    else:
        print(yellow("  DeepAgent sidecar not found — config saved, will apply on next start."))


def cmd_setup(args: Any) -> None:
    """Interactive setup — configure models (provider/key/model) or Telegram bridge."""
    header("Aishe Setup", "Configure model or Telegram")
    section("What do you want to set up?")
    print("  [1] Models — provider, API key, model selection")
    print("  [2] Telegram bridge — bot token + allowed users")
    choice = _setup_input("\n  Choice ▶ ")
    if choice.strip() == "2":
        _setup_telegram()
        print(f"\n  {green(bold('✓ Telegram setup complete'))}")
        return
    _setup_models()


# ─── Telegram bridge ───────────────────────────────────────────────────────

def _setup_telegram() -> None:
    """Interactive Telegram setup: bot token + allowed user id."""
    from .config import get as cfg_get, set_key as cfg_set

    print("  To get a bot token: message @BotFather on Telegram and run /newbot.")
    try:
        token = input(bold("  Bot token ▶ ")).strip()
    except (EOFError, KeyboardInterrupt):
        print(red("  Telegram setup cancelled."))
        return
    if token:
        cfg_set("telegram.token", token)
        print(f"  {green('✓')} Bot token saved.")

    print("  To find your user id: message @userinfobot on Telegram and copy the number.")
    try:
        uid = input(bold("  Your Telegram user id ▶ ")).strip()
    except (EOFError, KeyboardInterrupt):
        uid = ""
    if uid.isdigit():
        existing = list(cfg_get("telegram.allowed_users", []) or [])
        if int(uid) not in existing:
            existing.append(int(uid))
        cfg_set("telegram.allowed_users", ",".join(str(u) for u in existing))
        print(f"  {green('✓')} Allowed user {uid} added.")

    print(f"  Start the bridge anytime with: {cyan('aishe gateway start')}")


def _telegram_bot_path() -> str:
    """Locate telegram_bot.py — in the repo clone or the installed sidecars dir."""
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sidecars", "telegram_bot.py"),
        os.path.expanduser("~/.local/share/aishe-cli/sidecars/telegram_bot.py"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


def _telegram_pid() -> Optional[str]:
    """Return the running telegram bridge PID, or None."""
    import subprocess as _sp
    try:
        out = _sp.run(["pgrep", "-f", "telegram_bot.py"], capture_output=True, text=True).stdout.strip()
        pids = [p for p in out.split() if p]
        return pids[0] if pids else None
    except Exception:
        return None


def cmd_telegram(args: Any) -> None:
    """Manage the Telegram bridge."""
    from .config import get as cfg_get, set_key as cfg_set

    action = getattr(args, "tg_action", None)

    if action == "auth":
        token = getattr(args, "token", "") or ""
        cfg_set("telegram.token", token)
        print(f"  {green('✓')} Bot token saved.")
        return

    if action == "status":
        pid = _telegram_pid()
        token = cfg_get("telegram.token", "")
        if pid:
            print(f"  {green('●')} Telegram bridge running (PID {pid})")
        else:
            print(f"  {red('○')} Telegram bridge not running")
        print(f"  Token: {cyan('set') if token else red('not set')}")
        print(f"  Allowed users: {cyan(str(cfg_get('telegram.allowed_users', [])))}")
        return

    if action == "start" or action == "restart":
        _telegram_stop()
        token = cfg_get("telegram.token", "")
        if not token:
            print(red("  No bot token set. Run: aishe setup (Telegram section) or aishe telegram auth <token>"))
            sys.exit(1)
        bot_path = _telegram_bot_path()
        log_file = cfg_get("telegram.log_file", "~/aishe/telegram.log")
        log_file = os.path.expanduser(log_file)
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        env = dict(os.environ)
        env["AISHE_TELEGRAM_TOKEN"] = token
        subprocess.Popen(
            [sys.executable, bot_path],
            stdout=open(log_file, "a"),
            stderr=subprocess.STDOUT,
            env=env,
        )
        # Wait briefly for it to come up
        import time as _t
        for _ in range(10):
            if _telegram_pid():
                break
            _t.sleep(0.5)
        print(f"  {green('✓')} Telegram bridge {'restarted' if action == 'restart' else 'started'}. Log: {dim(log_file)}")
        return

    if action == "stop":
        _telegram_stop()
        print(f"  {green('✓')} Telegram bridge stopped.")
        return


def _telegram_stop() -> None:
    pid = _telegram_pid()
    if pid:
        subprocess.run(["kill", pid], check=False)


def cmd_gateway(args: Any) -> None:
    """Gateway control — start/restart/stop/status for the Telegram bridge.

    Bare `aishe gateway` (no action) tails the bridge log live.
    """
    action = getattr(args, "gw_action", None)
    if action in ("start", "restart"):
        cmd_telegram(type("A", (), {"tg_action": action})())
    elif action == "stop":
        cmd_telegram(type("A", (), {"tg_action": "stop"})())
    elif action == "status":
        cmd_telegram(type("A", (), {"tg_action": "status"})())
    else:  # bare `aishe gateway` → live log view
        cmd_gateway_logs(args)


def cmd_gateway_logs(args: Any) -> None:
    """Live-tail the Telegram bridge log file."""
    import time as _t

    log_file = get("telegram.log_file", "~/aishe/telegram.log")
    log_file = os.path.expanduser(log_file)

    if not os.path.exists(log_file):
        print(red(f"Log file not found: {log_file}"))
        print(dim("Start the bridge first: aishe gateway start"))
        sys.exit(1)

    print(bold("📡 Aishe Gateway — live bridge log"))
    print(dim(f"Tailing: {log_file}"))
    print(dim("Ctrl+C to stop."))
    print(dim("─" * 50))

    # Print the last N lines first, then follow new ones.
    try:
        with open(log_file) as f:
            lines = f.readlines()
        for line in lines[-20:]:
            print(line.rstrip())
    except Exception as e:
        print(red(f"Could not read log: {e}"))
        sys.exit(1)

    # Follow the file for new lines.
    try:
        with open(log_file) as f:
            f.seek(0, 2)  # end of file
            while True:
                line = f.readline()
                if line:
                    print(line.rstrip(), flush=True)
                else:
                    _t.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n{dim('Log view stopped.')}")


# ─── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="aishe",
        description="Aishe CLI — voice-first AI assistant for your terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  aishe                         — interactive streaming chat
  aishe "Tell me a joke"        — one-shot streaming chat
  aishe live                    — live voice conversation
  aishe doctor                  — run diagnostics
  aishe config                  — view config
  aishe config set voice.default_voice M1
  aishe version
""",
    )

    # Bare `aishe` accepts an optional message (one-shot) or none (interactive).
    # Intercept before argparse: if the first arg isn't a known subcommand,
    # treat everything as a message.
    _KNOWN = {"status", "live", "memory", "config", "doctor", "version", "setup", "telegram", "gateway", "dashboard", "search", "-h", "--help"}
    _argv = sys.argv[1:]
    if _argv and _argv[0] not in _KNOWN:
        msg = " ".join(_argv)
        _stream_once(msg, "cli")
        return

    sub = parser.add_subparsers(dest="command")

    # status
    sub.add_parser("status", help="Check all service statuses")

    # live
    p_live = sub.add_parser("live", help="Live voice conversation (record → STT → think → TTS)")
    p_live.add_argument("-d", "--device", type=int, default=None, help="Mic device index")
    p_live.add_argument("--duration", type=int, default=None, help="Recording duration in seconds (default: config)")
    p_live.add_argument("-t", "--thread", default="live", help="Thread ID")
    p_live.add_argument("-V", "--voice", default=None, help="TTS voice (F1-F5, M1-M5)")
    p_live.add_argument("--no-tts", action="store_true", help="Don't speak responses")
    p_live.add_argument("--no-vad", action="store_true", help="Disable VAD (use fixed duration)")
    p_live.add_argument("--list", action="store_true", help="List mic devices and exit")

    # memory
    p_mem = sub.add_parser("memory", help="Memory management (vector + short-term files)")
    p_mem.add_argument("action", choices=["add", "search", "list", "clear", "status", "update", "delete", "seed", "soul", "user"], help="Memory action")
    p_mem.add_argument("value", nargs="*", help="Fact text, search query, or <id> <new text>")

    # search sessions
    p_search = sub.add_parser("search", help="Search past conversations/sessions for any fact or topic")
    p_search.add_argument("query", nargs="+", help="Search query")

    # config
    p_config = sub.add_parser("config", help="View/edit configuration")
    p_config.add_argument("action", choices=["get", "set"], nargs="?", help="Config action")
    p_config.add_argument("key", nargs="?", help="Config key (dot-separated)")
    p_config.add_argument("value", nargs="?", help="Config value (for set)")

    # doctor
    sub.add_parser("doctor", help="Run comprehensive diagnostics")

    # setup
    sub.add_parser("setup", help="Configure model provider (Sarvam / Ollama cloud / llama.cpp / OpenRouter / OpenCode) or Telegram bridge")

    # telegram
    p_tg = sub.add_parser("telegram", help="Manage the Telegram bridge")
    p_tg.add_argument("tg_action", nargs="?", choices=["auth", "start", "stop", "restart", "status"], help="Telegram action")
    p_tg.add_argument("token", nargs="?", help="Bot token (for auth)")

    # gateway
    p_gw = sub.add_parser("gateway", help="Start/restart/stop the Telegram connection bridge")
    p_gw.add_argument("gw_action", nargs="?", choices=["start", "stop", "restart", "status"], help="Gateway action")

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Open the local web config dashboard")
    p_dash.add_argument("--port", type=int, default=8767, help="Port (default 8767)")
    p_dash.add_argument("--host", default="127.0.0.1", help="Host (default 127.0.0.1)")
    p_dash.add_argument("--open", action="store_true", help="Open browser automatically")

    # version
    sub.add_parser("version", help="Show version information")

    args = parser.parse_args()

    # Bare `aishe` (no subcommand) → one-shot (with message) or interactive loop
    if not args.command:
        cmd_repl_stream(args)
        return

    # Handle --list for live before dispatch
    if args.command == "live" and hasattr(args, "list") and args.list:
        devices = list_mic_devices()
        if devices:
            print(bold("Available microphone devices:"))
            for idx, name in devices:
                print(f"  [{idx}] {name}")
            print(dim("\nUse: aishe live -d <index>"))
        else:
            print(red("No microphone devices found"))
        sys.exit(0)

    # Handle config specially (it has its own sub-subcommands)
    if args.command == "config":
        if not args.action:
            # Print config
            cfg = load()
            try:
                import yaml
                print(yaml.dump(cfg, default_flow_style=False, sort_keys=False).strip())
            except ImportError:
                print(cfg)
            return
        if args.action == "get":
            if not args.key:
                print(red("Usage: aishe config get <key>"))
                sys.exit(1)
            from .config import get as cfg_get
            val = cfg_get(args.key)
            if val is None:
                print(f"Key '{args.key}' not found")
                sys.exit(1)
            print(val)
            return
        if args.action == "set":
            if not args.key or not args.value:
                print(red("Usage: aishe config set <key> <value>"))
                sys.exit(1)
            from .config import set_key
            set_key(args.key, args.value)
            print(f"Set {args.key} = {args.value}")
            return

    dispatch: Dict[str, Any] = {
        "status": cmd_status,
        "live": cmd_live,
        "memory": cmd_memory,
        "search": cmd_search_sessions,
        "doctor": cmd_doctor,
        "setup": cmd_setup,
        "telegram": cmd_telegram,
        "gateway": cmd_gateway,
        "dashboard": cmd_dashboard,
        "version": cmd_version,
    }

    dispatch[args.command](args)


def cmd_search_sessions(args: Any = None) -> None:
    """Search past sessions for a fact or topic."""
    query = " ".join(args.query) if hasattr(args, "query") and args.query else ""
    if not query:
        print(red("Usage: aishe search <query>"))
        sys.exit(1)
    from .threads import search_sessions
    results = search_sessions(query)
    if not results:
        print(dim(f"No matches for '{query}' in past sessions."))
        return
    print(bold(f"Found {len(results)} match(es) for '{query}':"))
    print("─" * 60)
    for r in results:
        who = "You" if r.get("role") == "user" else "Aishe"
        ts = (r.get("timestamp") or "")[:16]
        title = r.get("title") or r.get("thread_id")
        content = (r.get("content") or "").replace("\n", " ")
        print(f"  {bullet(f'[{title}]')} {dim(ts)} {bold(who)}: {content[:200]}")
    print(dim(f"\n{len(results)} hit(s) across all sessions (SQLite FTS5)"))


def cmd_status(args: Any = None) -> None:
    """Check all service statuses."""
    header("Aishe Service Status")

    services = [
        ("Ollama", get("services.ollama", "http://localhost:11434"), "/api/tags"),
        ("DeepAgent", get("services.deepagent", "http://localhost:8765"), "/health"),
        ("Parakeet STT", get("services.stt", "http://localhost:5093"), "/healthz"),
        ("Supertonic TTS", get("services.tts", "http://localhost:8766"), "/health"),
    ]

    section("Services")
    for name, url, endpoint in services:
        full_url = f"{url}{endpoint}"
        ok = check(full_url)
        extra = ""
        if ok and name == "Ollama":
            try:
                r = requests.get(f"{url}/api/tags", timeout=3)
                models = r.json().get("models", [])
                extra = f" ({len(models)} models)"
            except Exception:
                pass
        elif ok and name == "Supertonic TTS":
            extra = f" ({tts_info()})"
        dot = status_dot(ok)
        status_text = green("UP") if ok else red("DOWN")
        print(f"  {dot} {name:16s} {status_text}{dim(extra)}")

    section("Data")
    mem_cnt = mem_count()
    thr_cnt = thr_count()
    print(f"  {bullet('Memory')} {cyan(str(mem_cnt))} {dim('entries')}")
    print(f"  {bullet('Threads')} {cyan(str(thr_cnt))} {dim('conversations (SQLite)')}")
    try:
        from .memory import status as mem_status
        print(f"  {bullet('Mem backend')} {cyan(mem_status())}")
    except Exception:
        pass
