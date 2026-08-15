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
  if open -a Ollama 2>/dev/null || launchctl start ollama 2>/dev/null; then
    # wait up to 15s
    for _ in $(seq 1 15); do up_ "ollama" 11434 && break; sleep 1; done
  fi
  if up_ "ollama" 11434; then up "Ollama" ":11434"; else down "Ollama" ":11434 — start it manually"; fail=1; fi
fi

# 2. launchd STT / TTS ---------------------------------------------------
for svc in com.opencode.parakeet-stt:5093 com.opencode.supertonic:8766; do
  name="${svc%%:*}"; port="${svc##*:}"
  if up_ "$name" "$port"; then
    up   "${name#com.opencode.}" ":${port}"
  else
    launchctl start "$name" 2>/dev/null
    sleep 1
    if up_ "$name" "$port"; then up "${name#com.opencode.}" ":${port}"; else down "${name#com.opencode.}" ":${port} — launchctl start ${name}"; fail=1; fi
  fi
done

# 3. DeepAgent sidecar ----------------------------------------------------
DA="$HOME/.local/share/aishe-cli/deepagent"
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
  echo "  ${GREEN}✓ All Aishe services up.${RESET}  Try: ${DIM}aishe live${RESET}  or  ${DIM}aishe status${RESET}"
else
  echo "  ${YEL}Some services failed — see statuses above.${RESET}"
fi
echo
exit "$fail"
