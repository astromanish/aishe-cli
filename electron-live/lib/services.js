"use strict";
/**
 * Aishe Live — service proxy (pure Node, no Electron imports).
 *
 * The Electron main process uses these to talk directly to the same
 * services the CLI uses — DeepAgent (:8765), Parakeet STT (:5093),
 * Supertonic TTS (:8766), Ollama (:11434). No localhost web server.
 *
 * Kept dependency-free and testable with plain `node` (Node 18+ has
 * global fetch / FormData / Blob).
 */

const os = require("os");
const path = require("path");
const fs = require("fs");
const AgentStatus = require("./agent-status");

const DEFAULTS = {
  model: "",
  services: {
    deepagent: "http://localhost:8765",
    ollama: "http://localhost:11434",
    stt: "http://localhost:5093",
    tts: "http://localhost:8766",
  },
  voice: { default_voice: "F4" },
  live: { silence_ms: 500, min_speech_ms: 250, rms_threshold: 0.018 },
};

function configPath() {
  return path.join(os.homedir(), ".config", "aishe", "config.yaml");
}

function parseScalar(s) {
  if (s === "true") return true;
  if (s === "false") return false;
  if (/^-?\d+$/.test(s)) return parseInt(s, 10);
  if (/^-?\d*\.\d+$/.test(s)) return parseFloat(s);
  return s;
}

/** Minimal YAML read for the flat-ish config the app needs. Falls back to defaults. */
function loadConfig() {
  const cfg = JSON.parse(JSON.stringify(DEFAULTS));
  try {
    const raw = fs.readFileSync(configPath(), "utf8");
    let top = null;
    for (const line of raw.split("\n")) {
      const m = line.match(/^(\s*)([\w.-]+):\s*(.*)$/);
      if (!m) continue;
      const [, indent, key, rest] = m;
      const val = rest.trim();
      if (indent.length > 0) {
        if (top && cfg[top] && typeof cfg[top] === "object" && val && !val.startsWith("#")) {
          const v = parseScalar(val);
          if (cfg[top][key] !== undefined) cfg[top][key] = v;
        }
      } else {
        top = key;
        if (!cfg[key] || typeof cfg[key] !== "object") {
          if (val && !val.startsWith("#")) cfg[key] = parseScalar(val);
        }
      }
    }
  } catch (e) {
    /* missing config → defaults */
  }
  return cfg;
}

async function _fetchTimeout(url, opts, ms, externalSignal) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  const onAbort = () => ctrl.abort();
  if (externalSignal) {
    if (externalSignal.aborted) ctrl.abort();
    else externalSignal.addEventListener("abort", onAbort, { once: true });
  }
  try {
    return await fetch(url, { ...opts, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
    if (externalSignal) externalSignal.removeEventListener("abort", onAbort);
  }
}

/** Service health + app config (the "alive" showcase data). */
async function health() {
  const cfg = loadConfig();
  const checks = [
    ["ollama", "/api/tags", "Ollama"],
    ["deepagent", "/health", "DeepAgent"],
    ["stt", "/healthz", "STT"],
    ["tts", "/health", "TTS"],
  ];
  const out = {};
  await Promise.all(
    checks.map(async ([key, ep, label]) => {
      const base = (cfg.services && cfg.services[key]) || DEFAULTS.services[key];
      let up = false;
      try {
        const r = await _fetchTimeout(base + ep, {}, 2000);
        up = r.ok;
      } catch (e) {
        up = false;
      }
      out[key] = { up, url: base, label };
    })
  );
  return {
    services: out,
    model: cfg.model || "",
    voice: (cfg.voice && cfg.voice.default_voice) || "F4",
    live: cfg.live,
  };
}

/** Transcribe a 16 kHz mono WAV (base64) via Parakeet. Returns text. */
async function transcribe(wavBase64) {
  const cfg = loadConfig();
  const base = (cfg.services && cfg.services.stt) || DEFAULTS.services.stt;
  const buf = Buffer.from(wavBase64, "base64");
  const fd = new FormData();
  fd.append("model", "parakeet-tdt-0.6b-v3");
  fd.append("file", new Blob([buf], { type: "audio/wav" }), "speech.wav");
  const r = await _fetchTimeout(base + "/v1/audio/transcriptions", { method: "POST", body: fd }, 30000);
  if (!r.ok) throw new Error("STT HTTP " + r.status);
  const j = await r.json();
  return (j.text || "").trim();
}

/** Split completed spoken sentences without waiting for the full answer. */
function pullSentence(buffer, flush = false) {
  const match = buffer.match(/^([\s\S]*?[.!?…])(?:\s+|$)/);
  if (match) return { sentence: match[1].trim(), rest: buffer.slice(match[0].length) };
  if (flush && buffer.trim()) return { sentence: buffer.trim(), rest: "" };
  return null;
}

async function synthesizeAudio(text, cfg, signal) {
  const tts = (cfg.services && cfg.services.tts) || DEFAULTS.services.tts;
  const voice = (cfg.voice && cfg.voice.default_voice) || "F4";
  const tr = await _fetchTimeout(
    tts + "/v1/audio/speech",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input: text, voice, model: "supertonic-3", response_format: "wav" }),
    },
    60000,
    signal
  );
  if (!tr.ok) return "";
  const ab = await tr.arrayBuffer();
  return Buffer.from(ab).toString("base64");
}

