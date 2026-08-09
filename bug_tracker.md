# Aishe CLI — Bug Tracker

Repo: `~/Documents/aishe/aishe-cli` · Branch: `main` · Last audited: 2026-08-09
Scope: `aishe` + `aishe_pkg/*.py`

Status legend: `OPEN` = unfixed · `FIXED` = resolved this session · `WONTFIX` = accepted/known · `REMOVED` = feature deleted

---

## Cleanup — 2026-08-09

Two rounds of feature removal per user request:

**Round 1 — removed:**
- **Intent Lab** — `intent` subcommand, `cmd_intent`, `intent_lab/` data dir, completions, README
- **Ollama management** — `aishe ollama` subcommand, `ollama.py`, completions, README. **Ollama kept as the LLM backend** (DeepAgent still talks to `:11434`; `status`/`doctor` still health-check it).
- **Pet blob** — `pet.py`, `blob.py`, `pet_attach.py`, all `send_signal`/`PetState` hooks, completions, README, `util.welcome` hint.

**Round 2 — removed standalone subcommands (underlying modules kept):**
- **`repl`** — `cmd_repl` deleted. `threads.py` kept (chat/stream/live still persist conversations).
- **`threads`** — `cmd_threads` deleted. `threads.py` module kept for chat/stream/live persistence.
- **`voice`** — `cmd_voice` deleted. `voice.py` module kept (live uses STT/TTS).
- **`export`** — `cmd_export` deleted entirely.
- **`search`** — `cmd_search` deleted entirely.
- **`completions`** — `cmd_completions` deleted entirely.

**Remaining commands:** `status`, `chat`, `stream`, `live`, `memory`, `config`, `doctor`, `version`.

Files deleted: `aishe_pkg/pet.py`, `aishe_pkg/blob.py`, `aishe_pkg/pet_attach.py`, `aishe_pkg/ollama.py`.

Bugs that became moot (feature removed): BUG-004 (ollama dead code), BUG-006 (pet path), BUG-007 (intent path).

---

## OPEN — Confirmed bugs

### BUG-009 · Thread persistence broken — user & assistant turns land in different threads
- **File:** `aishe_pkg/threads.py:86-109` (`ensure_thread` / `create` / `add_message`)
- **Severity:** **High** (core feature broken)
- **Repro:** `aishe chat "What is (15*4)/3?"` → creates TWO thread files: one titled `"cli"` with only the assistant message, another auto-titled from the user message with only the user message.
- **Cause:** `add_message(tid, ...)` → `ensure_thread(tid)` → `get(tid)` looks for a file named `<tid>.json` (e.g. `cli.json`), but `create()` always generates a fresh `thr_{uuid}` id and stores under that name. The logical thread id is never used as the actual storage key.
- **Impact:** Every chat/stream/live turn fragments into separate 1-message threads. Conversation history is never actually preserved.
- **Fix:** Make `create()` honor a caller-supplied id (store under `<tid>.json` when the tid is a stable logical name like `cli`/`live`), or have `ensure_thread` map the logical tid to the existing file.

### BUG-001 · `config.load()` shallow-copies DEFAULT_CONFIG → nested defaults get mutated
- **File:** `aishe_pkg/config.py:73`
- **Severity:** Medium
- **Fix:** Use `copy.deepcopy(DEFAULT_CONFIG)` in `load()`.

### BUG-005 · `_slugify()` in threads.py is dead code
- **File:** `aishe_pkg/threads.py:28-33`
- **Severity:** Low (dead code)
- **Fix:** Remove, or use it to sanitize thread titles for filenames in `export_markdown`.

---

## FIXED — Resolved this session

### BUG-008 · REPL `/delete` crashed with NameError (delete not imported)
- **File:** `aishe_pkg/cli.py:24,228`
- **Severity:** High (crash)
- **Fix:** Added `delete as thr_delete` to the import and changed the call site to `thr_delete(target)`. (Note: `repl` subcommand since removed, but the fix was correct at the time.)

---

## Notes / observations (not bugs)

- `aishe` version still reports `v2.0.0` despite multiple feature commits. Consider bumping to `v2.1.0`.
- `voice speak`/`voice transcribe`/`voice status` subcommands removed — voice is now only reachable via `aishe live`.
- Thread `--rename --title`, `--new`, `--show`, `--delete` subcommands removed — thread management is now implicit (chat/stream/live auto-create threads).
