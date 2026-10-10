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

  .price { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 13px; }
  .price th, .price td { padding: 7px 10px; border-bottom: 1px solid var(--border); text-align: left; }
  .price thead th { color: var(--muted); font-weight: 600; background: var(--panel2); position: sticky; top: 0; }
  .price td.num { text-align: right; color: var(--ink); font-variant-numeric: tabular-nums; }
  .price tbody tr:hover { background: color-mix(in srgb, var(--panel) 90%, var(--border)); }
  .price td:first-child { font-weight: 600; }
  .price tbody.grp-head th { background: var(--amber); color: #111; font-weight: 700; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; }
  .price tbody.grp-tier tr { background: var(--panel2); }
  .price tbody.grp-tier td { color: var(--muted); font-weight: 700; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
  .badge-tier { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; line-height: 1.6; }
  .badge-tier.high { background: #7f1d1d; color: #fecaca; }
  .badge-tier.mid  { background: #78350f; color: #fde68a; }
  .badge-tier.low  { background: #14532d; color: #bbf7d0; }

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
    <div class="section-title">Pricing</div>
    <div class="card">
      <div class="card-head"><span class="k">model pricing catalog</span><span class="hint">USD + ₹ INR, indicative</span></div>
      <div class="card-body">
        <p class="muted" style="margin-bottom:10px" id="price-fx"></p>
        <table class="price" id="price-table"></table>
        <p class="muted" style="margin-top:12px" id="price-note"></p>
      </div>
    </div>
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
  loadPricing();
}

/* ── Pricing catalog (read-only) ────────────────────────────────── */
const PRICE_FX = 83; // ₹ per USD, indicative
// Live OpenRouter list prices (USD / 1M tokens), verified 2026-09. Every model
// supports tools (agentic). Grouped by company; tier = High/Mid/Low by OUTPUT
// price per 1M tokens. c = cached input read (USD), '-' when not offered.
const PRICE_CATALOG = [
  { company: "Anthropic", models: [
    { m: "Claude Sonnet 5", c: 0.200, inp: 2.000, out: 10.000, tier: "High" },
    { m: "Claude Sonnet 4.6", c: 0.300, inp: 3.000, out: 15.000, tier: "High" },
    { m: "Claude Sonnet 4.5", c: 0.300, inp: 3.000, out: 15.000, tier: "High" },
    { m: "Claude Sonnet 4", c: 0.300, inp: 3.000, out: 15.000, tier: "High" },
    { m: "Claude Opus 5", c: 0.500, inp: 5.000, out: 25.000, tier: "High" },
    { m: "Claude Haiku 4.5", c: 0.100, inp: 1.000, out: 5.000, tier: "Mid" },
    { m: "Claude 3 Haiku", c: 0.030, inp: 0.250, out: 1.250, tier: "Low" },
  ] },
  { company: "OpenAI", models: [
    { m: "o3", c: 0.500, inp: 2.000, out: 8.000, tier: "High" },
    { m: "GPT-4.1", c: 0.500, inp: 2.000, out: 8.000, tier: "High" },
    { m: "GPT-5.6 Sol Pro", c: 0.200, inp: 2.000, out: 10.000, tier: "High" },
    { m: "GPT-5.6 Sol", c: 0.200, inp: 2.000, out: 10.000, tier: "High" },
    { m: "GPT-5.1-Codex-Max", c: 0.125, inp: 1.250, out: 10.000, tier: "High" },
    { m: "GPT-5.1", c: 0.125, inp: 1.250, out: 10.000, tier: "High" },
    { m: "GPT-5.1-Codex", c: 0.130, inp: 1.250, out: 10.000, tier: "High" },
    { m: "GPT-5", c: 0.125, inp: 1.250, out: 10.000, tier: "High" },
    { m: "GPT-4o", c: 1.250, inp: 2.500, out: 10.000, tier: "High" },
    { m: "GPT-5.6 Terra Pro", c: 0.200, inp: 2.000, out: 12.000, tier: "High" },
    { m: "GPT-5.6 Terra", c: 0.200, inp: 2.000, out: 12.000, tier: "High" },
    { m: "GPT-5.3-Codex", c: 0.175, inp: 1.750, out: 14.000, tier: "High" },
    { m: "GPT-5.2-Codex", c: 0.175, inp: 1.750, out: 14.000, tier: "High" },
    { m: "GPT-5.2 Chat", c: 0.175, inp: 1.750, out: 14.000, tier: "High" },
    { m: "GPT-5.2", c: 0.175, inp: 1.750, out: 14.000, tier: "High" },
    { m: "GPT-5.4", c: 0.250, inp: 2.500, out: 15.000, tier: "High" },
    { m: "GPT Chat Latest", c: 0.500, inp: 5.000, out: 30.000, tier: "High" },
    { m: "GPT-5.5", c: 0.500, inp: 5.000, out: 30.000, tier: "High" },
    { m: "GPT-6 Astra", c: 1.000, inp: 10.000, out: 50.000, tier: "High" },
    { m: "GPT-6 Astra Pro", c: 1.000, inp: 10.000, out: 50.000, tier: "High" },
    { m: "GPT-4", c: null, inp: 30.000, out: 60.000, tier: "High" },
    { m: "GPT-5 Pro", c: null, inp: 15.000, out: 120.000, tier: "High" },
    { m: "GPT-5.2 Pro", c: null, inp: 21.000, out: 168.000, tier: "High" },
    { m: "GPT-5.5 Pro", c: null, inp: 30.000, out: 180.000, tier: "High" },
    { m: "GPT-5.4 Pro", c: null, inp: 30.000, out: 180.000, tier: "High" },
    { m: "o4 Mini High", c: 0.275, inp: 1.100, out: 4.400, tier: "Mid" },
    { m: "o4 Mini", c: 0.275, inp: 1.100, out: 4.400, tier: "Mid" },
    { m: "o3 Mini High", c: 0.550, inp: 1.100, out: 4.400, tier: "Mid" },
    { m: "o3 Mini", c: 0.550, inp: 1.100, out: 4.400, tier: "Mid" },
    { m: "GPT-5.4 Mini", c: 0.075, inp: 0.750, out: 4.500, tier: "Mid" },
    { m: "gpt-oss-20b", c: 0.030, inp: 0.030, out: 0.130, tier: "Low" },
    { m: "gpt-oss-120b", c: null, inp: 0.037, out: 0.170, tier: "Low" },
    { m: "GPT-5 Nano", c: 0.005, inp: 0.050, out: 0.400, tier: "Low" },
    { m: "GPT-4.1 Nano", c: 0.025, inp: 0.100, out: 0.400, tier: "Low" },
    { m: "GPT-4o-mini", c: 0.075, inp: 0.150, out: 0.600, tier: "Low" },
    { m: "GPT-5.6 Luna Pro", c: 0.020, inp: 0.200, out: 1.200, tier: "Low" },
    { m: "GPT-5.6 Luna", c: 0.020, inp: 0.200, out: 1.200, tier: "Low" },
    { m: "GPT-5.4 Nano", c: 0.020, inp: 0.200, out: 1.250, tier: "Low" },
    { m: "GPT-4.1 Mini", c: 0.100, inp: 0.400, out: 1.600, tier: "Low" },
    { m: "GPT-5.1-Codex-Mini", c: 0.030, inp: 0.250, out: 2.000, tier: "Low" },
    { m: "GPT-5 Mini", c: 0.025, inp: 0.250, out: 2.000, tier: "Low" },
  ] },
  { company: "Google", models: [
    { m: "Gemini 3.5 Flash", c: 0.150, inp: 1.500, out: 9.000, tier: "High" },
    { m: "Gemini 2.5 Pro", c: 0.125, inp: 1.250, out: 10.000, tier: "High" },
    { m: "Gemini 3.1 Pro Preview Custom Tools", c: 0.200, inp: 2.000, out: 12.000, tier: "High" },
    { m: "Gemini 2.5 Flash", c: 0.030, inp: 0.300, out: 2.500, tier: "Mid" },
    { m: "Gemini 3 Flash Preview", c: 0.050, inp: 0.500, out: 3.000, tier: "Mid" },
    { m: "Gemini 3.8 Flash", c: 0.075, inp: 0.750, out: 3.750, tier: "Mid" },
    { m: "Gemini 3.7 Flash", c: 0.075, inp: 0.750, out: 3.750, tier: "Mid" },
    { m: "Gemini 3.6 Flash", c: 0.075, inp: 0.750, out: 3.750, tier: "Mid" },
    { m: "Gemma 3 12B", c: null, inp: 0.050, out: 0.150, tier: "Low" },
    { m: "Gemma 4 26B A4B", c: 0.050, inp: 0.090, out: 0.300, tier: "Low" },
    { m: "Gemma 4 31B", c: 0.050, inp: 0.090, out: 0.340, tier: "Low" },
    { m: "Gemma 3 27B", c: 0.040, inp: 0.080, out: 0.450, tier: "Low" },
    { m: "Gemini 3.1 Flash Lite", c: 0.025, inp: 0.250, out: 1.500, tier: "Low" },
  ] },
  { company: "xAI", models: [
    { m: "Grok 4.3", c: 0.200, inp: 1.250, out: 2.500, tier: "Mid" },
    { m: "Grok 4.20", c: 0.200, inp: 1.250, out: 2.500, tier: "Mid" },
    { m: "Grok 4.6", c: 0.500, inp: 2.000, out: 6.000, tier: "Mid" },
    { m: "Grok 4.5", c: 0.300, inp: 2.000, out: 6.000, tier: "Mid" },
  ] },
  { company: "DeepSeek", models: [
    { m: "DeepSeek V4 Pro 0813", c: 0.033, inp: 0.983, out: 2.950, tier: "Mid" },
    { m: "DeepSeek V4 Pro 0423", c: 0.135, inp: 1.600, out: 3.200, tier: "Mid" },
    { m: "DeepSeek V4 Flash Vision Exp", c: 0.007, inp: 0.220, out: 0.660, tier: "Low" },
    { m: "DeepSeek V3", c: null, inp: 0.257, out: 1.029, tier: "Low" },
    { m: "DeepSeek V4.1 Flash", c: 0.006, inp: 0.300, out: 1.200, tier: "Low" },
  ] },
  { company: "Alibaba", models: [
    { m: "Qwen3 Coder Plus", c: 0.130, inp: 0.650, out: 3.250, tier: "Mid" },
    { m: "Qwen3.5 397B A17B", c: 0.225, inp: 0.550, out: 3.500, tier: "Mid" },
    { m: "Qwen3 Max Thinking", c: null, inp: 0.780, out: 3.900, tier: "Mid" },
    { m: "Qwen3 VL 235B A22B Thinking", c: null, inp: 0.400, out: 4.000, tier: "Mid" },
    { m: "Qwen3.7 Max", c: 0.295, inp: 1.475, out: 4.425, tier: "Mid" },
    { m: "Qwen3.8 Max (0902)", c: 0.250, inp: 2.000, out: 6.000, tier: "Mid" },
    { m: "Qwen3.8 2.4T A95B", c: 0.250, inp: 2.000, out: 6.000, tier: "Mid" },
    { m: "Qwen3.6 Max Preview", c: null, inp: 1.027, out: 6.162, tier: "Mid" },
    { m: "Qwen3.7 Flash", c: 0.006, inp: 0.030, out: 0.130, tier: "Low" },
    { m: "Qwen2.5 7B Instruct", c: null, inp: 0.100, out: 0.200, tier: "Low" },
    { m: "Qwen2.5 72B Instruct", c: null, inp: 0.360, out: 0.400, tier: "Low" },
    { m: "Qwen Plus 0728", c: null, inp: 0.260, out: 0.780, tier: "Low" },
    { m: "Qwen-Plus", c: 0.052, inp: 0.260, out: 0.780, tier: "Low" },
    { m: "Qwen3 Coder Next", c: 0.070, inp: 0.120, out: 0.800, tier: "Low" },
    { m: "Qwen3 Coder 480B A35B", c: 0.100, inp: 0.300, out: 1.000, tier: "Low" },
    { m: "Qwen3 Next 80B A3B Thinking", c: null, inp: 0.150, out: 1.200, tier: "Low" },
    { m: "Qwen3.7 Plus", c: 0.064, inp: 0.320, out: 1.280, tier: "Low" },
    { m: "Qwen3.5 Plus 2026-02-15", c: null, inp: 0.260, out: 1.560, tier: "Low" },
    { m: "Qwen3.5 Plus 2026-04-20", c: null, inp: 0.300, out: 1.800, tier: "Low" },
    { m: "Qwen3 235B A22B", c: null, inp: 0.455, out: 1.820, tier: "Low" },
    { m: "Qwen3.6 Plus", c: null, inp: 0.325, out: 1.950, tier: "Low" },
    { m: "Qwen3.6 27B", c: 0.030, inp: 0.300, out: 2.000, tier: "Low" },
    { m: "Qwen3.5-122B-A10B", c: null, inp: 0.260, out: 2.080, tier: "Low" },
  ] },
  { company: "Meta", models: [
    { m: "Llama 4 Scout", c: null, inp: 0.100, out: 0.300, tier: "Low" },
    { m: "Llama 4 Maverick", c: null, inp: 0.188, out: 0.652, tier: "Low" },
  ] },
  { company: "NVIDIA", models: [
    { m: "Nemotron 3.5 Lightning", c: 0.040, inp: 0.080, out: 0.200, tier: "Low" },
    { m: "Nemotron 3 Nano 30B A3B", c: 0.030, inp: 0.050, out: 0.200, tier: "Low" },
    { m: "Nemotron 3 Super", c: null, inp: 0.080, out: 0.450, tier: "Low" },
    { m: "Nemotron 3 Ultra", c: 0.120, inp: 0.600, out: 2.400, tier: "Low" },
  ] },
  { company: "Mistral", models: [
    { m: "Mistral Large", c: 0.200, inp: 2.000, out: 6.000, tier: "Mid" },
    { m: "Mistral Medium 3.5", c: null, inp: 1.500, out: 7.500, tier: "Mid" },
    { m: "Ministral 3 3B 2512", c: 0.010, inp: 0.100, out: 0.100, tier: "Low" },
    { m: "Ministral 3 14B 2512", c: 0.020, inp: 0.200, out: 0.200, tier: "Low" },
    { m: "Mistral Small 3.2 24B", c: null, inp: 0.075, out: 0.200, tier: "Low" },
    { m: "Mistral Small 4", c: 0.015, inp: 0.150, out: 0.600, tier: "Low" },
    { m: "Codestral 2508", c: 0.030, inp: 0.300, out: 0.900, tier: "Low" },
    { m: "Mistral Large 3 2512", c: 0.050, inp: 0.500, out: 1.500, tier: "Low" },
    { m: "Devstral 2 2512", c: 0.040, inp: 0.400, out: 2.000, tier: "Low" },
    { m: "Mistral Medium 3.1", c: 0.040, inp: 0.400, out: 2.000, tier: "Low" },
    { m: "Mistral Medium 3", c: 0.040, inp: 0.400, out: 2.000, tier: "Low" },
  ] },
  { company: "Moonshot AI", models: [
    { m: "Kimi K3", c: 0.303, inp: 2.648, out: 13.283, tier: "High" },
    { m: "Kimi K2 Thinking", c: 0.150, inp: 0.600, out: 2.500, tier: "Mid" },
    { m: "Kimi K2 0905", c: null, inp: 0.600, out: 2.500, tier: "Mid" },
    { m: "Kimi K2.7 Code", c: 0.150, inp: 0.710, out: 3.500, tier: "Mid" },
    { m: "Kimi K2.6", c: 0.160, inp: 0.950, out: 4.000, tier: "Mid" },
    { m: "Kimi K2.5", c: 0.070, inp: 0.450, out: 2.250, tier: "Low" },
    { m: "Kimi K2 0711", c: null, inp: 0.570, out: 2.300, tier: "Low" },
  ] },
  { company: "Zhipu", models: [
    { m: "GLM 5.1", c: 0.179, inp: 0.966, out: 3.036, tier: "Mid" },
    { m: "GLM 5V Turbo", c: 0.240, inp: 1.200, out: 4.000, tier: "Mid" },
    { m: "GLM 5 Turbo", c: 0.240, inp: 1.200, out: 4.000, tier: "Mid" },
    { m: "GLM 5.3", c: 0.260, inp: 1.400, out: 4.400, tier: "Mid" },
    { m: "GLM 5.2", c: 0.140, inp: 1.400, out: 4.400, tier: "Mid" },
    { m: "GLM 4.7", c: 0.080, inp: 0.400, out: 1.750, tier: "Low" },
    { m: "GLM 4.6", c: 0.080, inp: 0.430, out: 1.750, tier: "Low" },
    { m: "GLM 5", c: 0.120, inp: 0.600, out: 1.920, tier: "Low" },
    { m: "GLM 4.5", c: 0.110, inp: 0.600, out: 2.200, tier: "Low" },
  ] },
  { company: "Amazon", models: [
    { m: "Nova 2 Lite", c: null, inp: 0.300, out: 2.500, tier: "Mid" },
    { m: "Nova Pro 1.0", c: null, inp: 0.800, out: 3.200, tier: "Mid" },
    { m: "Nova Micro 1.0", c: null, inp: 0.035, out: 0.140, tier: "Low" },
    { m: "Nova Lite 1.0", c: null, inp: 0.060, out: 0.240, tier: "Low" },
  ] },
  { company: "Upstage", models: [
    { m: "Solar Pro 4", c: 0.018, inp: 0.090, out: 0.360, tier: "Low" },
    { m: "Solar Pro 3", c: 0.015, inp: 0.150, out: 0.600, tier: "Low" },
  ] },
];;

const TIER_ORDER = ['High', 'Mid', 'Low'];

function fmt(c) {
  return c === null || c === undefined ? '—' : '₹' + Math.round(c * PRICE_FX);
}

function loadPricing() {
  const fx = document.getElementById('price-fx');
  if (fx) fx.textContent = 'Per‑1M‑token list prices (output tier) · 1 USD ≈ ₹' + PRICE_FX;
  const wrap = document.getElementById('price-table');
  if (!wrap) return;
  // Rebuild whole table: header + company-grouped tbody(s)
  let html = '<thead><tr><th>Model</th><th class="num">Cached input</th><th class="num">Input ₹/M</th><th class="num">Output ₹/M</th><th>Tier</th></tr></thead>';
  for (const grp of PRICE_CATALOG) {
    html += '<tbody class="grp-head"><tr><th colspan="5">' + grp.company + '</th></tr></tbody>';
    for (const t of TIER_ORDER) {
      const rows = grp.models.filter(r => r.tier === t);
      if (!rows.length) continue;
      html += '<tbody class="grp-tier"><tr class="tier-'+t.toLowerCase()+'"><td colspan="5">' + t + '</td></tr></tbody>';
      html += '<tbody>';
      for (const r of rows) {
        html += '<tr>'
          + '<td>' + r.m + '</td>'
          + '<td class="num">' + fmt(r.c) + '</td>'
          + '<td class="num">' + fmt(r.inp) + '</td>'
          + '<td class="num">' + fmt(r.out) + '</td>'
          + '<td><span class="badge-tier ' + t.toLowerCase() + '">' + t + '</span></td>'
          + '</tr>';
      }
      html += '</tbody>';
    }
  }
  wrap.innerHTML = html;
  const note = document.getElementById('price-note');
  if (note) note.textContent = 'List price per 1M tokens (≈33,000–66,000 short messages). Actual cost depends on token count, caching and the exact model id the provider bills against. Prices are indicative — verify against the provider before trusting a number here.';
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
