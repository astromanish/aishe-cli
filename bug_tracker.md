# Aishe — Bug Tracker

Scope: `aishe_pkg/*.py`, `deepagent/`, `sidecars/`, `aishe-desktop/`
Last audited: 2026-10-10

Status legend: `OPEN` = unfixed · `FIXED` = resolved · `REMOVED` = feature deleted

---

## FIXED — 2026-10-10 · single shared core

The audit that prompted `aishe_pkg/aishe_core.py`. Everything below is fixed.

### FIXED · Three surfaces disagreed about where the backend lives
- **Severity:** High — silently ran two different copies of DeepAgent
- `aishe-desktop/lib/control.js` resolved `~/Documents/projects/aishe-cli/deepagent`;
  `start-all.sh` hardcoded `~/.local/share/aishe-cli/deepagent`; the CLI's own
  lookups probed paths that don't exist on this machine.
- **Impact:** two divergent backends racing for `:8765`. The one that won was
  decided by whichever tool started first, so edits to one copy appeared to do
  nothing.
- **Fix:** `aishe_pkg/aishe_core.py` is now the single resolver. The desktop
  reaches it through `aishe-desktop/lib/core.js`; `start-all.sh` calls it via a
  bash helper with a hardcoded fallback so the script never hard-fails.
- **Test:** the desktop asserts both paths are identical
  (`control: deepagent resolves to the same place the CLI resolves it`);
  `aishe-cli/tests/test_aishe_core.py` covers resolution and `AISHE_HOME`.

### FIXED · `aishe doctor` and `aishe status` never checked Qdrant
- **Severity:** Medium — a dead vector DB looked like a healthy system
- Only the desktop managed Qdrant; neither CLI command probed it. Memory
  silently degraded to the JSONL fallback with nothing reporting why.
- **Fix:** Qdrant is in the shared `SERVICES` registry, both commands probe it,
  and `start-all.sh` starts it.

### FIXED — 2026-10-10 · bugs found by the new test suite

### FIXED · `add_message()` raised NameError on every first message
- **Severity:** High — found by the new tests, not by usage
- `add_message` ended with `return get(thread_id)`, but `threads.py` has no
  `get()` — the name resolved to the `from .config import get` import and raised
  `NameError` the first time a thread was written.
- **Fix:** return the persisted thread row directly.

### FIXED · `AISHE_HOME` did not relocate the data dir
- **Severity:** High — tests were writing into the live `~/aishe/aishe.db`
- `DATA_DIR = Path(get("data.dir", …))` reads the user's real config.yaml first,
  which hardcodes an absolute path, so the `AISHE_HOME` override never applied.
  The same bug meant `memory.add()` reached the live DeepAgent → mem0 → Qdrant,
  so a test run could POST junk facts into real memory and `clear()` would wipe it.
- **Fix:** `threads._resolve_data_dir()` checks `AISHE_HOME` before the config
  file; `config._default_data_dir()` honours it too; `AISHE_MEMORY_OFFLINE=1`
  forces the JSONL fallback. The harness sets both.

### FIXED · BUG-001 · `config.load()` shallow-copied DEFAULT_CONFIG
- **Severity:** Medium
- `dict(DEFAULT_CONFIG)` copies only the top level, so nested dicts (`providers`,
  `services`, `voice`) were shared. Mutating a nested value on a returned config
  poisoned the defaults for the rest of the process.
- **Fix:** `copy.deepcopy(DEFAULT_CONFIG)`.

### REMOVED · dead code shipped in the repo
- `sidecars/stt_server.py`, `sidecars/tts_server.py` — the pre-Sarvam local voice
  servers (`:5093`/`:8766`). Voice is cloud-only now; nothing referenced them.
- `sidecars/sarvam_stt_server.py`, `sidecars/sarvam_tts_server.py` — orphaned
  cloud adapters. `aishe_pkg/voice.py` calls Sarvam directly.
- `sidecars/aishe-voice` — a port-swap shim that existed only for the deleted
  `electron-live` app.
- `setup.sh` — the electron-live `npm install` block (could never pass, always
  warned) and the macOS `launchctl` STT/TTS branches in `start-all.sh`.

### FIXED · stale documentation
- `README.md` documented `aishe live` and `electron-live`, both deleted.
- `sidecars/README.md` documented the removed local voice stack in full.
- This tracker was last audited 2026-08-09 and described 10 removed commands as
  current.

---

## FIXED — earlier sessions

### FIXED · BUG-009 · thread persistence fragmented every turn
- User and assistant turns landed in different threads; `add_message` looked for
  `<tid>.json` while `create()` always generated a fresh `thr_{uuid}`.
- **Fix:** single shared thread (`main`) across CLI, desktop and Telegram.
- **Verified:** all 44 messages in `~/aishe/aishe.db` are under `main`; zero
  fragmented thread files.

### FIXED · Telegram sends with `parse_mode` unset
- Aishe's Markdown (`**bold**`) is not valid Telegram Markdown. Setting the mode
  made the API reject the send, so the bot looked like it had hung.
- **Fix:** `parse_mode` is left null; tool calls are rendered as readable
  one-liners by `describe_tool(name, args)`.

---

## OPEN

### The CLI now has a test suite — 70 assertions, 4 suites

`python tests/run.py` runs `test_aishe_core.py` (15), `test_config.py` (26),
`test_threads.py` (16) and `test_memory.py` (13). Tests run against a throwaway
`AISHE_HOME` with `AISHE_MEMORY_OFFLINE=1`, so they never touch the real
`~/aishe` or the live mem0/Qdrant store.

Still uncovered: `aishe_pkg/voice.py`, `aishe_pkg/util.py`,
`aishe_pkg/dashboard.py`, `deepagent/server.py`, `deepagent/tools.py` and
`sidecars/telegram_bot.py`.

---

## Notes

- `aishe version` reports `2.0.0`. Consider bumping.
- Voice config once drifted to `language_code: bn-IN` / `stt_mode: translate`,
  which surfaced as an unrelated test failure. Anything writing voice config
  should go through the same validation the model picker now uses.
