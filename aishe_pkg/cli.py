"""Aishe CLI — main entry point and command dispatch."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
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
        elif etype == "tool_call":
            print(yellow(f"\n  [tool: {ev.get('name', '?')}]"), flush=True)
        elif etype == "tool_result":
            print(dim(f"  [result: {ev.get('result', '')[:100]}]\n"), flush=True)
        elif etype == "final":
            if not answer:
                answer = ev.get("answer", "")
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
    print(dim("Press Enter to record, Ctrl+C to exit."))
    print(dim("Or type a message and press Enter to chat without voice."))
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

        # Assistant prefix line
        print(f"\n{cyan('##')} ", end="")

        for line in r.iter_lines():
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = ev.get("event", "")
            if etype == "tool_call":
                name = ev.get("name", "?")
                args_str = json.dumps(ev.get("args", {}), ensure_ascii=False)
                tool_calls.append(name)
                if not in_tool:
                    print(f"\n{dim('```tools')}")
                    in_tool = True
                print(f"  {yellow('•')} {cyan(name)}({args_str})")
            elif etype == "tool_result":
                result = ev.get("result", "")
                result_short = result[:200] + ("..." if len(result) > 200 else "")
                print(f"    {dim('└─')} {result_short}")
            elif etype == "token":
                tok = ev.get("content", "")
                if tok:
                    print(tok, end="", flush=True)
                    answer += tok
            elif etype == "final":
                if not answer:
                    answer = ev.get("answer", "")

        # Persist assistant turn
        if answer:
            thr_add_message(tid, "assistant", answer)

        if in_tool:
            print(dim("```"))
        elif answer:
            print()

        if answer and not answer.endswith("\n"):
            print()

        # Close assistant turn
        print(dim("─" * 50))

        if not args.no_tts and answer.strip():
            print(f"  {dim('🔊 Speaking...')}", end="", flush=True)
            try:
                synthesize(answer, voice=voice, play=True)
                print(f"\r  {green('🔊 Spoken')}                    ")
            except Exception as e:
                print(f"\r  {yellow(f'TTS failed: {e}')}                    ")


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

def cmd_setup(args: Any) -> None:
    """Interactive setup — pick an Ollama model, pull it, apply it, restart DeepAgent."""
    from .config import get as cfg_get, set_key as cfg_set
    from .util import check as util_check

    ollama_url = cfg_get("services.ollama", "http://localhost:11434")
    current = cfg_get("model", "deepseek-v4-flash:cloud")

    header("Aishe Setup", "Choose your Ollama model")

    # 1. List available models
    section("Available Models")
    models: List[str] = []
    if util_check(f"{ollama_url}/api/tags"):
        try:
            r = requests.get(f"{ollama_url}/api/tags", timeout=5)
            models = sorted(m["name"] for m in r.json().get("models", []))
        except Exception:
            models = []
        if models:
            for m in models:
                marker = "●" if m == current else "○"
                print(f"  {marker} {cyan(m)}")
            print(dim(f"\n  Current: {current}"))
        else:
            print(dim("  No models pulled yet."))
    else:
        print(red("  Ollama is not reachable. Is it running?"))
        sys.exit(1)

    # 2. Choose a model
    section("Choose Model")
    if models:
        print("  Pick a number or type a model name (e.g. deepseek-v4-flash:cloud, llama3.2):")
        for i, m in enumerate(models, 1):
            print(f"    [{i}] {m}")
        try:
            choice = input(bold("\n  Model ▶ ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(red("\n  Setup cancelled."))
            sys.exit(1)
    else:
        try:
            choice = input(bold("  Model name to pull ▶ ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(red("\n  Setup cancelled."))
            sys.exit(1)

    selected = None
    if choice.isdigit() and 1 <= int(choice) <= len(models):
        selected = models[int(choice) - 1]
    elif choice:
        selected = choice.strip()
    else:
        selected = current

    if selected != current:
        section("Pull Model")
        print(f"  Pulling {cyan(selected)}...")
        subprocess.run(["ollama", "pull", selected])
        print(f"  {green('✓')} {selected} available")

    # 3. Save to config
    section("Save")
    cfg_set("model", selected)
    print(f"  {green('✓')} Saved model: {cyan(selected)}")

    # 4. Restart DeepAgent to apply the model
    section("Restart DeepAgent")
    print("  Restarting DeepAgent to apply the model...")
    # Locate the sidecar
    deepagent_dir = os.path.expanduser("~/.local/share/aishe-cli/deepagent")
    server_py = os.path.join(deepagent_dir, "server.py")
    venv_py = os.path.join(deepagent_dir, ".venv", "bin", "python")

    if os.path.exists(server_py) and os.path.exists(venv_py):
        # Stop the running DeepAgent by matching its working directory (avoids
        # killing the STT sidecar, whose cmdline is also `server.py`).
        try:
            lsof = subprocess.run(
                ["lsof", "-t", f"+d", deepagent_dir],
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

        env = dict(os.environ)
        env["AISHE_MODEL"] = selected
        env["AISHE_OLLAMA_URL"] = f"{ollama_url}/v1"
        env["AISHE_API_KEY"] = "ollama"
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
        print(f"  {green('✓')} DeepAgent restarted with {cyan(selected)}")
    else:
        print(yellow("  DeepAgent sidecar not found — model saved, will apply on next start."))

    print()
    print(f"  {green(bold('✓ Setup complete'))}  Model: {cyan(selected)}")

    # 5. Telegram bridge (optional)
    section("Telegram Bridge")
    from .config import get as cfg_get
    have_token = bool(cfg_get("telegram.token", ""))
    print(f"  Telegram: {green('configured') if have_token else red('not configured')}")
    try:
        want = input(bold("  Set up Telegram bridge? (y/N) ▶ ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        want = "n"
    if want in ("y", "yes"):
        _setup_telegram()


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
    """Gateway control — start/restart/stop/status for the Telegram bridge."""
    action = getattr(args, "gw_action", None)
    if action in ("start", "restart"):
        cmd_telegram(type("A", (), {"tg_action": action})())
    elif action == "stop":
        cmd_telegram(type("A", (), {"tg_action": "stop"})())
    else:  # status
        cmd_telegram(type("A", (), {"tg_action": "status"})())


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
    _KNOWN = {"status", "live", "memory", "config", "doctor", "version", "setup", "telegram", "gateway", "dashboard", "-h", "--help"}
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
    p_mem = sub.add_parser("memory", help="Memory management")
    p_mem.add_argument("action", choices=["add", "search", "list", "clear"], help="Memory action")
    p_mem.add_argument("value", nargs="*", help="Fact text or search query")

    # config
    p_config = sub.add_parser("config", help="View/edit configuration")
    p_config.add_argument("action", choices=["get", "set"], nargs="?", help="Config action")
    p_config.add_argument("key", nargs="?", help="Config key (dot-separated)")
    p_config.add_argument("value", nargs="?", help="Config value (for set)")

    # doctor
    sub.add_parser("doctor", help="Run comprehensive diagnostics")

    # setup
    sub.add_parser("setup", help="Choose your Ollama model and restart DeepAgent")

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
        "doctor": cmd_doctor,
        "setup": cmd_setup,
        "telegram": cmd_telegram,
        "gateway": cmd_gateway,
        "dashboard": cmd_dashboard,
        "version": cmd_version,
    }

    dispatch[args.command](args)


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
    print(f"  {bullet('Threads')} {cyan(str(thr_cnt))} {dim('conversations')}")