/**
 * Full turn: stream DeepAgent NDJSON, emit tool activity immediately, and
 * synthesize completed sentence chunks while later tokens are still arriving.
 */
let activeChatController = null;

async function chat(text, onEvent, externalSignal) {
  if (externalSignal && externalSignal.aborted) {
    const error = new Error("chat cancelled");
    error.name = "AbortError";
    throw error;
  }
  const controller = externalSignal ? null : new AbortController();
  const signal = externalSignal || controller.signal;
  activeChatController = controller;
  const cfg = loadConfig();
  const da = (cfg.services && cfg.services.deepagent) || DEFAULTS.services.deepagent;
  const status = new AgentStatus.AgentStatusStream(text);
  status.model = cfg.model || "local agent";
  try {
    if (onEvent) onEvent({ kind: "start", model: status.model, message: text });
    const r = await _fetchTimeout(
      da + "/stream",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, thread_id: "live" }),
      },
      120000,
      signal
    );
    if (!r.ok) throw new Error("DeepAgent HTTP " + r.status);

    let answer = "";
    let spokenBuffer = "";
    let audioBase64 = "";
    let sawToken = false;
    let ttsQueue = Promise.resolve();
    const toolCalls = [];
    const pendingTools = [];
    const queueSentence = (sentence) => {
      if (!sentence) return;
      ttsQueue = ttsQueue.then(async () => {
        try {
          const audio = await synthesizeAudio(sentence, cfg, signal);
          if (audio) {
            if (!audioBase64) audioBase64 = audio;
            if (onEvent) onEvent({ kind: "audio_chunk", audioBase64: audio, text: sentence });
          }
        } catch (error) {
          if (error.name === "AbortError") throw error;
          // TTS is best effort; text remains available.
        }
      });
    };
    const consume = (ev) => {
      // Feed through the status stream first — it decides what kind of
      // activity this is and accumulates per-tool timing/bytes.
      const merged = status.feed(ev);
      // The stream's `final` is not a UI "done" — the real done is emitted
      // after TTS finishes below. Only surface non-done activity live.
      if (onEvent && merged && merged.kind !== "done") {
        // Normalize to the typed status contract the renderer renders.
        onEvent({
          kind: merged.kind,
          stage: merged.stage,
          stageLabel: merged.label,
          name: merged.name,
          label: merged.label,
          icon: merged.icon,
          content: merged.content,
          result: merged.result,
          ms: merged.ms,
          now: merged.now,
          tools: status.tools.length,
          summary: merged.kind === "done" ? status.summary() : undefined,
        });
      }
      if (ev.event === "token") {
        const content = ev.content || "";
        sawToken = true;
        answer += content;
        spokenBuffer += content;
        let part;
        while ((part = pullSentence(spokenBuffer))) {
          spokenBuffer = part.rest;
          queueSentence(part.sentence);
        }
      } else if (ev.event === "tool_call") {
        if (!ev.name) return;
        const entry = {
          name: ev.name,
          args: typeof ev.args === "string" ? ev.args : JSON.stringify(ev.args || {}),
          result: "",
        };
        if (entry.args.length > 300) entry.args = entry.args.slice(0, 300) + "…";
        toolCalls.push(entry);
        pendingTools.push(entry);
      } else if (ev.event === "tool_result") {
        const target = pendingTools.shift();
        if (target) {
          target.result = String(ev.result || "").replace(/\s+/g, " ").trim();
          if (target.result.length > 500) target.result = target.result.slice(0, 500) + "…";
        }
      } else if (ev.event === "error") {
        const error = new Error(ev.message || "stream error");
        error.name = "StreamError";
        throw error;
      } else if (ev.event === "final" && !answer) {
        answer = ev.answer || "";
      }
    };

    let pending = "";
    const decoder = new TextDecoder();
    if (!r.body) throw new Error("DeepAgent returned no stream body");
    for await (const chunk of r.body) {
      pending += decoder.decode(chunk, { stream: true });
      const lines = pending.split("\n");
      pending = lines.pop() || "";
      for (const line of lines) {
        if (line.trim()) consume(JSON.parse(line));
      }
    }
    pending += decoder.decode();
    if (pending.trim()) consume(JSON.parse(pending));
    if (!answer.trim()) throw new Error("empty reply");

    if (!sawToken) spokenBuffer = answer;
    const tail = pullSentence(spokenBuffer, true);
    if (tail) queueSentence(tail.sentence);
    await ttsQueue;
    const summary = status.summary();
    if (onEvent) onEvent({ kind: "done", reply: answer.trim(), summary });
    return { reply: answer.trim(), tool_calls: toolCalls, audioBase64, summary };
  } finally {
    if (activeChatController === controller) activeChatController = null;
  }
}

function stopChat() {
  if (activeChatController) activeChatController.abort();
  return true;
}

module.exports = { configPath, loadConfig, health, transcribe, chat, stopChat };
