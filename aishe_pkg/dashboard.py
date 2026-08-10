"""Aishe Dashboard — local web UI to view and edit Aishe configuration.

Zero-dependency: uses only the Python standard library (http.server), so it
runs on the system python3 with no pip installs. Reads/writes the same
~/.config/aishe/config.yaml that the CLI uses, via aishe_pkg.config.

Usage:
    aishe dashboard            # start on default port (8767)
    aishe dashboard --port 9000
    aishe dashboard --open     # also open the browser

Endpoints:
    GET  /              → the dashboard HTML
    GET  /api/config    → full config as JSON
    POST /api/config    → save a partial config update (JSON body)
    GET  /api/status    → service health summary
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

# ─── Config access (reuse the CLI's config module) ─────────────────────────

def _load_config() -> Dict[str, Any]:
    from .config import load
    return load()


def _save_config(cfg: Dict[str, Any]) -> None:
    from .config import save
    save(cfg)


def _deep_merge(base: Dict, override: Dict) -> None:
    """Recursively merge override into base (in place)."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


# ─── Service health ────────────────────────────────────────────────────────

def _service_status() -> Dict[str, Any]:
    import requests
    cfg = _load_config()
    services = cfg.get("services", {})
    out: Dict[str, Any] = {}
    checks = {
        "ollama": ("/api/tags", "Ollama"),
        "deepagent": ("/health", "DeepAgent"),
        "stt": ("/healthz", "STT"),
        "tts": ("/health", "TTS"),
    }
    for key, (endpoint, label) in checks.items():
        base = services.get(key, "")
        ok = False
        try:
            ok = requests.get(f"{base}{endpoint}", timeout=2).status_code == 200
        except Exception:
            ok = False
        out[key] = {"label": label, "url": base, "up": ok}
    return out


# ─── HTTP handler ──────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AisheDashboard/1.0"

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            self._send_html(_DASHBOARD_HTML)
            return
        if self.path == "/api/config":
            self._send_json(_load_config())
            return
        if self.path == "/api/status":
            self._send_json(_service_status())
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.path != "/api/config":
            self._send_json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            update = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send_json({"error": f"bad request: {e}"}, 400)
            return

        cfg = _load_config()
        _deep_merge(cfg, update)
        try:
            _save_config(cfg)
        except Exception as e:
            self._send_json({"error": f"save failed: {e}"}, 500)
            return
        self._send_json({"ok": True, "config": cfg})

    def log_message(self, fmt: str, *args: Any) -> None:
        # Quiet by default; only log errors.
        if " 4" not in fmt % args and " 2" not in fmt % args:
            sys.stderr.write(f"[dashboard] {fmt % args}\n")


# ─── CLI entry ────────────────────────────────────────────────────────────

def cmd_dashboard(args: Any) -> None:
    port = int(getattr(args, "port", 8767) or 8767)
    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"
    open_browser = bool(getattr(args, "open", False))

    server = ThreadingHTTPServer((host, port), DashboardHandler)
    url = f"http://{host}:{port}"

    print(f"  Aishe Dashboard → {url}")
    print(f"  Config: {os.path.expanduser('~/.config/aishe/config.yaml')}")
    print("  Press Ctrl+C to stop.")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Dashboard stopped.")
        server.server_close()


