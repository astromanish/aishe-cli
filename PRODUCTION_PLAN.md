# Aishe CLI — Production Readiness Plan

Ordered by risk. Items 1 and 2 are **done** (see the Completed section);
everything below is planned.

Status legend: `DONE` · `PLANNED` · `WONTFIX`

---

## DONE

### 1 · `run_command` was unrestricted shell execution
`deepagent/tools.py` ran `subprocess.run(cmd, shell=True)` with no checks, and
safety was only a line in the system prompt asking the model politely to
"confirm with the user" before destructive commands. That is not enforcement —
a hallucination, or a prompt injection smuggled through `web_extract` output,
had the same access as the user.

Now `deepagent/command_policy.py` gates every call in three tiers:
**allow** (read-only, runs unattended), **confirm** (needs explicit user
approval), **deny** (never runs, no override). Anything unrecognised fails
closed. Read-only subcommand forms of confirm-tier tools run unattended, so
`git status` works while `git push` asks. Configurable under
`security.commands` in config.yaml; timeout is clamped to 120s.

154 assertions in `deepagent/test_command_policy.py` cover ~40 attack cases
including chained commands (`ls && rm -rf ~`), force-pushes, privilege
escalation, and remote-script-to-shell pipes.

### 2 · Markdown URL scheme restriction — verified already correct
The audit flagged `javascript:` URLs as reachable through model output. On
closer inspection the link regex at `renderer/app.js:44` already requires
`https?://`, so non-http schemes never become links at all. `escapeHtml` alone
would not have been enough — it stops HTML injection but leaves the scheme
intact. Two regression tests now pin the scheme restriction and the
escape-before-parse ordering.

---

## PLANNED — blocking for a real user

### 3 · Packaging
Neither repo can be installed. The CLI has no `setup.py`/`pyproject.toml`, and
the desktop has no electron build script. "Installing Aishe" currently means
`curl | bash` into a git checkout.

- `pyproject.toml` with console entry point `aishe`, pinned deps
- `electron-builder` config producing an AppImage (this is an Arch/Omarchy box,
  so AppImage first; .deb/.rpm if there is demand)
- Signed releases via GitHub Actions

### 4 · CI
116 test assertions exist and nothing runs them on push. Both repos will rot.

- `.github/workflows/test.yml` on push + PR for both repos
- `python tests/run.py` for the CLI, `npm test` for the desktop
- `deepagent/test_command_policy.py` needs its venv — run in a step that
  installs `deepagent/requirements.txt`

## PLANNED — correctness

### 5 · Auth-error handling
A provider 401/403 surfaces as a generic failure. The user cannot tell "your key
is wrong" from "the provider is down". Detect 401/403 in
`lib/services.js` and `aishe_pkg/voice.py` and say which credential failed,
pointing at `aishe config set providers.<name>.api_key`.

### 6 · Logging
Zero uses of the `logging` module across all repos — errors go to stdout and
vanish, so a user's problem cannot be diagnosed after the fact. Add a
`~/.local/state/aishe/aishe.log` with rotation; keep stdout output as-is for
interactive use.

### 7 · Retry on transient failure
The desktop service layer has no retry. A dropped connection during a health
probe reads as "service down". Add bounded retry with backoff to
`lib/services.js` fetch paths — not to `run_command`, where retrying a blocked
command is explicitly wrong.

### 8 · Version bump
Still `2.0.0` after weeks of changes. Bump on release, and consider deriving it
from git describe so it cannot drift.

## PLANNED — coverage gaps

Still untested: `aishe_pkg/voice.py`, `aishe_pkg/util.py`,
`aishe_pkg/dashboard.py`, `deepagent/tools.py` (beyond the policy),
`sidecars/telegram_bot.py`. Voice and telegram are the highest-value targets
because they carry live external dependencies.

## WONTFIX

- **Multi-user / auth on the desktop app** — single-user by design; DeepAgent
  binds `127.0.0.1` only.
- **Windows/macOS parity** — Linux-first. `setup.sh` retains platform branches
  but nothing is verified there.
