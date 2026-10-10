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
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from . import __version__
from .config import cmd_config, get, get_config_path, load
from .memory import cmd_memory, count as mem_count
from .threads import (
    add_message as thr_add_message,
    count as thr_count,
    resolve_thread_id,
)
from .util import (
    bold, check, cyan, dim, green, red, yellow,
    header, section, bullet, status_dot, key_value,
)
# Voice helpers kept for `aishe doctor` diagnostics (STT/TTS roundtrip tests).
from .voice import (
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

    tid = resolve_thread_id()

    # One-shot: message provided as arg, or piped via stdin (non-TTY)
    msg = " ".join(args.message) if isinstance(getattr(args, "message", None), list) else getattr(args, "message", "")
    if not msg and not sys.stdin.isatty():
        msg = sys.stdin.read().strip()
    if msg:
        _stream_once(msg, tid)
        return

    # Interactive loop
    print(bold("aishe — streaming chat"))
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


# ─── Doctor ─────────────────────────────────────────────────────────────────

def _desktop_app_dir() -> Optional[str]:
    """Locate the Aishe desktop app directory, or None."""
    candidates = [
        os.path.expanduser("~/Documents/projects/aishe-desktop"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aishe-desktop"),
        os.path.expanduser("~/.local/share/aishe-cli/aishe-desktop"),
    ]
    return next((c for c in candidates if os.path.isdir(c)), None)


def _desktop_launcher(app_dir: str) -> Optional[str]:
    """Path to the app's launcher script, or None.

    Prefer run.sh: it recovers WAYLAND_DISPLAY / XDG_RUNTIME_DIR from the user
    session (Electron otherwise dies with "Failed to connect to Wayland
    display") and applies the software-rendering flags this machine's Intel iGPU
    needs. Launching electron directly works only when those vars already happen
    to be set, which is why `aishe desktop` used to look fine from a terminal but
    fail everywhere else.
    """
    script = os.path.join(app_dir, "run.sh")
    if os.path.exists(script) and os.access(script, os.X_OK):
        return script
    return None


def _launch_desktop_app(tab: Optional[str] = None) -> None:
    """Launch the full Aishe desktop client, optionally focused on a tab."""
    app_dir = _desktop_app_dir()
    if not app_dir:
        print(red("Aishe desktop app not found."))
        print(dim("  Expected aishe-desktop/ next to this CLI, or in ~/.local/share/aishe-cli/"))
        sys.exit(1)

    electron = os.path.join(app_dir, "node_modules", ".bin", "electron")
    if not os.path.exists(electron):
        print(red("Aishe desktop app dependencies are not installed."))
        print(dim(f"  Run: cd {app_dir} && npm install"))
        sys.exit(1)

    launcher = _desktop_launcher(app_dir)
    if launcher:
        cmd = [launcher]
    else:
        cmd = [electron, app_dir]

    if tab:
        cmd.append(f"--tab={tab}")
    print(f"  Launching Aishe desktop ({dim(app_dir)})")

    # Capture stderr instead of DEVNULL: Electron's Wayland/GPU failures used to
    # vanish here, so a failed launch looked identical to a working one. The
    # child keeps running after we return — this is not a wait.
    log_dir = os.path.join(app_dir, ".logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        log = open(os.path.join(log_dir, "launch.log"), "ab", buffering=0)
    except OSError:
        log = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)
    except OSError as e:
        print(red(f"Could not launch: {e}"))
        sys.exit(1)

    # Give Electron a moment to fail fast. A process that exits immediately is a
    # launch failure, and reporting it beats printing "Launching…" and exiting 0.
    #
    # exit code 0 is NOT a failure here: when the app is already running, the
    # single-instance lock in main.js quits the newcomer cleanly after handing
    # focus to the existing window. Only a non-zero exit is a real problem.
    time.sleep(2.5)
    code = proc.poll()
    if code:
        print(red(f"Aishe desktop exited immediately (code {code})."))
        print(dim(f"  Log: {os.path.join(log_dir, 'launch.log')}"))
        sys.exit(1)

    print(green(f"  Aishe desktop is running (pid {proc.pid})"))


def cmd_desktop(args: Any) -> None:
    """`aishe desktop` — launch the full desktop client."""
    _launch_desktop_app(tab=getattr(args, "tab", None))


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
        print(f"  {status_dot(ok)} {name:20s} {green('UP') if ok else red('DOWN')}{dim(extra)}")

    # 5. Data
    section("Data")
    mem_cnt = mem_count()
    thr_cnt = thr_count()
    print(f"  {bullet('Memory entries')} {cyan(str(mem_cnt))}")
    print(f"  {bullet('Threads')} {cyan(str(thr_cnt))}")

    # 6. STT roundtrip test
    section("STT Roundtrip (Sarvam Saaras v3)")
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
        print(f"  {yellow('⚠ Skipped (no Sarvam key)')}")

    # 7. TTS roundtrip test
    section("TTS Roundtrip (Sarvam Bulbul v3)")
    if tts_health():
        try:
            out = synthesize("test", play=False)
            if out and os.path.exists(out):
                size = os.path.getsize(out)
                os.unlink(out)
                print(f"  {status_dot(True)} {green('TTS responds')} {dim(f'({size} bytes · {tts_info()})')}")
            else:
                print(f"  {status_dot(True)} {green('TTS responds')}")
        except Exception as e:
            print(f"  {status_dot(False)} {red(f'TTS error: {e}')}")
    else:
        print(f"  {yellow('⚠ Skipped (no Sarvam key)')}")

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
#   - openrouter   OpenRouter (OpenAI-compatible)
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
        "label": "Ollama Cloud (:cloud models via local Ollama)",
        "base_url": "http://localhost:11434",
        "needs_key": False,
        "key_default": "ollama",
        "models_url": "/api/tags",
        "ollama": True,
    },
    "openrouter": {
        "label": "OpenRouter (cloud API)",
        "base_url": "https://openrouter.ai/api/v1",
        "needs_key": True,
        "key_hint": "Get a key from https://openrouter.ai/keys",
        "models_url": "/models",
    },
}