# ─── Dashboard HTML (embedded) ────────────────────────────────────────────

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aishe Dashboard</title>
<style>
  :root {
    --bg: #0c0c0c;
    --panel: #141412;
    --panel2: #1a1a17;
    --ink: #e8e4dd;
    --muted: #a09a8e;
    --dim: #6b675e;
    --amber: #d4a24e;
    --amber-dim: #8a6a2f;
    --green: #7c9a8c;
    --red: #c05b4d;
    --border: #26261f;
    --mono: "JetBrains Mono", "SF Mono", Menlo, monospace;
    --sans: "Inter", -apple-system, system-ui, sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--ink);
    font-family: var(--sans);
    line-height: 1.5;
    min-height: 100vh;
  }
  /* subtle noise */
  body::before {
    content: "";
    position: fixed; inset: 0;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
    pointer-events: none; z-index: 0;
  }
  .wrap { position: relative; z-index: 1; max-width: 900px; margin: 0 auto; padding: 40px 24px 80px; }

  header { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }
  h1 { font-size: 22px; font-weight: 600; letter-spacing: -0.01em; }
  h1 .dot { color: var(--amber); }
  .sub { color: var(--muted); font-size: 13px; font-family: var(--mono); }
  .path { color: var(--dim); font-size: 12px; font-family: var(--mono); word-break: break-all; }

  .statusbar { display: flex; gap: 8px; flex-wrap: wrap; margin: 20px 0 28px; }
  .svc { display: flex; align-items: center; gap: 8px; padding: 6px 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 12px; font-family: var(--mono); background: var(--panel); }
  .svc .led { width: 8px; height: 8px; border-radius: 50%; }
  .svc .led.up { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .svc .led.down { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .svc .nm { color: var(--muted); }

  .section { margin-bottom: 28px; }
  .section-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.12em; color: var(--amber); font-family: var(--mono); margin-bottom: 12px; }

  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  .card + .card { margin-top: 12px; }
  .card-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; background: var(--panel2); border-bottom: 1px solid var(--border); }
  .card-head .k { font-family: var(--mono); font-size: 13px; color: var(--ink); }
  .card-head .hint { color: var(--dim); font-size: 11px; font-family: var(--mono); }
  .card-body { padding: 14px 16px; }

  .field { display: flex; align-items: center; gap: 12px; padding: 8px 0; }
  .field + .field { border-top: 1px solid var(--border); }
  .field label { flex: 0 0 200px; font-size: 13px; color: var(--muted); font-family: var(--mono); }
  .field input[type="text"], .field input[type="number"], .field select {
    flex: 1; background: var(--bg); border: 1px solid var(--border); color: var(--ink);
    padding: 7px 10px; border-radius: 6px; font-size: 13px; font-family: var(--mono);
  }
  .field input:focus, .field select:focus { outline: none; border-color: var(--amber); }
  .field input[type="checkbox"] { width: 18px; height: 18px; accent-color: var(--amber); }
  .field .val { flex: 1; font-size: 13px; font-family: var(--mono); color: var(--ink); word-break: break-all; }

  .actions { display: flex; align-items: center; gap: 12px; margin-top: 24px; }
  button {
    background: var(--amber); color: #0c0c0c; border: none; padding: 10px 22px;
    border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; font-family: var(--sans);
  }
  button:hover { opacity: 0.9; }
  button.secondary { background: transparent; color: var(--muted); border: 1px solid var(--border); font-weight: 500; }
  button.secondary:hover { color: var(--ink); border-color: var(--dim); }
  .saved { color: var(--green); font-size: 13px; font-family: var(--mono); opacity: 0; transition: opacity 0.3s; }
  .saved.show { opacity: 1; }
  .err { color: var(--red); font-size: 13px; font-family: var(--mono); }

  .raw { width: 100%; min-height: 200px; background: var(--bg); border: 1px solid var(--border); color: var(--ink);
    padding: 12px; border-radius: 8px; font-family: var(--mono); font-size: 12px; resize: vertical; }
  .raw:focus { outline: none; border-color: var(--amber); }

  .footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--dim); font-size: 12px; font-family: var(--mono); display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; }

  @media (max-width: 640px) {
    .field { flex-direction: column; align-items: stretch; gap: 4px; }
    .field label { flex: none; }
    .card-head { flex-direction: column; align-items: flex-start; gap: 4px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span class="dot">●</span> Aishe Dashboard</h1>
    <div class="sub" id="cfgpath">config.yaml</div>
  </header>
  <div class="path" id="cfgpath2"></div>

  <div class="statusbar" id="statusbar"><span class="svc"><span class="led"></span><span class="nm">loading…</span></span></div>

  <div class="section">
    <div class="section-title">Services</div>
    <div class="card" id="card-services"></div>
  </div>

  <div class="section">
    <div class="section-title">Voice</div>
    <div class="card" id="card-voice"></div>
  </div>

  <div class="section">
    <div class="section-title">Data &amp; UI</div>
    <div class="card" id="card-data"></div>
  </div>

  <div class="section">
    <div class="section-title">Telegram</div>
    <div class="card" id="card-telegram"></div>
  </div>

  <div class="section">
    <div class="section-title">Raw YAML</div>
    <div class="card">
      <div class="card-head"><span class="k">config.yaml</span><span class="hint">edit directly</span></div>
      <div class="card-body"><textarea class="raw" id="raw"></textarea></div>
    </div>
  </div>

  <div class="actions">
    <button id="save">Save changes</button>
    <button class="secondary" id="reload">Reload</button>
    <span class="saved" id="saved">✓ saved</span>
    <span class="err" id="err"></span>
  </div>

  <div class="footer">
    <span>aishe dashboard</span>
    <span id="model"></span>
  </div>
</div>

<script>
let cfg = {};

function el(tag, attrs, children) {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  (children || []).forEach(c => {
    if (typeof c === 'string') e.appendChild(document.createTextNode(c));
    else e.appendChild(c);
  });
  return e;
}

function textField(key, label, value, type) {
  const row = el('div', { class: 'field' });
  row.appendChild(el('label', {}, [label]));
  const input = el('input', { type: type || 'text', value: value == null ? '' : value });
  input.dataset.key = key;
  input.addEventListener('input', () => { cfg = setPath(cfg, key, input.value); });
  row.appendChild(input);
  return row;
}

function boolField(key, label, value) {
  const row = el('div', { class: 'field' });
  row.appendChild(el('label', {}, [label]));
  const input = el('input', { type: 'checkbox' });
  input.checked = !!value;
  input.dataset.key = key;
  input.addEventListener('change', () => { cfg = setPath(cfg, key, input.checked); });
  row.appendChild(input);
  return row;
}

function setPath(obj, path, value) {
  const parts = path.split('.');
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) cur[parts[i]] = {};
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = value;
  return obj;
}

function getPath(obj, path) {
  return path.split('.').reduce((o, p) => (o == null ? undefined : o[p]), obj);
}

function card(title, hint, body) {
  const c = el('div', { class: 'card' });
  c.appendChild(el('div', { class: 'card-head' }, [
    el('span', { class: 'k' }, [title]),
    hint ? el('span', { class: 'hint' }, [hint]) : null
  ]));
  c.appendChild(el('div', { class: 'card-body' }, body));
  return c;
}

function render() {
  // Services
  const svc = cfg.services || {};
  const svcBody = [
    textField('services.ollama', 'ollama', svc.ollama),
    textField('services.deepagent', 'deepagent', svc.deepagent),
    textField('services.stt', 'stt', svc.stt),
    textField('services.tts', 'tts', svc.tts),
  ];
  document.getElementById('card-services').replaceWith(card('services', 'endpoint URLs', svcBody));

  // Voice
  const v = cfg.voice || {};
  const voiceBody = [
    textField('voice.default_voice', 'default_voice', v.default_voice),
    textField('voice.recording_duration', 'recording_duration', v.recording_duration, 'number'),
    boolField('voice.vad_enabled', 'vad_enabled', v.vad_enabled),
    textField('voice.vad_threshold', 'vad_threshold', v.vad_threshold, 'number'),
    textField('voice.vad_min_speech_duration_ms', 'vad_min_speech_duration_ms', v.vad_min_speech_duration_ms, 'number'),
    textField('voice.vad_min_silence_duration_ms', 'vad_min_silence_duration_ms', v.vad_min_silence_duration_ms, 'number'),
  ];
  document.getElementById('card-voice').replaceWith(card('voice', 'TTS + VAD', voiceBody));

  // Data & UI
  const d = cfg.data || {};
  const ui = cfg.ui || {};
  const dataBody = [
    textField('data.dir', 'data.dir', d.dir),
    boolField('ui.color', 'ui.color', ui.color),
  ];
  document.getElementById('card-data').replaceWith(card('data / ui', '', dataBody));

  // Telegram
  const t = cfg.telegram || {};
  const tgBody = [
    textField('telegram.token', 'token', t.token),
    textField('telegram.allowed_users', 'allowed_users', Array.isArray(t.allowed_users) ? t.allowed_users.join(',') : t.allowed_users),
    boolField('telegram.allow_all', 'allow_all', t.allow_all),
    textField('telegram.log_file', 'log_file', t.log_file),
  ];
  document.getElementById('card-telegram').replaceWith(card('telegram', 'bridge', tgBody));

  // Raw
  document.getElementById('raw').value = JSON.stringify(cfg, null, 2);
  document.getElementById('model').textContent = 'model: ' + (cfg.model || '');
}

async function loadStatus() {
  try {
    const r = await fetch('/api/status');
    const st = await r.json();
    const bar = document.getElementById('statusbar');
    bar.innerHTML = '';
    for (const [k, s] of Object.entries(st)) {
      bar.appendChild(el('span', { class: 'svc' }, [
        el('span', { class: 'led ' + (s.up ? 'up' : 'down') }),
        el('span', { class: 'nm' }, [s.label])
      ]));
    }
  } catch (e) { /* status is best-effort */ }
}

async function load() {
  const r = await fetch('/api/config');
  cfg = await r.json();
  document.getElementById('cfgpath').textContent = '~/.config/aishe/config.yaml';
  render();
  loadStatus();
}

document.getElementById('save').addEventListener('click', async () => {
  const err = document.getElementById('err');
  err.textContent = '';
  try {
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg)
    });
    const res = await r.json();
    if (res.error) { err.textContent = res.error; return; }
    cfg = res.config;
    render();
    const s = document.getElementById('saved');
    s.classList.add('show');
    setTimeout(() => s.classList.remove('show'), 2000);
  } catch (e) {
    err.textContent = 'save failed: ' + e;
  }
});

document.getElementById('reload').addEventListener('click', load);

load();
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(prog="aishe dashboard", description="Aishe config dashboard")
    parser.add_argument("--port", type=int, default=8767, help="Port (default 8767)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default 127.0.0.1)")
    parser.add_argument("--open", action="store_true", help="Open browser")
    args = parser.parse_args()
    cmd_dashboard(args)


if __name__ == "__main__":
    main()
