# Aishe — Cross-Repo Bug & Staleness Tracker

Three repos, one product. Last audited: **2026-10-10**.

| Repo | Role | Tests | Status |
|------|------|-------|--------|
| [`aishe-cli`](https://github.com/astromanish/aishe-cli) | CLI + DeepAgent + Telegram | 15 | Clean |
| [`aishe-desktop`](https://github.com/astromanish/aishe-desktop) | Electron client | 46 | Clean |
| [`aishe-web`](https://github.com/astromanish/aishe-web) | Landing page (Netlify) | none | Clean |

Legend: `FIXED` · `OPEN` · `WONTFIX`

---

## Summary of this audit

| # | Issue | Repo | Severity | Status |
|---|-------|------|----------|--------|
| 1 | Three surfaces disagreed about where the backend lives | cli + desktop | **High** | FIXED |
| 2 | Two divergent copies of DeepAgent on disk | cli | **High** | FIXED |
| 3 | Qdrant invisible to CLI — dead vector DB looked healthy | cli | Medium | FIXED |
| 4 | Pricing page never committed — `/pricing` 404s on fresh clone | web | **High** | FIXED |
| 5 | 24 generated `.netlify` artifacts tracked despite gitignore | web | Low | FIXED |
| 6 | `config.load()` shallow-copied `DEFAULT_CONFIG` | cli | Medium | FIXED |
| 7 | `aishe-web` README documented a stack that doesn't exist | web | Medium | FIXED |
| 8 | Six `.bak` files committed to a public repo | cli | Low | FIXED |
| 9 | 5 dead sidecar files shipped | cli | Low | FIXED |
| 10 | `setup.sh` npm-installed a deleted directory | cli | Low | FIXED |
| 11 | macOS `launchctl` branches in a Linux-only script | cli | Low | FIXED |
| 12 | Stale `.env` files holding live API keys, unread by anything | cli | Medium | FIXED |
| 13 | `bug_tracker.md` 2 months stale, 10 phantom commands | cli | Low | FIXED |

---

## The shared core

`aishe_pkg/aishe_core.py` is the single resolver for every surface — deepagent,
desktop, sidecars, and the service registry.

```python
from aishe_pkg import aishe_core
aishe_core.deepagent_dir()    # where server.py lives
aishe_core.desktop_dir()      # the Electron app
aishe_core.telegram_bot()     # the Telegram sidecar
aishe_core.SERVICES           # deepagent :8765 · ollama :11434 · qdrant :6333
aishe_core.describe()         # all of the above + existence flags
```

Resolution order: `AISHE_HOME` → repo checkout → `~/.local/share/aishe-cli`.

Each surface reaches it its own way:
- **desktop** — `lib/core.js` shells out, with the old probe kept as fallback
- **start-all.sh** — a bash helper, also with a fallback so it never hard-fails

**The two-copies bug is now structurally impossible**: a desktop test asserts
`control.paths.DEEPAGENT_DIR === core.deepagentDir()`.

---

## Per-repo detail

### aishe-cli

**FIXED · surfaces disagreed about the backend path**
`aishe-desktop` resolved `~/Documents/projects/aishe-cli/deepagent`,
`start-all.sh` hardcoded `~/.local/share/aishe-cli/deepagent`, and the CLI
probed paths that don't exist on this machine. Two divergent backends raced for
`:8765`; whichever tool started first won, so edits to one copy looked like
they did nothing.

**FIXED · two copies of DeepAgent on disk**
`~/.local/share/aishe-cli/deepagent` (Sep 30) and
`~/Documents/projects/aishe-cli/deepagent` (Oct 10). `diff` said identical, so
the divergence was latent — the next edit would have split them. Deleted the
stale copy; the repo checkout is now the only one.

**FIXED · Qdrant was CLI-invisible**
Only the desktop managed it; neither `aishe doctor` nor `aishe status` probed
it, and `start-all.sh` didn't start it. A dead vector DB looked like a healthy
system while memory silently fell back to JSONL. Now in `SERVICES` and checked
by all three.

**FIXED · BUG-001 shallow config copy**
`dict(DEFAULT_CONFIG)` shares nested dicts, so mutating a returned config
poisoned the defaults for the rest of the process. Now `copy.deepcopy`.

**FIXED · dead code removed**
`stt_server.py`, `tts_server.py` (pre-Sarvam local voice), `sarvam_stt_server.py`,
`sarvam_tts_server.py` (orphaned cloud adapters), `aishe-voice` (a port-swap
shim that existed only for the deleted electron-live app).

**FIXED · `setup.sh` installed a deleted directory**
The electron-live `npm install` block could never pass — it always warned.

**FIXED · `start-all.sh` had macOS `launchctl` branches**
On Linux `launchctl` does nothing, so those services silently never started.
Replaced with Qdrant + an `ollama serve` fallback.

**FIXED · stale `.env` files with live API keys**
`runtime/deepagent.env` and `deepagent-sarvam.env` held live keys and were read
by nothing — the desktop reads `~/aishe/deepagent.env` (which doesn't exist, so
it falls back to config.yaml). Deleted.

**OPEN · the CLI has almost no tests**
`tests/test_aishe_core.py` (15 tests) covers the resolver only. The desktop has
46. A regression in `aishe_pkg/` ships unnoticed — worth filling before a real
daily user.

**OPEN · version still reports `2.0.0`**

---

### aishe-desktop

**FIXED · model switching reported failure after a successful restart**
Root cause was an `exited` Promise left on the `model:set` reply. Electron
serialises IPC replies with structured-clone, which cannot clone a Promise — so
the main process threw `An object could not be cloned` *after* DeepAgent had
come back healthy. `delete res.exited` before replying; a test guards it.

**FIXED · deepagent path was broken by the project move**
The app used to live inside aishe-cli, so `REPO/deepagent` was right. After the
move it pointed at nothing, and every `serviceAction("deepagent", …)` threw
"venv missing" — which is what made model switching fail while looking like a
restart had happened. Now resolves through `aishe_core`.

**FIXED · silent service death**
`spawnDetached` used `stdio: "ignore"`, so a service that failed on startup died
silently while the caller reported success. stderr is captured to a log now,
and `waitForPort` does a real HTTP health check plus early-death detection.

**FIXED · unvalidated model ids**
The picker offered ids no provider serves; the switch wrote it to config and
restarted, then failed on the first real request. `model:set` validates against
the provider's live catalogue first, with near-match suggestions.

**FIXED · service registry**
Read from `aishe_core.SERVICES`, so Qdrant appears here exactly as in the CLI.
A new `core:paths` IPC channel exposes resolved paths and whether the deepagent
venv exists.

---

### aishe-web

**FIXED · the pricing page was never committed**
`src/routes/pricing/+page.svelte` was untracked even though `pricing.ts` and the
nav link were committed. `/pricing` 404s on any fresh clone. Committed.

**FIXED · 24 generated `.netlify` artifacts tracked**
`.gitignore` lists `.netlify`, but the files were committed before the rule
existed — hashed-filename SSR bundles churning in every diff. Untracked;
verified `npm run build` regenerates them from scratch.

**FIXED · README documented a stack that doesn't exist**
Claimed `adapter-node` (actually `adapter-netlify`), `better-sqlite3` and a
waitlist DB (both absent), `lucide-svelte` (absent), and 7 components that were
never in the repo. Rewritten from the actual tree.

**OPEN · no test suite at all**
Not urgent for a static landing page, but `npm run check` (svelte-check) is not
in CI either.

---

## Cross-repo consistency — verified

| Concern | Status |
|---------|--------|
| One conversation thread (`main`) across CLI, desktop, Telegram | ✓ |
| Shared SQLite at `~/aishe/aishe.db` | ✓ |
| One config file `~/.config/aishe/config.yaml` | ✓ |
| One service registry, read by CLI + desktop | ✓ |
| One path resolver (`aishe_core`) | ✓ |
| Voice via Sarvam cloud, no local ports | ✓ |

---

## Open items

1. **aishe-cli needs a real test suite** — biggest gap before a daily user.
2. **Bump the version** — still `2.0.0`.
3. **aishe-web has no CI** — `npm run check` would catch type drift.
