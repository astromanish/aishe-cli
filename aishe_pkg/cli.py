"""Aishe CLI — main entry point and command dispatch."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from . import __version__
from .config import cmd_config, get, get_config_path, load
from .memory import cmd_memory, count as mem_count, export_csv as mem_export_csv, export_markdown as mem_export_md, list_all as mem_list, search as mem_search
from .ollama import cmd_ollama, health as ollama_health
from .pet import cmd_pet
from .threads import add_message as thr_add_message, cmd_threads, count as thr_count, delete as thr_delete, ensure_thread, export_markdown as thr_export_md, list_all as thr_list, search as thr_search
from .util import (
    Spinner, bold, check, cyan, dim, green, red, yellow,
    header, section, bullet, status_dot, key_value, divider,
    welcome, prompt_text, assistant_prefix,
)
from .voice import (
    cmd_voice,
    list_mic_devices,
    record_audio,
    record_with_vad,
    stt_health,
    synthesize,
    transcribe,
    tts_health,
    tts_info,
)

DEEPAGENT_URL = get("services.deepagent", "http://localhost:8765")


# ─── Chat ───────────────────────────────────────────────────────────────────

def cmd_chat(args: Any) -> None:
    msg = " ".join(args.message) if isinstance(args.message, list) else args.message
    if not msg:
        msg = sys.stdin.read().strip()
    if not msg:
        print(red("No message provided"))
        sys.exit(1)

    tid = args.thread or "cli"
    from .pet import send_signal
    send_signal("thinking", {"thread_id": tid, "stage": "start"})

    # Ensure thread exists and save user message
    thr_add_message(tid, "user", msg)

    try:
        r = requests.post(
            f"{DEEPAGENT_URL}/invoke",
            json={"message": msg, "thread_id": tid},
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.ConnectionError:
        send_signal("error", {"error": "deepagent_down"})
        print(red("Cannot reach DeepAgent on :8765. Is the sidecar running?"))
        sys.exit(1)
    except Exception as e:
        send_signal("error", {"error": str(e)[:50]})
        print(red(f"Error: {e}"))
        sys.exit(1)

    answer = data.get("answer", "")
    print(answer)
    if answer:
        thr_add_message(tid, "assistant", answer)
    if args.verbose:
        print(dim(f"\n─ stats: {data.get('steps', '?')} steps, {len(data.get('tool_calls', []))} tool calls"))
        for tc in data.get("tool_calls", []):
            print(dim(f"  tool: {tc['name']}({json.dumps(tc['args'], ensure_ascii=False)})"))
    # Bump pet counters
    try:
        from .pet import PetState, check_milestones, update_streak
        ps = PetState.load()
        ps.n_chats += 1
        if data.get("answer"):
            ps.total_tokens += len(data["answer"].split())
        update_streak(ps)
        new_ms = check_milestones(ps)
        if new_ms:
            ps.milestones.extend(new_ms)
        ps.save()
    except Exception:
        pass
    send_signal("idle", {"last": "chat", "thread_id": tid, "mem_count": mem_count()})


def cmd_stream(args: Any) -> None:
    msg = " ".join(args.message) if isinstance(args.message, list) else args.message
    if not msg:
        msg = sys.stdin.read().strip()
    if not msg:
        print(red("No message provided"))
        sys.exit(1)

    tid = args.thread or "cli"
    from .pet import send_signal
    send_signal("thinking", {"thread_id": tid, "stage": "stream_start"})

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
        send_signal("error", {"error": "deepagent_down"})
        print(red("Cannot reach DeepAgent on :8765."))
        sys.exit(1)

    answer = ""
    token_count = 0
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
            token_count += 1
            if token_count % 8 == 0:
                send_signal("thinking", {"thread_id": tid, "tokens": token_count})
        elif etype == "tool_call":
            print(yellow(f"\n  [tool: {ev.get('name', '?')}]"), flush=True)
        elif etype == "tool_result":
            print(dim(f"  [result: {ev.get('result', '')[:100]}]"), flush=True)
        elif etype == "final":
            if not answer:
                answer = ev.get("answer", "")
    if not answer:
        print(dim("(empty response)"))
    else:
        thr_add_message(tid, "assistant", answer)
    print()
    send_signal("idle", {"last": "stream", "thread_id": tid, "tokens": token_count})


# ─── REPL (text-based continuous chat) ─────────────────────────────────────

def cmd_repl(args: Any) -> None:
    """Continuous text REPL — type messages, get streaming responses."""
    if not check(f"{DEEPAGENT_URL}/health"):
        print(red("DeepAgent is down on :8765"))
        sys.exit(1)

    tid = args.thread or "repl"
    print(bold("💬 Aishe REPL"))
    print(dim("Type your message and press Enter. Type /exit, /quit, or Ctrl+C to exit."))
    print(dim("Commands: /new (new thread), /thread <id> (switch), /title <name>, /threads, /delete <id>, /clear"))
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
        if user_input.lower() == "/clear":
            os.system("clear")
            continue
        if user_input.lower() == "/new":
            tid = f"repl_{datetime.now().strftime('%H%M%S')}"
            ensure_thread(tid)
            print(dim(f"  New thread: {tid}"))
            continue
        if user_input.lower().startswith("/thread "):
            tid = user_input.split(" ", 1)[1].strip()
            ensure_thread(tid)
            print(dim(f"  Switched to thread: {tid}"))
            continue
        if user_input.lower().startswith("/title "):
            new_title = user_input.split(" ", 1)[1].strip()
            t = ensure_thread(tid)
            t["title"] = new_title
            t["updated_at"] = datetime.now().isoformat()
            from .threads import _save_thread
            _save_thread(t)
            print(dim(f"  Renamed thread to: {new_title}"))
            continue
        if user_input.lower() == "/threads":
            threads = thr_list()
            print(dim(f"\n  Threads ({len(threads)}):"))
            for t in threads[:20]:
                n = len(t.get("messages", []))
                marker = "* " if t["id"] == tid else "  "
                print(dim(f"  {marker}{t['id'][:20]:22s} {t.get('title','untitled'):20s} {n} msgs"))
            print()
            continue
        if user_input.lower().startswith("/delete"):
            parts = user_input.split()
            if len(parts) < 2:
                print(red("  Usage: /delete <thread_id>"))
                continue
            target = parts[1]
            if thr_delete(target):
                print(dim(f"  Deleted {target}"))
                if target == tid:
                    tid = f"repl_{datetime.now().strftime('%H%M%S')}"
                    ensure_thread(tid)
            else:
                print(red(f"  Thread {target} not found"))
            continue

        # Print user turn in markdown-style block
        print(f"\n{green('>')} {green(user_input)}")

        # Save user turn
        thr_add_message(tid, "user", user_input)

        try:
            r = requests.post(
                f"{DEEPAGENT_URL}/stream",
                json={"message": user_input, "thread_id": tid},
                stream=True,
                timeout=120,
            )
            r.raise_for_status()
        except Exception as e:
            print(f"\n  {red(f'Error: {e}')}")
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
            if etype == "token":
                tok = ev.get("content", "")
                print(tok, end="", flush=True)
                answer += tok
            elif etype == "tool_call":
                name = ev.get("name", "?")
                tool_calls.append(name)
                if not in_tool:
                    print(f"\n{dim('```tools')}")
                    in_tool = True
                print(f"  {yellow('•')} {cyan(name)}")
            elif etype == "tool_result":
                result = ev.get("result", "")
                result_short = result[:200] + ("..." if len(result) > 200 else "")
                print(f"    {dim('└─')} {result_short}")
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


# ─── Live Voice ──────────────────────────────────────────────────────────────

def cmd_live(args: Any) -> None:
    import signal as sig_mod
    import threading

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
    from .pet import send_signal, PetState
    _pet = PetState.load()
    _pet.n_voice_sessions += 1
    _pet.save()

    while True:
        try:
            user_input = input(bold("\nYou ▶ ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{dim('Goodbye!')}")
            break

        if not user_input:
            print(dim("  🔴 Recording..."), end="", flush=True)
            send_signal("listening", {"thread_id": tid})
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
            send_signal("speaking", {"thread_id": tid, "len": len(answer)})
            try:
                synthesize(answer, voice=voice, play=True)
                print(f"\r  {green('🔊 Spoken')}                    ")
            except Exception as e:
                print(f"\r  {yellow(f'TTS failed: {e}')}                    ")
        else:
            send_signal("idle", {"last": "live", "thread_id": tid})


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

    # 5b. Pet
    section("Pet")
    try:
        from .pet import PetState
        ps = PetState.load()
        print(f"  {bullet('Enabled')} {cyan(str(ps.enabled))}")
        print(f"  {bullet('Born')} {cyan(ps.born or '—')}")
        print(f"  {bullet('Last seen')} {cyan(ps.last_seen or '—')}")
        print(f"  {bullet('Chats / Voice')} {cyan(str(ps.n_chats))} / {cyan(str(ps.n_voice_sessions))}")
        print(f"  {bullet('Size')} {cyan(f'{ps.size:.2f}')}")
        print(f"  {bullet('Streak')} {cyan(str(ps.streak_days))} {dim('day(s)')}")
        if ps.milestones:
            print(f"  {bullet('Milestones')} {cyan(', '.join(ps.milestones))}")
    except Exception as e:
        print(f"  {red('pet error: ' + str(e))}")

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


# ─── Search ─────────────────────────────────────────────────────────────────

def cmd_search(args: Any) -> None:
    """Full-text search across threads and memory."""
    query = " ".join(args.query) if isinstance(args.query, list) else args.query
    if not query:
        print(red("No search query provided"))
        sys.exit(1)

    found_any = False

    # Search memory
    mem_results = mem_search(query)
    if mem_results:
        found_any = True
        print(bold(f"📝 Memory ({len(mem_results)} match(es))"))
        print("─" * 40)
        for r in mem_results:
            print(f"  {cyan(r['id'])}  {r['fact']}")
            print(f"  {dim(r['timestamp'])}")
        print()

    # Search threads
    thr_results = thr_search(query)
    if thr_results:
        found_any = True
        print(bold(f"💬 Threads ({len(thr_results)} thread(s) with matches)"))
        print("─" * 40)
        for r in thr_results:
            t = r["thread"]
            print(f"  {cyan(t['id'])}  {t.get('title', 'untitled')}")
            for m in r["matches"]:
                content = m.get("content", "")
                print(f"    {dim(content[:120])}")
            print()

    if not found_any:
        print(dim(f"No results for '{query}'"))


# ─── Export ──────────────────────────────────────────────────────────────────

def cmd_export(args: Any) -> None:
    """Export data (threads, memory) to portable formats."""
    export_dir = args.dir or os.path.expanduser("~/Downloads")
    export_dir = os.path.expanduser(export_dir)
    os.makedirs(export_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    exported = 0

    # Export memory
    mem_path = os.path.join(export_dir, f"aishe_memory_{timestamp}.md")
    n = mem_export_md(mem_path)
    if n:
        print(f"{green('Exported')} {n} memories → {mem_path}")
        exported += 1

    mem_csv = os.path.join(export_dir, f"aishe_memory_{timestamp}.csv")
    n = mem_export_csv(mem_csv)
    if n:
        print(f"{green('Exported')} {n} memories → {mem_csv}")

    # Export threads
    threads = thr_list()
    for t in threads:
        tid = t["id"]
        title = t.get("title", "untitled").replace(" ", "_")
        thr_path = os.path.join(export_dir, f"aishe_thread_{title}_{tid[:12]}_{timestamp}.md")
        n = thr_export_md(tid, thr_path)
        if n:
            print(f"{green('Exported')} thread {cyan(tid)} ({n} msgs) → {thr_path}")
            exported += 1

    if not exported:
        print(dim("Nothing to export."))


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


# ─── Completions ────────────────────────────────────────────────────────────

def cmd_completions(args: Any) -> None:
    """Generate shell completion scripts."""
    shell = args.shell or "bash"

    if shell == "bash":
        print("""_aishe_completions() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    opts="status chat stream repl live threads memory voice ollama intent config doctor search export version completions pet"

    if [[ ${cur} == -* ]] ; then
        COMPREPLY=( $(compgen -W "--help" -- ${cur}) )
        return 0
    fi

    case "${prev}" in
        chat|stream)
            COMPREPLY=( $(compgen -W "-t --thread -v --verbose" -- ${cur}) )
            ;;
        live)
            COMPREPLY=( $(compgen -W "-d --device --duration -t --thread -V --voice --no-tts --no-vad --list" -- ${cur}) )
            ;;
        threads)
            COMPREPLY=( $(compgen -W "--new --delete --show --rename --title" -- ${cur}) )
            ;;
        memory)
            COMPREPLY=( $(compgen -W "add search list clear" -- ${cur}) )
            ;;
        voice)
            COMPREPLY=( $(compgen -W "transcribe speak status" -- ${cur}) )
            ;;
        ollama)
            COMPREPLY=( $(compgen -W "models running pull rm stop signin signout status" -- ${cur}) )
            ;;
        intent)
            COMPREPLY=( $(compgen -W "stats export" -- ${cur}) )
            ;;
        config)
            COMPREPLY=( $(compgen -W "get set" -- ${cur}) )
            ;;
        completions)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- ${cur}) )
            ;;
        pet)
            COMPREPLY=( $(compgen -W "status inspect reset enable disable signal milestones attach detach watch --mood --state --width --height --fps --direction --ratio --window" -- ${cur}) )
            ;;
        *)
            COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
            ;;
    esac
    return 0
}
complete -F _aishe_completions aishe""")
    elif shell == "zsh":
        print("""#compdef aishe
