# Aishe CLI — Bug Tracker

Repo: `~/Documents/aishe/aishe-cli` · Branch: `main` · Last audited: 2026-08-09
Scope: `aishe` + `aishe_pkg/*.py`

Status legend: `OPEN` = unfixed · `FIXED` = resolved this session · `WONTFIX` = accepted/known · `REMOVED` = feature deleted

---

## Cleanup — 2026-08-09

Four rounds of feature removal per user request:

**Round 1 — removed:**
- **Intent Lab** — `intent` subcommand, `cmd_intent`, `intent_lab/` data dir, completions, README
- **Ollama management** — `aishe ollama` subcommand, `ollama.py`, completions, README. **Ollama kept as the LLM backend** (DeepAgent still talks to `:11434`; `status`/`doctor` still health-check it).
- **Pet blob** — `pet.py`, `blob.py`, `pet_attach.py`, all `send_signal`/`PetState` hooks, completions, README, `util.welcome` hint.

**Round 2 — removed standalone subcommands (underlying modules kept):**
- **`repl`** — `cmd_repl` deleted. `threads.py` kept (stream/live still persist conversations).
- **`threads`** — `cmd_threads` deleted. `threads.py` module kept for stream/live persistence.
- **`voice`** — `cmd_voice` deleted. `voice.py` module kept (live uses STT/TTS).
- **`export`** — `cmd_export` deleted entirely.
- **`search`** — `cmd_search` deleted entirely.
- **`completions`** — `cmd_completions` deleted entirely.

**Round 3 — removed `chat`, added bare-`aishe` streaming:**
- **`chat`** — `cmd_chat` deleted. One-shot non-streaming chat removed.
- **Bare `aishe`** (no subcommand) now launches an **interactive streaming loop** (`cmd_repl_stream`): type a message, get a streaming reply, repeat. `/exit` or Ctrl+C to quit.

**Round 4 — removed `stream`, unified into bare `aishe`, moved data to `~/aishe`:**
- **`stream`** — `cmd_stream` deleted. Streaming is now the default behavior of bare `aishe`.
- **Bare `aishe`** now handles both modes via `_stream_once`:
  - `aishe "message"` → one-shot streaming reply
  - `echo "hi" | aishe` → one-shot from stdin (non-TTY)
  - `aishe` (no args, TTY) → interactive streaming loop
- **Data dir** — all user data now lives in **`~/aishe`** on every platform (was `~/Library/Application Support/aishe` on macOS, `~/.local/share/aishe` on Linux, `~/AppData/Local/aishe` on Windows). Updated `config._default_data_dir()`, `threads.py`, `memory.py`, `setup.sh`. Existing data migrated to `~/aishe`.

**Remaining commands:** `status`, `live`, `memory`, `config`, `doctor`, `version` + bare `aishe` (streaming chat).

Files deleted: `aishe_pkg/pet.py`, `aishe_pkg/blob.py`, `aishe_pkg/pet_attach.py`, `aishe_pkg/ollama.py`.

Bugs that became moot (feature removed): BUG-004 (ollama dead code), BUG-006 (pet path), BUG-007 (intent path).

---

## OPEN — Confirmed bugs

### BUG-009 · Thread persistence broken — user & assistant turns land in different threads
- **File:** `aishe_pkg/threads.py` (`ensure_thread` / `create` / `add_message`)
- **Severity:** **High** (core feature broken)
- **Repro:** `aishe "What is (15*4)/3?"` → creates TWO thread files: one titled `"cli"` with only the assistant message, another auto-titled from the user message with only the user message.
- **Cause:** `add_message(tid, ...)` → `ensure_thread(tid)` → `get(tid)` looks for a file named `<tid>.json` (e.g. `cli.json`), but `create()` always generates a fresh `thr_{uuid}` id and stores under that name. The logical thread id is never used as the actual storage key.
- **Impact:** Every bare-aishe/live turn fragments into separate 1-message threads. Conversation history is never actually preserved.
- **Fix:** Make `create()` honor a caller-supplied id (store under `<tid>.json` when the tid is a stable logical name like `cli`/`live`), or have `ensure_thread` map the logical tid to the existing file.

### BUG-001 · `config.load()` shallow-copies DEFAULT_CONFIG → nested defaults get mutated
- **File:** `aishe_pkg/config.py:73`
- **Severity:** Medium
- **Fix:** Use `copy.deepcopy(DEFAULT_CONFIG)` in `load()`.

---

## FIXED — Resolved this session

### BUG-005 · `_slugify()` in threads.py was dead code
- **File:** `aishe_pkg/threads.py`
- **Severity:** Low (dead code)
- **Fix:** Removed `_slugify()` (and the now-unused `re` import) during Round 2 cleanup.

### BUG-008 · REPL `/delete` crashed with NameError (delete not imported)
- **File:** `aishe_pkg/cli.py`
- **Severity:** High (crash)
- **Fix:** Added `delete as thr_delete` to the import and changed the call site to `thr_delete(target)`. (Note: `repl` subcommand since removed, but the fix was correct at the time.)

---

## Notes / observations (not bugs)

- `aishe` version still reports `v2.0.0` despite multiple feature commits. Consider bumping to `v2.1.0`.
- `voice speak`/`voice transcribe`/`voice status` subcommands removed — voice is now only reachable via `aishe live`.
- Thread management subcommands removed — thread persistence is now implicit (bare-aishe/live auto-create threads).
- Bare `aishe` uses `getattr(args, "thread", None) or "cli"` since the no-subcommand namespace has no `--thread` flag.
- Bare `aishe` one-shot intercepts `sys.argv` before argparse (first arg not a known subcommand → treated as message), because argparse can't mix a free-form positional with subparsers.
- Data dir is now `~/aishe` on all platforms; config `data.dir` key points there.