_PROVIDER_ORDER = ["sarvam", "ollama-cloud", "openrouter"]


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

    # 6. Restart DeepAgent to apply the provider + model
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
        description="Aishe CLI — privacy-first AI assistant for your terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  aishe                         — interactive streaming chat
  aishe "Tell me a joke"        — one-shot streaming chat
  aishe doctor                  — run diagnostics
  aishe config                  — view config
  aishe config set voice.default_voice M1
  aishe version
""",
    )

    # Bare `aishe` accepts an optional message (one-shot) or none (interactive).
    # Intercept before argparse: if the first arg isn't a known subcommand,
    # treat everything as a message.
    _KNOWN = {"status", "desktop", "memory", "config", "doctor", "version", "setup", "telegram", "gateway", "dashboard", "search", "-h", "--help"}
    _argv = sys.argv[1:]
    if _argv and _argv[0] not in _KNOWN:
        msg = " ".join(_argv)
        _stream_once(msg, resolve_thread_id())
        return

    sub = parser.add_subparsers(dest="command")

    # status
    sub.add_parser("status", help="Check all service statuses")

    # desktop — full GUI covering every CLI capability
    p_desktop = sub.add_parser("desktop", help="Open the Aishe desktop app (chat, voice, memory, sessions, services, config, doctor)")
    p_desktop.add_argument("--tab", default=None, choices=["chat", "memory", "sessions", "keys"], help="Tab to open on launch")

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
        "desktop": cmd_desktop,
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
        dot = status_dot(ok)
        status_text = green("UP") if ok else red("DOWN")
        print(f"  {dot} {name:16s} {status_text}{dim(extra)}")

    section("Voice (Sarvam)")
    if stt_health() and tts_health():
        print(f"  {status_dot(True)} {green('Ready')} {dim(f'· {tts_info()}')}")
    else:
        print(f"  {status_dot(False)} {red('No Sarvam key')} {dim('· set providers.sarvam.api_key')}")

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