_aishe() {
    local -a commands
    commands=(
        'status:Check all service statuses'
        'chat:One-shot chat via DeepAgent'
        'stream:Streaming chat (tokens printed live)'
        'repl:Continuous text REPL'
        'live:Live voice conversation'
        'threads:Manage chat threads'
        'memory:Memory management'
        'voice:Voice input/output'
        'ollama:Ollama model management'
        'config:View/edit configuration'
        'doctor:Run diagnostics'
        'search:Search threads and memory'
        'export:Export data'
        'version:Show version'
        'completions:Generate shell completions'
        'pet:Your terminal blob pet'
    )
    _describe 'aishe' commands
}
compdef _aishe aishe""")
    elif shell == "fish":
        print("""# Aishe completions for fish
complete -c aishe -f
complete -c aishe -a "status" -d "Check all service statuses"
complete -c aishe -a "chat" -d "One-shot chat via DeepAgent"
complete -c aishe -a "stream" -d "Streaming chat (tokens printed live)"
complete -c aishe -a "repl" -d "Continuous text REPL"
complete -c aishe -a "live" -d "Live voice conversation"
complete -c aishe -a "threads" -d "Manage chat threads"
complete -c aishe -a "memory" -d "Memory management"
complete -c aishe -a "voice" -d "Voice input/output"
complete -c aishe -a "ollama" -d "Ollama model management"
complete -c aishe -a "config" -d "View/edit configuration"
complete -c aishe -a "doctor" -d "Run diagnostics"
complete -c aishe -a "search" -d "Search threads and memory"
complete -c aishe -a "export" -d "Export data"
complete -c aishe -a "version" -d "Show version"
complete -c aishe -a "completions" -d "Generate shell completion scripts"
complete -c aishe -a "pet" -d "Your terminal blob pet"
""")
    else:
        print(red(f"Unknown shell: {shell}. Use bash, zsh, or fish."))
        sys.exit(1)


# ─── Intent Lab ──────────────────────────────────────────────────────────────

def cmd_intent(args: Any) -> None:
    from .config import get as cfg_get
    DATA_DIR = Path(cfg_get("data.dir", str(Path.home() / "Library" / "Application Support" / "aishe")))
    INTENT_DIR = DATA_DIR / "intent_lab"

    if args.action == "stats":
        days = args.days or 7
        if not INTENT_DIR.exists():
            print(dim("No intent logs found."))
            return
        now = datetime.now()
        total = 0
        by_intent: Dict[str, int] = {}
        correct = 0
        misclassified = 0
        for f in INTENT_DIR.glob("intent_*.jsonl"):
            name = f.stem
            date_str = name.replace("intent_", "")
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d").date()
                days_ago = (now.date() - d).days
                if days_ago >= days:
                    continue
            except ValueError:
                pass
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                by_intent[e["intent"]] = by_intent.get(e["intent"], 0) + 1
                if e.get("feedback") is True:
                    correct += 1
                elif e.get("feedback") is False:
                    misclassified += 1
        accuracy = (correct / total * 100) if total > 0 else 0
        print(bold(f"Intent Stats (last {days} days)"))
        print("─" * 40)
        print(f"  Total:          {total}")
        print(f"  Accuracy:       {accuracy:.1f}%")
        print(f"  Misclassified:  {misclassified}")
        if by_intent:
            print(f"\n  {bold('By intent:')}")
            for intent, count in sorted(by_intent.items(), key=lambda x: -x[1]):
                bar = "█" * min(count, 30)
                print(f"    {intent:20s} {count:4d} {cyan(bar)}")
        return

    if args.action == "export":
        if not INTENT_DIR.exists():
            print(dim("No intent logs found."))
            return
        outpath = os.path.expanduser(f"~/Downloads/intent_export_{datetime.now().strftime('%Y-%m-%d')}.csv")
        import csv
        with open(outpath, "w", newline="") as csvfile:
            w = csv.writer(csvfile)
            w.writerow(["id", "timestamp", "raw_text", "language", "intent", "confidence", "tool_used", "feedback"])
            for f in INTENT_DIR.glob("intent_*.jsonl"):
                for line in f.read_text().splitlines():
                    if not line.strip():
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    w.writerow([
                        e["id"], e["timestamp"], e["raw_text"], e["language"],
                        e["intent"], e["confidence"],
                        e.get("tool_used", ""),
                        "correct" if e.get("feedback") is True else "wrong" if e.get("feedback") is False else "",
                    ])
        print(f"{green('Exported')} → {outpath}")
        return


# ─── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="aishe",
        description="Aishe CLI — voice-first AI assistant for your terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  aishe status
  aishe chat "What is 2+2?"
  aishe stream "Tell me a joke"
  aishe repl                    — continuous text chat
  aishe live                    — live voice conversation
  aishe pet                     — your terminal blob (foreground, animated)
  aishe pet status              — one-shot frame
  aishe pet inspect             — dump all pet state as JSON
  aishe pet attach              — attach pet as tmux side-pane (runs persistently)
  aishe pet detach              — detach the side-pane
  aishe pet watch install       — auto-attach pet in every new tmux window
  aishe doctor                  — run diagnostics
  aishe search "query"          — search threads + memory
  aishe export                  — export all data
  aishe config                  — view config
  aishe config set voice.default_voice M1
  aishe version
  aishe completions bash > ~/.bash_completion.d/aishe
""",
    )

    sub = parser.add_subparsers(dest="command")

    # status
    sub.add_parser("status", help="Check all service statuses")

    # chat
    p_chat = sub.add_parser("chat", help="One-shot chat via DeepAgent")
    p_chat.add_argument("message", nargs="*", help="Message to send (or pipe via stdin)")
    p_chat.add_argument("-t", "--thread", default="cli", help="Thread ID")
    p_chat.add_argument("-v", "--verbose", action="store_true", help="Show tool calls")

    # stream
    p_stream = sub.add_parser("stream", help="Streaming chat (tokens printed live)")
    p_stream.add_argument("message", nargs="*", help="Message to send (or pipe via stdin)")
    p_stream.add_argument("-t", "--thread", default="cli", help="Thread ID")

    # repl
    p_repl = sub.add_parser("repl", help="Continuous text REPL (type messages, get streaming responses)")
    p_repl.add_argument("-t", "--thread", default="repl", help="Thread ID")

    # live
    p_live = sub.add_parser("live", help="Live voice conversation (record → STT → think → TTS)")
    p_live.add_argument("-d", "--device", type=int, default=None, help="Mic device index")
    p_live.add_argument("--duration", type=int, default=None, help="Recording duration in seconds (default: config)")
    p_live.add_argument("-t", "--thread", default="live", help="Thread ID")
    p_live.add_argument("-V", "--voice", default=None, help="TTS voice (F1-F5, M1-M5)")
    p_live.add_argument("--no-tts", action="store_true", help="Don't speak responses")
    p_live.add_argument("--no-vad", action="store_true", help="Disable VAD (use fixed duration)")
    p_live.add_argument("--list", action="store_true", help="List mic devices and exit")

    # threads
    p_threads = sub.add_parser("threads", help="Manage chat threads")
    p_threads.add_argument("--new", action="store_true", help="Create a new thread")
    p_threads.add_argument("--delete", metavar="ID", help="Delete a thread")
    p_threads.add_argument("--show", metavar="ID", help="Show thread messages")
    p_threads.add_argument("--rename", metavar="ID", help="Rename a thread")
    p_threads.add_argument("--title", nargs="*", help="New title (use with --rename)")

    # memory
    p_mem = sub.add_parser("memory", help="Memory management")
    p_mem.add_argument("action", choices=["add", "search", "list", "clear"], help="Memory action")
    p_mem.add_argument("value", nargs="*", help="Fact text or search query")

    # voice
    p_voice = sub.add_parser("voice", help="Voice input/output")
    p_voice.add_argument("action", choices=["transcribe", "speak", "status"], help="Voice action")
    p_voice.add_argument("file", nargs="?", help="Audio file path (for transcribe)")
    p_voice.add_argument("text", nargs="*", help="Text to synthesize (for speak)")
    p_voice.add_argument("--voice", "-V", default=None, help="Voice (F1-F5, M1-M5)")
    p_voice.add_argument("--output", "-o", help="Save audio to file (no auto-play)")
    p_voice.add_argument("--no-play", action="store_true", help="Don't auto-play audio")

    # ollama
    p_ollama = sub.add_parser("ollama", help="Ollama model management")
    p_ollama.add_argument("action", choices=["models", "running", "pull", "rm", "stop", "signin", "signout", "status"], help="Ollama action")
    p_ollama.add_argument("model", nargs="?", help="Model name (for pull/rm/stop)")

    # intent
    p_intent = sub.add_parser("intent", help="Intent lab")
    p_intent.add_argument("action", choices=["stats", "export"], help="Intent action")
    p_intent.add_argument("--days", type=int, default=7, help="Days to include")

    # config
    p_config = sub.add_parser("config", help="View/edit configuration")
    p_config.add_argument("action", choices=["get", "set"], nargs="?", help="Config action")
    p_config.add_argument("key", nargs="?", help="Config key (dot-separated)")
    p_config.add_argument("value", nargs="?", help="Config value (for set)")

    # doctor
    sub.add_parser("doctor", help="Run comprehensive diagnostics")

    # search
    p_search = sub.add_parser("search", help="Search threads and memory")
    p_search.add_argument("query", nargs="*", help="Search query")

    # export
    p_export = sub.add_parser("export", help="Export data (threads, memory)")
    p_export.add_argument("--dir", "-d", default="~/Downloads", help="Export directory")

    # version
    sub.add_parser("version", help="Show version information")

    # completions
    p_comp = sub.add_parser("completions", help="Generate shell completion scripts")
    p_comp.add_argument("shell", nargs="?", default="bash", choices=["bash", "zsh", "fish"], help="Shell type")

    # pet
    p_pet = sub.add_parser("pet", help="Your aishe blob — terminal pet that reflects activity")
    p_pet.add_argument("pet_action", nargs="?",
                       choices=["status", "inspect", "reset", "enable", "disable", "signal", "milestones", "attach", "detach", "watch"],
                       help="Pet action (default = foreground animation)")
    p_pet.add_argument("--mood", choices=["idle", "thinking", "listening", "speaking", "error", "greeting"],
                       help="Mood for `pet status` (default: last seen)")
    p_pet.add_argument("--state", choices=["idle", "thinking", "listening", "speaking", "error", "greeting"],
                       help="Signal state (for `pet signal`)")
    p_pet.add_argument("--width", type=int, default=None, help="Frame width in cells")
    p_pet.add_argument("--height", type=int, default=None, help="Frame height in cells")
    p_pet.add_argument("--fps", type=int, default=None, help="Frames per second for foreground mode")
    # attach-specific flags
    p_pet.add_argument("--direction", choices=["right", "bottom"], default="right",
                       help="For `pet attach`: split direction (default right)")
    p_pet.add_argument("--ratio", type=int, default=30,
                       help="For `pet attach`: %% of pane given to pet (5-95, default 30)")
    p_pet.add_argument("--window", action="store_true",
                       help="For `pet attach`: open a separate terminal window instead of tmux split")
    p_pet.add_argument("--no-pane", action="store_true",
                       help=argparse.SUPPRESS)  # alias for --window
    # watch sub-action
    p_pet.add_argument("watch_action", nargs="?",
                       choices=["install", "uninstall", "status"],
                       help="Watch sub-action: install/uninstall the tmux auto-attach hook")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

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
        "chat": cmd_chat,
        "stream": cmd_stream,
        "repl": cmd_repl,
        "live": cmd_live,
        "threads": cmd_threads,
        "memory": cmd_memory,
        "voice": cmd_voice,
        "ollama": cmd_ollama,
        "intent": cmd_intent,
        "doctor": cmd_doctor,
        "search": cmd_search,
        "export": cmd_export,
        "version": cmd_version,
        "completions": cmd_completions,
        "pet": cmd_pet,
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
