"""Pet — the aishe blob state, signal channel, and animation loop.

The pet has a *mood* (idle/listening/thinking/speaking/error/greeting) and
*persistent state* (size, streak, milestones, etc.). Background processes
can poke the foreground pet's mood by writing JSONL signals to a log file
that the foreground listener tails.

For Phase 1+2 the focus is:
  - A clean state machine
  - Foreground animation loop (`cmd_pet` with no args)
  - One-shot static frame (`aishe pet status`)
  - Cross-process signal channel via JSONL tail file
  - Persistence (state.json)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import blob
from .config import get
from .util import bold, cyan, dim, green, red, yellow, status_dot, section, header, bullet

# ─── Paths ──────────────────────────────────────────────────────────────────

def _data_dir() -> Path:
    base = Path(get("data.dir", str(Path.home() / ".local" / "share" / "aishe")))
    return base

PET_DIR = _data_dir() / "pet"
STATE_FILE = PET_DIR / "state.json"
SIGNAL_LOG = PET_DIR / "signals.jsonl"


def ensure_dirs() -> None:
    PET_DIR.mkdir(parents=True, exist_ok=True)


# ─── State ──────────────────────────────────────────────────────────────────

VALID_STATES = ("idle", "listening", "thinking", "speaking", "error", "greeting")


@dataclass
class PetState:
    """Persistent pet state, saved to state.json."""
    born: str = ""
    last_seen: str = ""
    last_state: str = "idle"
    size: float = 1.0               # grows ~0.05/day, capped at 2.0
    color_phase: float = 0.0        # cycles through palette slowly
    streak_days: int = 0
    n_chats: int = 0
    n_voice_sessions: int = 0
    total_tokens: int = 0
    milestones: List[str] = field(default_factory=list)
    name: str = ""                  # user-set name (for greeting)
    enabled: bool = True            # user can disable the pet

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def load(cls) -> "PetState":
        if not STATE_FILE.exists():
            return cls()
        try:
            with open(STATE_FILE) as f:
                # Try to lock briefly to avoid reading mid-write
                import fcntl
                try:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    data = f.read()
                finally:
                    try:
                        fcntl.flock(f, fcntl.LOCK_UN)
                    except Exception:
                        pass
            if not data.strip():
                return cls()
            data = json.loads(data)
            # Filter to known fields to survive future schema changes
            known = {f for f in cls.__dataclass_fields__}
            filtered = {k: v for k, v in data.items() if k in known}
            return cls(**filtered)
        except (json.JSONDecodeError, TypeError):
            return cls()

    def save(self) -> None:
        ensure_dirs()
        # Initialize born on first save
        if not self.born:
            self.born = datetime.now().isoformat(timespec="seconds")
        # Update last_seen
        self.last_seen = datetime.now().date().isoformat()
        # Atomic write with file lock to prevent races between the
        # foreground pet and command handlers.
        import fcntl
        # Make sure the file exists before opening for lock
        if not STATE_FILE.exists():
            STATE_FILE.touch()
        with open(STATE_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                tmp = STATE_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.to_dict(), indent=2))
                tmp.replace(STATE_FILE)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


# ─── Milestones ─────────────────────────────────────────────────────────────

MILESTONE_DEFS = [
    ("first_chat", "First chat", "n_chats >= 1"),
    ("ten_chats", "10 chats", "n_chats >= 10"),
    ("hundred_chats", "100 chats", "n_chats >= 100"),
    ("first_voice", "First voice session", "n_voice_sessions >= 1"),
    ("streak_3", "3-day streak", "streak_days >= 3"),
    ("streak_7", "7-day streak", "streak_days >= 7"),
]


def check_milestones(state: PetState) -> List[str]:
    """Return any new milestones the pet has just unlocked."""
    new: List[str] = []
    for key, _label, cond in MILESTONE_DEFS:
        if key in state.milestones:
            continue
        try:
            # Use the state's actual attributes as locals
            if eval(cond, {}, state.to_dict()):  # noqa: S307 — trusted source
                new.append(key)
        except Exception:
            pass
    return new


def unlock_milestone(state: PetState, key: str) -> bool:
    if key not in state.milestones:
        state.milestones.append(key)
        return True
    return False


# ─── Signal channel ────────────────────────────────────────────────────────

def send_signal(state_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Write a signal to the log. Safe to call from any process."""
    if state_name not in VALID_STATES:
        return
    try:
        ensure_dirs()
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "state": state_name,
            "payload": payload or {},
        }
        with open(SIGNAL_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # signals are best-effort


def read_signals(since_line: int = 0) -> tuple:
    """Read new signals from the log. Returns (new_signals, new_line_count)."""
    if not SIGNAL_LOG.exists():
        return [], since_line
    try:
        with open(SIGNAL_LOG) as f:
            lines = f.readlines()
    except Exception:
        return [], since_line
    if since_line >= len(lines):
        return [], since_line
    new_lines = lines[since_line:]
    signals: List[Dict[str, Any]] = []
    for line in new_lines:
        line = line.strip()
        if not line:
            continue
        try:
            signals.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return signals, len(lines)


# ─── Streak / growth bookkeeping ───────────────────────────────────────────

def _today_str() -> str:
    return datetime.now().date().isoformat()


def update_streak(state: PetState) -> None:
    """Update streak_days based on last_seen vs today. Mutates state."""
    today = _today_str()
    if not state.last_seen:
        state.streak_days = 1
    else:
        try:
            last = datetime.fromisoformat(state.last_seen).date()
            now_d = datetime.now().date()
            delta = (now_d - last).days
            if delta == 0:
                # same day, no change
                pass
            elif delta == 1:
                state.streak_days += 1
            else:
                state.streak_days = 1
        except ValueError:
            state.streak_days = 1
    state.last_seen = today


def update_streak_save(state: PetState) -> None:
    """Update streak and save. Convenience for command handlers."""
    update_streak(state)
    state.save()


def grow(state: PetState) -> None:
    """Slight growth on activity, capped at 2.0."""
    if state.size < 2.0:
        state.size = min(2.0, state.size + 0.01)


# ─── Animation loop ────────────────────────────────────────────────────────

class Pet:
    """Foreground pet — runs an animation loop and listens for signals."""

    def __init__(self, fps: int = 12, width: int = 36, height: int = 12):
        self.fps = fps
        self.width = width
        self.height = height
        self.state_name = "idle"
        self.payload: Dict[str, Any] = {}
        self._stop = threading.Event()
        self._signal_thread: Optional[threading.Thread] = None
        self._state = PetState.load()
        if not self._state.born:
            self._state.born = datetime.now().isoformat(timespec="seconds")
        update_streak(self._state)
        self._state.save()

    def stop(self) -> None:
        self._stop.set()

    def set_mood(self, state_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if state_name in VALID_STATES:
            self.state_name = state_name
            self.payload = payload or {}
            # Persist last_state to disk. Use a read-modify-write under
            # flock so we don't clobber concurrent updates from other
            # processes (chat handler, live handler, etc.) that bump
            # n_chats / n_voice_sessions.
            import fcntl
            try:
                if not STATE_FILE.exists():
                    STATE_FILE.touch()
                with open(STATE_FILE, "r+") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        raw = f.read()
                        data = json.loads(raw) if raw.strip() else {}
                    except Exception:
                        data = {}
                    # Re-hydrate a full PetState from disk so we preserve
                    # all fields (n_chats, milestones, etc.)
                    known = {k for k in PetState.__dataclass_fields__}
                    self._state = PetState(
                        **{k: v for k, v in data.items() if k in known}
                    )
                    self._state.last_state = state_name
                    self._state.last_seen = datetime.now().date().isoformat()
                    tmp = STATE_FILE.with_suffix(".tmp")
                    tmp.write_text(json.dumps(self._state.to_dict(), indent=2))
                    tmp.replace(STATE_FILE)
                    fcntl.flock(f, fcntl.LOCK_UN)
            except Exception:
                # Best-effort: still update in-memory state
                self._state.last_state = state_name

    def _signal_listener(self) -> None:
        """Tail the signal log and update mood on new entries."""
        seen = 0
        # Read existing signals to avoid replaying old ones
        _, seen = read_signals(seen)
        while not self._stop.is_set():
            signals, seen = read_signals(seen)
            for sig in signals:
                self.set_mood(sig.get("state", "idle"), sig.get("payload"))
            # Refresh memory count every cycle
            self.payload["mem_count"] = mem_count_safe()
            # also check periodically
            self._stop.wait(0.2)

    def render(self, t: float) -> str:
        """Render a single frame at time t (seconds since start)."""
        params = blob.BlobParams(
            width=self.width,
            height=self.height,
            seed=int(time.time()) & 0xFFFF,
        )
        # Modulate by state
        if self.state_name == "thinking":
            params.n_points = 12
            params.orbit_speed = 0.18
        elif self.state_name == "listening":
            params.n_points = 10
            params.wobble = 0.18
        elif self.state_name == "speaking":
            params.height = max(6, self.height - 2)
            params.orbit_speed = 0.22
        elif self.state_name == "error":
            params.n_points = 5
            params.wobble = 0.30
        elif self.state_name == "greeting":
            params.n_points = 10
            params.wobble = 0.20
        else:  # idle
            params.n_points = 8
            params.wobble = 0.08

        # Size scales the orbit radius slightly
        params.orbit_radius = 0.45 * (0.9 + 0.1 * (self._state.size - 1.0))

        # Palette shift by state
        if self.state_name == "error":
            params.palette = [
                (30, 8, 8), (100, 30, 30), (170, 60, 50),
                (210, 120, 80), (230, 170, 100), (240, 210, 130),
            ]
        elif self.state_name == "thinking":
            params.palette = [
                (15, 12, 30), (45, 30, 70), (90, 60, 110),
                (140, 100, 160), (200, 160, 180), (230, 200, 180),
            ]
        elif self.state_name == "speaking":
            params.palette = [
                (20, 15, 5), (70, 50, 15), (130, 95, 30),
                (190, 145, 55), (220, 175, 80), (240, 210, 130),
            ]
        elif self.state_name == "greeting":
            params.palette = [
                (15, 25, 20), (50, 90, 70), (90, 150, 110),
                (160, 200, 130), (210, 220, 150), (240, 230, 170),
            ]

        points = blob.make_points(params, t=0.0)
        # Advance points by elapsed time so the field animates
        steps = int(t * 10)
        for _ in range(steps):
            for p in points:
                p.tick(t, params.wobble)
        return blob.render_frame(
            params, points,
            use_color=blob._term_supports_color(),
            use_truecolor=blob._term_supports_truecolor(),
        )

    def run(self) -> None:
        """Run the animation loop until stop() is called."""
        # Hide cursor, save screen
        sys.stdout.write("\033[?25l\033[?1049h")
        sys.stdout.flush()
        self._signal_thread = threading.Thread(target=self._signal_listener, daemon=True)
        self._signal_thread.start()

        start = time.time()
        frame_interval = 1.0 / self.fps
        next_frame = start
        last_state = self.state_name
        last_payload_key = ""

        try:
            while not self._stop.is_set():
                now = time.time()
                if now < next_frame:
                    self._stop.wait(next_frame - now)
                    continue
                t = now - start
                frame = self.render(t)

                # Build the info line
                state_label = self.state_name.upper()
                extra = ""
                if self.state_name == "thinking":
                    tok = self.payload.get("tokens", 0)
                    extra = f" · {tok} tok" if tok else ""
                elif self.state_name == "listening":
                    extra = " · mic"
                elif self.state_name == "speaking":
                    extra = " · tts"
                elif self.state_name == "error":
                    err = self.payload.get("error", "")
                    extra = f" · {err}" if err else ""

                info = (
                    f"  {state_label}{extra}  ·  "
                    f"size {self._state.size:.2f}  ·  "
                    f"mem {self.payload.get('mem_count', '—')}  ·  "
                    f"day {self._state.streak_days}  ·  "
                    f"chats {self._state.n_chats}"
                )

                # Position cursor home, write frame, write info, restore
                lines = frame.split("\n")
                buf = "\033[H"
                for ln in lines:
                    buf += ln + "\n"
                buf += info + "\n"
                buf += dim("  Ctrl+C to exit")
                sys.stdout.write(buf)
                sys.stdout.flush()

                next_frame += frame_interval
                # if we're falling behind, reset (don't try to catch up)
                if next_frame < now - 0.5:
                    next_frame = now + frame_interval
        finally:
            # Restore screen, show cursor
            sys.stdout.write("\033[?1049l\033[?25h")
            sys.stdout.flush()


# ─── Commands ───────────────────────────────────────────────────────────────

def cmd_pet(args: Any) -> None:
    """Dispatch subcommands for `aishe pet`."""
    state = PetState.load()

    # No subcommand: foreground animation
    if not getattr(args, "pet_action", None):
        if not state.enabled:
            print(yellow("Pet is disabled. Run: aishe pet enable"))
            sys.exit(0)
        pet = Pet(fps=args.fps or 12, width=args.width or 36, height=args.height or 12)
        # Send greeting on startup
        send_signal("greeting", {"mem_count": mem_count_safe()})
        try:
            pet.run()
        except KeyboardInterrupt:
            pass
        finally:
            # Idle out on exit
            send_signal("idle")
        return

    action = args.pet_action

    if action == "status":
        _cmd_pet_status(args, state)
    elif action == "inspect":
        _cmd_pet_inspect(args, state)
    elif action == "reset":
        _cmd_pet_reset(args, state)
    elif action == "enable":
        state.enabled = True
        state.save()
        print(green("Pet enabled."))
    elif action == "disable":
        state.enabled = False
        state.save()
        print(green("Pet disabled. Run `aishe pet` to bring it back."))
    elif action == "signal":
        # manual signal poke
        s = args.state
        if not s:
            print(red("Usage: aishe pet signal --state {idle|thinking|listening|speaking|error|greeting}"))
            sys.exit(1)
        if s not in VALID_STATES:
            print(red(f"Invalid state. Use one of: {', '.join(VALID_STATES)}"))
            sys.exit(1)
        send_signal(s)
        print(green(f"Sent signal: {s}"))
    elif action == "milestones":
        _cmd_pet_milestones(args, state)
    elif action == "attach":
        from .pet_attach import cmd_pet_attach
        cmd_pet_attach(args)
    elif action == "detach":
        from .pet_attach import cmd_pet_detach
        cmd_pet_detach(args)
    elif action == "watch":
        from .pet_attach import cmd_pet_watch
        cmd_pet_watch(args)
    else:
        print(red(f"Unknown pet action: {action}"))


def mem_count_safe() -> int:
    try:
        from .memory import count as mem_count
        return mem_count()
    except Exception:
        return 0


def _cmd_pet_status(args: Any, state: PetState) -> None:
    """One-shot frame + summary."""
    mood = args.mood or state.last_state or "idle"
    width = args.width or 36
    height = args.height or 12
    print()
    print(bold(f"  ◉ Aishe — {mood}"))
    print()
    print(blob.static_frame(
        width=width, height=height, state=mood, seed=int(time.time()) & 0xFFFF,
    ))
    print()
    print(dim(f"  size {state.size:.2f}  ·  "
              f"day {state.streak_days}  ·  "
              f"chats {state.n_chats}  ·  "
              f"voice {state.n_voice_sessions}  ·  "
              f"mem {mem_count_safe()}"))
    print(dim(f"  born {state.born or '—'}  ·  last {state.last_seen or '—'}"))
    if state.milestones:
        print(dim(f"  milestones: {', '.join(state.milestones)}"))
    print()


def _cmd_pet_inspect(args: Any, state: PetState) -> None:
    """Dump all state as JSON for the dev who wants to see everything."""
    data = state.to_dict()
    data["data_dir"] = str(PET_DIR)
    data["state_file"] = str(STATE_FILE)
    data["signal_log"] = str(SIGNAL_LOG)
    data["valid_states"] = list(VALID_STATES)
    print(json.dumps(data, indent=2))


def _cmd_pet_reset(args: Any, state: PetState) -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    if SIGNAL_LOG.exists():
        SIGNAL_LOG.unlink()
    print(green("Pet state and signals cleared."))


def _cmd_pet_milestones(args: Any, state: PetState) -> None:
    print(bold("Milestones"))
    print(dim("─" * 40))
    for key, label, _cond in MILESTONE_DEFS:
        unlocked = key in state.milestones
        print(f"  {status_dot(unlocked)} {cyan(label)}  {dim('· ' + key)}")
