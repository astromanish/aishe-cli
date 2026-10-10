#!/bin/bash
# aishe start-all — bring up every Aishe service in one go (idempotent).
# Stops at first failure, reports per-service status. Safe to re-run.
set -u

GREEN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
ok()  { echo "  ${GREEN}●${RESET} $1"; }
up()  { echo "  ${GREEN}●${RESET} $1  ${DIM}${2}${RESET}"; }
down(){ echo "  ${RED}○${RESET} $1  ${DIM}${2}${RESET}"; }
warn(){ echo "  ${YEL}!${RESET} $1"; }

up_() { # up_ NAME PORT -> 0 if a listener is on PORT
  lsof -nP -iTCP:"$2" -sTCP:LISTEN >/dev/null 2>&1
}

# Resolve a path through aishe_core, the single shared resolver every surface
# uses. Falls back silently to the installed location if the Python resolver is
# unavailable (e.g. no interpreter on PATH), so this script never hard-fails.
aishe_core_deepagent_dir() {
  local py repo
  py="$(command -v python3 || command -v python)" || return 1
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  "$py" -c "
import sys
sys.path.insert(0, '$repo')
try:
    from aishe_pkg import aishe_core
    print(aishe_core.deepagent_dir())
except Exception:
    sys.exit(1)
" 2>/dev/null || return 1
}

# The install root, from the same resolver.
aishe_core_home() {
  local py repo
  py="$(command -v python3 || command -v python)" || { echo "$HOME/.local/share/aishe-cli"; return; }
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  "$py" -c "
import sys
sys.path.insert(0, '$repo')
try:
    from aishe_pkg import aishe_core
    print(aishe_core.home())
except Exception:
    sys.exit(1)
" 2>/dev/null || echo "$HOME/.local/share/aishe-cli"
}

echo
echo "  ${GREEN}╭──────────────────────────────────────╮${RESET}"
echo "  ${GREEN}│      Aishe — starting all services   │${RESET}"
echo "  ${GREEN}╰──────────────────────────────────────╯${RESET}"
echo

fail=0

# 1. Ollama -------------------------------------------------------------
if up_ "ollama" 11434; then
  up   "Ollama" ":11434 (already running)"
else
  echo -n "  Starting Ollama... "
  # `open` is macOS-only and a no-op failure on Linux, so try it first and fall
  # back to `ollama serve` — the macOS branch used to `launchctl start` here,
  # which never does anything on a Linux box.
  if open -a Ollama 2>/dev/null || nohup ollama serve >/tmp/ollama.log 2>&1 & then
    # wait up to 15s
    for _ in $(seq 1 15); do up_ "ollama" 11434 && break; sleep 1; done
  fi
  if up_ "ollama" 11434; then up "Ollama" ":11434"; else down "Ollama" ":11434 — start it manually"; fail=1; fi
fi

# 2. Qdrant (vector DB for memory) ----------------------------------------
# Qdrant was managed only by the desktop app before, so starting Aishe from the
# CLI left memory silently degraded to the JSONL fallback. It is a first-class
# service now, driven by the same registry both surfaces read.
QD="$(aishe_core_home)/qdrant"
QDRANT_BIN="$HOME/.local/bin/qdrant"
if up_ "Qdrant" 6333; then
  up "Qdrant" ":6333 (already running)"
elif [ -x "$QDRANT_BIN" ]; then
  echo -n "  Starting Qdrant... "
  mkdir -p "$QD"
  (cd "$QD" && "$QDRANT_BIN" >/tmp/qdrant.log 2>&1 &)
  for _ in $(seq 1 15); do up_ "Qdrant" 6333 && break; sleep 1; done
  if up_ "Qdrant" 6333; then up "Qdrant" ":6333"; else down "Qdrant" ":6333 — check /tmp/qdrant.log"; fail=1; fi
else
  warn "Qdrant not installed (expected at $QDRANT_BIN) — memory will use the JSONL fallback"
fi

# 3. DeepAgent sidecar ----------------------------------------------------
# Resolve the backend through the shared resolver rather than a hardcoded path.
# start-all.sh used to point only at ~/.local/share/aishe-cli/deepagent, so on a
# repo checkout it launched a *different* copy of deepagent than the desktop app
# did — two divergent backends racing for :8765, with the winner decided by
# whichever tool started first.
DA="$(aishe_core_deepagent_dir 2>/dev/null || echo "$HOME/.local/share/aishe-cli/deepagent")"
if up_ "DeepAgent" 8765; then
  up   "DeepAgent" ":8765 (already running)"
else
  echo -n "  Starting DeepAgent sidecar... "
  if [ -x "$DA/.venv/bin/python" ] && [ -f "$DA/server.py" ]; then
    # match the setup command exactly (model from config default)
    AISHE_MODEL="${AISHE_MODEL:-deepseek-v4-flash:cloud}" \
    AISHE_OLLAMA_URL="${AISHE_OLLAMA_URL:-http://localhost:11434/v1}" \
    AISHE_API_KEY="${AISHE_API_KEY:-ollama}" \
      "$DA/.venv/bin/python" "$DA/server.py" \
        >>/tmp/deepagent.log 2>&1 &
    for _ in $(seq 1 20); do up_ "DeepAgent" 8765 && break; sleep 1; done
    if up_ "DeepAgent" 8765; then up "DeepAgent" ":8765"; else down "DeepAgent" ":8765 — check /tmp/deepagent.log"; fail=1; fi
  else
    down "DeepAgent" "sidecar missing — run: aishe setup"; fail=1
  fi
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "  ${GREEN}✓ All Aishe services up.${RESET}  Try: ${DIM}aishe status${RESET}  or  ${DIM}aishe desktop${RESET}"
else
  echo "  ${YEL}Some services failed — see statuses above.${RESET}"
fi
echo
exit "$fail"
