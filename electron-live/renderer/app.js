"use strict";
/**
 * Aishe Live renderer — pixel pet + always-listening live voice.
 * Talks to the services via window.aishe (preload → main → lib/services.js).
 */

// ─── config (from main) ───────────────────────────────────────────
let CFG = { silence_ms: 500, min_speech_ms: 250, rms_threshold: 0.018 };

// ─── pet state ────────────────────────────────────────────────────
let petState = "idle"; // idle|listening|thinking|speaking|dead
let micLevel = 0;
let t0 = performance.now();
let blinkAt = 3000 + Math.random() * 2500;

// live status beside the pet
const statusMain = document.getElementById("status-main");
const statusTool = document.getElementById("status-tool");
const agentIcon = document.getElementById("agent-icon");
const agentCopy = document.getElementById("agent-copy");
const agentDetail = document.getElementById("agent-detail");
const agentRail = document.getElementById("agent-rail");
const agentMeta = document.getElementById("agent-meta");
const resourceModel = document.getElementById("resource-model");
const resourceTools = document.getElementById("resource-tools");
const resourceTime = document.getElementById("resource-time");
const completionCard = document.getElementById("completion-card");
const completionTitle = document.getElementById("completion-title");
const completionCopy = document.getElementById("completion-copy");
const approvalCard = document.getElementById("approval-card");
const approvalTitle = document.getElementById("approval-title");
const approvalCopy = document.getElementById("approval-copy");
const muteButton = document.getElementById("mute");
const micLabel = document.getElementById("mic-label");
let currentTool = null; // name of the tool currently running
let agentStartedAt = 0;
let agentToolCount = 0;
let lastReply = "";
let micMuted = false;

const TOOL_META = {
  memory_search: ["◎", "checking saved context"],
  memory_add: ["＋", "saving this to memory"],
  calculator: ["∑", "working through the numbers"],
  get_current_time: ["◷", "checking the current time"],
  word_stats: ["≡", "checking the text"],
  web_search: ["⌕", "searching the web"],
  default: ["◆", "using a tool"]
};

function friendlyTool(name) {
  const meta = TOOL_META[name] || TOOL_META.default;
  return { icon: meta[0], text: meta[1] };
}

function setAgentActivity(copy, detail = "", icon = "◌", phase = "ready") {
  agentIcon.textContent = icon;
  agentCopy.textContent = copy;
  agentDetail.textContent = detail;
  agentMeta.textContent = phase;
  agentRail.classList.toggle("has-error", phase === "error");
}

function showAgentError(message) {
  setAgentActivity("error", message, "×", "error");
}

function updateResources() {
  const elapsed = agentStartedAt ? ((performance.now() - agentStartedAt) / 1000).toFixed(1) : "0.0";
  resourceTools.textContent = agentToolCount + (agentToolCount === 1 ? " tool" : " tools");
  resourceTime.textContent = elapsed + "s";
}

function showCompletion(reply, summary) {
  lastReply = reply || "";
  const first = lastReply.split(/[.!?…](?:\s|$)/)[0].trim();
  completionTitle.textContent = first ? first.slice(0, 84) : "Aishe has something for you";
  const s = summary || {};
  const bits = [];
  if (s.toolCount) bits.push(s.toolCount + " tool" + (s.toolCount === 1 ? "" : "s"));
  if (s.durationMs) bits.push((s.durationMs / 1000).toFixed(1) + "s");
  if (s.tokenCount) bits.push(s.tokenCount + " tokens");
  if (bits.length) bits.push("context preserved");
  completionCopy.textContent = bits.join(" · ") || "answer ready · context preserved";
  completionCard.hidden = false;
}

function hideCompletion() { completionCard.hidden = true; }
function hideApproval() { approvalCard.hidden = true; }

const STATUS_TEXT = {
  idle: "",
  listening: "listening…",
  transcribing: "transcribing…",
  thinking: "thinking…",
  speaking: "speaking…",
  dead: "offline"
};

function renderStatus() {
  statusMain.textContent = STATUS_TEXT[petState] || "";
  if (currentTool) {
    statusTool.textContent = "◆ " + currentTool;
    statusTool.classList.remove("done");
  } else {
    statusTool.textContent = "";
    statusTool.classList.remove("done");
  }
}

function petSet(st) {
  petState = st;
  renderStatus();
  if (st === "idle" && (micMuted || !liveOn)) {
    setAgentActivity("muted", "", "○", "paused");
  } else if (st === "listening") setAgentActivity("listening", "", "◉", "ready");
  else if (st === "transcribing") setAgentActivity("transcribing", "", "↯", "transcribing");
  else if (st === "thinking") setAgentActivity("thinking", "", "◌", "active");
  else if (st === "speaking") setAgentActivity("speaking", "", "↗", "speaking");
  else if (st === "dead") setAgentActivity("offline", "", "×", "blocked");
  updateResources();
}

// Between turns the assistant immediately returns to listening.
function petIdle() {
  petSet(liveOn ? "listening" : "idle");
}

// ─── canvas / sprite ──────────────────────────────────────────────
const S = 12, OY = 3; // pixel scale, sprite offset (room for spark + dots)
const CW = 16 * S, CH = 19 * S; // 192 × 228
const canvas = document.getElementById("pet");
const ctx = canvas.getContext("2d");
const DPR = Math.min(2, window.devicePixelRatio || 1);
canvas.width = CW * DPR;
canvas.height = CH * DPR;
ctx.scale(DPR, DPR);

const SPRITE = [
  "......OOOO......",
  "....OHOOOOOH....",
  "...OHAOOOOAHO...",
  "..OHAOOOOOOAHO..",
  ".OHAOOOOOOOOAHO.",
  ".OAAAAAAAAAAAAO.",
  ".OAAAAAAAAAAAAO.",
  ".OAAAAAAAAAAAAO.",
  ".OAAAAAAAAAAAAO.",
  ".OAAAAAAAAAAAAO.",
  ".OAAAAAAAAAAAAO.",
  ".OAAAAAAAAAAAAO.",
  ".OASAAAAAAAAASO.",
  "..OASAAAAAAASO..",
  "...OAAAAAAAAO...",
  "....OAAAAAAO....",
];
const PAL = { O: "#0c0c0c", A: "#d4a24e", S: "#a97e3a", H: "#0c0c0c" };
const DEAD_PAL = { O: "#2a2a26", A: "#6b675e", S: "#57544d", H: "#2a2a26" };
const EYE = "#0c0c0c", WHITE = "#f4ead8", CHEEK = "#b8863f", MOUTH = "#0c0c0c";

function px(c, r, col, dy) {
  ctx.fillStyle = col;
  ctx.fillRect(c * S, (r + OY + dy) * S, S, S);
}

function drawPet() {
  const t = (performance.now() - t0) / 1000;
  const st = petState;
  const dead = st === "dead";
  ctx.clearRect(0, 0, CW, CH);

  let dy = 0;
  if (!dead) {
    if (st === "idle") dy = Math.round(Math.sin(t * 2) * 0.35);
    else if (st === "listening") dy = Math.round(Math.sin(t * 9) * 0.6 * (0.3 + 0.7 * micLevel));
    else if (st === "speaking") dy = Math.round(Math.sin(t * 11) * 0.6);
  }

  // ground shadow (fixed)
  ctx.fillStyle = "rgba(12,12,12,0.30)";
  ctx.fillRect(3 * S, (15 + OY) * S + 4, 10 * S, 2);

  // antenna spark — the "alive" pulse
  if (!dead) {
    const glow = 0.5 + 0.5 * Math.sin(t * 2.4);
    ctx.fillStyle = "rgba(212,162,78," + glow.toFixed(3) + ")";
    ctx.fillRect(7 * S, (OY - 2 + dy) * S, 2 * S, 2 * S);
  }

  // thinking dots above head (also during transcribing)
  if (st === "thinking" || st === "transcribing") {
    const phase = Math.floor(t * 2.2) % 3;
    for (let i = 0; i < 3; i++) {
      const dotY = (i === phase ? 0 : 1);
      px(6 + i * 2, dotY, "#a09a8e", dy);
    }
  }

  // body
  const pal = dead ? DEAD_PAL : PAL;
  for (let r = 0; r < 16; r++) {
    for (let c = 0; c < 16; c++) {
      const ch = SPRITE[r][c];
      if (ch === ".") continue;
      ctx.fillStyle = pal[ch] || pal.A;
      ctx.fillRect(c * S, (r + OY + dy) * S, S, S);
    }
  }

  if (dead) {
    px(5, 7, EYE, dy); px(6, 8, EYE, dy); px(6, 7, EYE, dy); px(5, 8, EYE, dy);
    px(9, 7, EYE, dy); px(10, 8, EYE, dy); px(10, 7, EYE, dy); px(9, 8, EYE, dy);
    px(7, 10, EYE, dy); px(8, 10, EYE, dy);
    return;
  }

  px(3, 10, CHEEK, dy); px(4, 10, CHEEK, dy);
  px(11, 10, CHEEK, dy); px(12, 10, CHEEK, dy);

  const blinking = (t * 1000 > blinkAt && t * 1000 < blinkAt + 160);
  if (t * 1000 > blinkAt + 160) blinkAt = t * 1000 + 2500 + Math.random() * 3000;

  if (st === "listening") {
    px(5, 6, WHITE, dy); px(6, 6, WHITE, dy); px(9, 6, WHITE, dy); px(10, 6, WHITE, dy);
    px(5, 7, EYE, dy); px(6, 7, EYE, dy); px(9, 7, EYE, dy); px(10, 7, EYE, dy);
    px(5, 8, EYE, dy); px(6, 8, EYE, dy); px(9, 8, EYE, dy); px(10, 8, EYE, dy);
    px(7, 9, MOUTH, dy); px(8, 9, MOUTH, dy);
    if (micLevel > 0.2) {
      const h = Math.max(1, Math.round(micLevel * 3));
      ctx.fillStyle = "rgba(212,162,78,0.55)";
      for (let i = 0; i < h; i++) {
        ctx.fillRect(1 * S, (9 - i + OY + dy) * S, S, S);
        ctx.fillRect(14 * S, (9 - i + OY + dy) * S, S, S);
      }
    }
  } else if (st === "speaking") {
    px(5, 7, EYE, dy); px(6, 7, EYE, dy); px(9, 7, EYE, dy); px(10, 7, EYE, dy);
    px(5, 8, EYE, dy); px(6, 8, EYE, dy); px(9, 8, EYE, dy); px(10, 8, EYE, dy);
    px(7, 9, MOUTH, dy); px(8, 9, MOUTH, dy);
    if (Math.sin(t * 12) > 0) { px(7, 10, MOUTH, dy); px(8, 10, MOUTH, dy); }
  } else if (blinking) {
    px(5, 7, EYE, dy); px(6, 7, EYE, dy); px(9, 7, EYE, dy); px(10, 7, EYE, dy);
    px(7, 9, MOUTH, dy); px(8, 9, MOUTH, dy);
  } else {
    px(5, 7, EYE, dy); px(6, 7, EYE, dy); px(9, 7, EYE, dy); px(10, 7, EYE, dy);
    px(5, 8, EYE, dy); px(6, 8, EYE, dy); px(9, 8, EYE, dy); px(10, 8, EYE, dy);
    px(7, 9, MOUTH, dy); px(8, 9, MOUTH, dy);
  }
  requestAnimationFrame(drawPet);
}

// ─── transcript ────────────────────────────────────────────────────
const chatEl = document.getElementById("chat");

function addLine(role, text, tools) {
  const row = document.createElement("div");
  row.className = "t " + role;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = role === "you" ? "you ›" : (role === "err" ? "err ›" : "aishe ›");
  row.appendChild(who);
  row.appendChild(document.createTextNode(" " + text));
  if (tools && tools.length) {
    const wrap = document.createElement("div");
    wrap.className = "tools";
    for (const t of tools) {
      const el = document.createElement("div");
      el.className = "tool";
      const name = document.createElement("div");
      name.className = "tname";
      name.textContent = "◆ " + (typeof t === "string" ? t : t.name);
      el.appendChild(name);
      if (typeof t === "object" && t.args) {
        const a = document.createElement("div");
        a.className = "targs";
        a.textContent = "args  " + t.args;
        el.appendChild(a);
      }
      if (typeof t === "object" && t.result) {
        const r = document.createElement("div");
        r.className = "tresult";
        r.textContent = "→ " + t.result;
        el.appendChild(r);
      }
      wrap.appendChild(el);
    }
    row.appendChild(wrap);
  }
  chatEl.appendChild(row);
  while (chatEl.children.length > 40) chatEl.removeChild(chatEl.firstChild);
  chatEl.scrollTop = chatEl.scrollHeight;
}

// ─── audio: mic, recorder, VAD, wav ────────────────────────────────
let stream = null, actx = null, analyser = null, dataArr = null;
let utterRec = null, utterChunks = [];
let speechActive = false, speechStarted = 0, lastSpeech = 0, processing = false, suppressing = false;
let liveOn = true;
let activeTurn = 0;
let audioFinish = null;
let audioQueue = [];
let audioPlaying = false;
let audioChunksThisTurn = 0;
const audioEl = new Audio();

async function ensureMic() {
  if (stream && stream.getAudioTracks().some((track) => track.readyState === "live")) return;
  stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
  });
  const track = stream.getAudioTracks()[0];
  if (track) {
    track.onended = () => {
      stream = null;
      speechActive = false;
      liveOn = false;
      micMuted = true;
      muteButton.classList.add("muted");
      micLabel.textContent = "MIC MUTED";
      petSet("idle");
    };
  }
  actx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: "interactive" });
  // VAD only needs analyser samples. Do not bind the context to the user's
  // output device: Bluetooth playback failures otherwise kill microphone input.
  if (typeof actx.setSinkId === "function") {
    try { await actx.setSinkId({ type: "none" }); } catch (_e) { /* older Chromium */ }
  }
  actx.onstatechange = () => {
    if (actx && actx.state === "closed") {
      setAgentActivity("audio off", "", "×", "blocked");
    }
  };
  const src = actx.createMediaStreamSource(stream);
  analyser = actx.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.3;
  src.connect(analyser);
  dataArr = new Uint8Array(analyser.fftSize);
}

function rms() {
  if (!analyser) return 0;
  analyser.getByteTimeDomainData(dataArr);
  let sum = 0;
  for (let i = 0; i < dataArr.length; i++) {
    const v = (dataArr[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / dataArr.length);
}

function encodeWav(samples, sampleRate) {
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const ws = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  ws(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); ws(8, "WAVE");
  ws(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
  v.setUint16(22, 1, true); v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  ws(36, "data"); v.setUint32(40, n * 2, true);
  let off = 44;
  for (let i = 0; i < n; i++) {
    const s = Math.max(-32768, Math.min(32767, Math.round(samples[i] * 32767)));
    v.setInt16(off, s, true); off += 2;
  }
  return new Blob([buf], { type: "audio/wav" });
}

async function toWav16k(blob) {
  const buf = await blob.arrayBuffer();
  let ab;
  try { ab = await actx.decodeAudioData(buf); }
  catch (e) { throw new Error("audio decode failed: " + e.message); }
  const srcRate = ab.sampleRate;
  const len = Math.max(1, Math.floor(ab.duration * 16000));
  const out = new Float32Array(len);
  const ch = ab.getChannelData(0);
  for (let i = 0; i < len; i++) {
    const idx = i * srcRate / 16000;
    const i0 = Math.floor(idx);
    const i1 = Math.min(i0 + 1, ch.length - 1);
    const frac = idx - i0;
    out[i] = ch[i0] * (1 - frac) + ch[i1] * frac;
  }
  return encodeWav(out, 16000);
}

function blobToBase64(blob) {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result).split(",")[1] || "");
    fr.onerror = rej;
    fr.readAsDataURL(blob);
  });
}

// live voice: ONE recorder per utterance, stopped at silence end.
function startUtterance() {
  if (!stream || utterRec) return;
  utterChunks = [];
  utterRec = new MediaRecorder(stream);
  utterRec.ondataavailable = (e) => { if (e.data && e.data.size) utterChunks.push(e.data); };
  utterRec.start();
}

function endUtterance() {
  if (!utterRec) return;
  processing = true; // block VAD until the turn is fully done
  const rec = utterRec;
  utterRec = null;
  const done = new Promise((res) => {
    rec.onstop = () => res(new Blob(utterChunks, { type: rec.mimeType || "audio/webm" }));
  });
  rec.stop();
  done.then(runTurn).catch(() => { processing = false; });
}

// ─── streaming chat row ───────────────────────────────────────────
let streamRow = null, streamBody = null, streamCursor = null;
let toolCount = 0;

function startStreamRow() {
  streamRow = document.createElement("div");
  streamRow.className = "t aishe";
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = "aishe ›";
  streamRow.appendChild(who);
  streamBody = document.createElement("span");
  streamBody.className = "sbody";
  streamRow.appendChild(streamBody);
  streamCursor = document.createElement("span");
  streamCursor.className = "cur";
  streamCursor.textContent = "▍";
  streamRow.appendChild(streamCursor);
  chatEl.appendChild(streamRow);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function appendToken(text) {
  if (!streamBody) return;
  streamBody.textContent += text;
  chatEl.scrollTop = chatEl.scrollHeight;
}

function finishStreamRow(fullReply) {
  if (streamCursor) { streamCursor.remove(); streamCursor = null; }
  if (streamBody && fullReply && streamBody.textContent !== fullReply) {
    streamBody.textContent = fullReply; // authoritative final text
  }
  streamRow = null; streamBody = null; toolCount = 0;
}

// ─── the turn pipeline ─────────────────────────────────────────────
async function runTurn(blob) {
  if (!blob || !blob.size) { processing = false; return; }
  const turn = ++activeTurn;
  const current = () => turn === activeTurn;
  currentTool = null;
  agentStartedAt = performance.now();
  agentToolCount = 0;
  hideCompletion();
  hideApproval();
  updateResources();
  try {
    petSet("transcribing");
    const wav = await toWav16k(blob);
    const b64 = await blobToBase64(wav);
    let text = "";
    try {
      text = (await window.aishe.transcribe(b64)) || "";
    } catch (e) {
      if (!current()) return;
      showAgentError( "STT failed: " + e.message);
      return;
    }
    if (!current()) return;
    if (!text) {
      showAgentError( "(no speech recognized)");
      return;
    }
    addLine("you", text);

    petSet("thinking");
    startStreamRow();
    audioChunksThisTurn = 0;
    let res;
    try {
      res = await window.aishe.chat(text);
    } catch (e) {
      if (!current()) return;
      finishStreamRow(null);
      showAgentError( "DeepAgent error: " + e.message);
      return;
    }
    if (!current()) return;
    const reply = (res.reply || "").trim();
    finishStreamRow(reply);
    showCompletion(reply);
    setAgentActivity("done", "", "✓", "complete");
    if (reply && !audioChunksThisTurn) {
      showAgentError( "(no audio — TTS unavailable, text only)");
    }
    if (!audioPlaying) petIdle();
  } catch (e) {
    if (!current()) return;
    finishStreamRow(null);
    showAgentError( "error: " + e.message);
  } finally {
    if (current()) processing = false;
  }
}

function playAudioB64(b64) {
  return new Promise((resolve) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const blob = new Blob([bytes], { type: "audio/wav" });
    const url = URL.createObjectURL(blob);
    let done = false;
    const finish = () => {
      if (!done) {
        done = true;
        if (audioFinish === finish) audioFinish = null;
        URL.revokeObjectURL(url);
        resolve();
      }
    };
    audioFinish = finish;
    audioEl.onended = finish;
    audioEl.onerror = () => { showAgentError( "audio playback failed"); finish(); };
    audioEl.src = url;
    const p = audioEl.play();
    if (p) p.catch(() => { console.warn("audio play blocked"); finish(); });
  });
}

function enqueueAudio(b64) {
  if (!b64) return;
  audioChunksThisTurn += 1;
  audioQueue.push(b64);
  drainAudioQueue();
}

async function drainAudioQueue() {
  if (audioPlaying) return;
  audioPlaying = true;
  suppressing = true;
  while (audioQueue.length) {
    petSet("speaking");
    await playAudioB64(audioQueue.shift());
  }
  audioPlaying = false;
  suppressing = false;
  if (!processing) petIdle();
}

async function interruptCurrentTurn() {
  if (petState !== "speaking") return;
  activeTurn += 1;
  audioEl.pause();
  audioEl.currentTime = 0;
  if (audioFinish) audioFinish();
  audioFinish = null;
  audioQueue = [];
  suppressing = false;
  currentTool = null;
  processing = false;
  try { await window.aishe.stopChat(); } catch (_e) { /* best effort */ }
  petSet("listening");
}

// ─── VAD loop ──────────────────────────────────────────────────────
function vadTick() {
  const level = rms();
  micLevel = Math.min(1, level / 0.3);

  if (liveOn && stream) {
    if (level > CFG.rms_threshold) {
      if (!speechActive) {
        speechActive = true;
        speechStarted = performance.now();
        if (petState === "speaking") interruptCurrentTurn();
        if (!processing) startUtterance();
      }
      if (petState === "speaking" && performance.now() - speechStarted >= CFG.min_speech_ms) {
        interruptCurrentTurn();
        startUtterance();
      }
      lastSpeech = performance.now();
      if (petState !== "thinking" && petState !== "speaking") petSet("listening");
    } else if (speechActive) {
      if (performance.now() - lastSpeech > CFG.silence_ms) {
        speechActive = false;
        if (utterRec) endUtterance();
      }
    }
  } else {
    speechActive = false;
  }
}
setInterval(vadTick, 60);

const hintEl = document.getElementById("hint");

async function startAlwaysLive() {
  try {
    await ensureMic();
    if (actx && actx.state === "suspended") await actx.resume();
    hintEl.textContent = "always listening · speak anytime · interrupt Aishe while speaking";
    petSet("listening");
  } catch (err) {
    liveOn = false;
    showAgentError( "mic access denied: " + err.message);
  }
}

// ─── live chat events → verbose narration feed + status beside pet ─────
const activityEl = document.getElementById("activity");

function addActivity(cls, parts) {
  if (!activityEl) return;
  const row = document.createElement("div");
  row.className = "feed-row " + (cls || "");
  const t = document.createElement("span");
  t.className = "t";
  t.textContent = agentStartedAt ? ((performance.now() - agentStartedAt) / 1000).toFixed(1) + "s" : "0.0s";
  row.appendChild(t);
  const body = document.createElement("span");
  body.className = "txt";
  for (const p of parts) {
    if (p && p.className) {
      const s = document.createElement("span");
      s.className = p.className;
      s.textContent = p.text || "";
      body.appendChild(s);
    } else if (p) {
      body.appendChild(document.createTextNode(p.text || String(p)));
    }
  }
  row.appendChild(body);
  activityEl.appendChild(row);
  while (activityEl.children.length > 60) activityEl.removeChild(activityEl.firstChild);
  activityEl.scrollTop = activityEl.scrollHeight;
}

window.aishe.onChatEvent((ev) => {
  if (!ev) return;
  if (ev.kind === "start") {
    hideCompletion(); hideApproval();
    activityEl.innerHTML = "";
    return;
  }
  if (ev.kind === "stage") {
    activityEl.hidden = false;
    addActivity("stage", [ev.label || ("phase · " + (ev.stage || ""))]);
    setAgentActivity(firstword(ev.label || "working"), "", "▶", "active");
  } else if (ev.kind === "tool_start") {
    currentTool = ev.name;
    agentToolCount += 1;
    activityEl.hidden = false;
    addActivity("tool_start", [{ className: "ico", text: ev.icon || "◆" }, ev.label || "using a tool"]);
    setAgentActivity(firstword(ev.label || "working"), "", ev.icon || "◆", "active");
    petSet("thinking");

  } else if (ev.kind === "tool_end") {
    const ms = typeof ev.ms === "number" ? Math.round(ev.ms) : null;
    activityEl.hidden = false;
    addActivity("tool_end", [
      { className: "ico", text: "✓" },
      (ev.label || "tool done") + (ms ? " · " + ms + "ms" : ""),
    ]);
    if (ev.result) {
      const t = document.createElement("div");
      t.className = "res";
      t.textContent = "→ " + ev.result;
      if (activityEl.lastElementChild) activityEl.lastElementChild.appendChild(t);
    }
    const meta = friendlyTool(ev.name);
    statusTool.textContent = "✓ " + (meta.text || ev.name);
    statusTool.classList.add("done");
    setAgentActivity("working", "", "✓", "active");
  } else if (ev.kind === "approval_required") {
    approvalTitle.textContent = ev.title || "Aishe is ready to act";
    approvalCopy.textContent = ev.summary || ev.message || "This action needs your approval before it continues.";
    approvalCard.hidden = false;
    setAgentActivity("approval", "", "?", "approval");
    petSet("thinking");
  } else if (ev.kind === "approval_resolved") {
    hideApproval();
    setAgentActivity(ev.approved ? "approved" : "stopped", "", ev.approved ? "→" : "×", ev.approved ? "active" : "cancelled");
  } else if (ev.kind === "token") {
    appendToken(ev.content || "");
  } else if (ev.kind === "audio_chunk") {
    enqueueAudio(ev.audioBase64 || "");
  } else if (ev.kind === "error") {
    currentTool = null;
    renderStatus();
    addActivity("error", [].concat(ev.message || "something went wrong"));
    showAgentError( "event: " + (ev.message || "?"));
  } else if (ev.kind === "done") {
    currentTool = null;
    renderStatus();
    activityEl.hidden = false;
    const s = ev.summary || {};
    addActivity("done", [
      "done in " + ((s.durationMs || 0) / 1000).toFixed(1) + "s",
      s.toolCount ? " · " + s.toolCount + " tool" + (s.toolCount === 1 ? "" : "s") : "",
      (s.tokenCount || 0) ? " · " + s.tokenCount + " tok" : "",
    ]);
    showCompletion(ev.reply || "", s);
  }
});

function firstword(s) {
  const m = String(s).split(" ");
  return m[0] ? m[0].replace(/[.!?…]/g, "") : "working";
}

// ─── status polling (alive check only — the pet IS the status) ───
async function pollStatus() {
  try {
    const st = await window.aishe.status();

    if (st.live) {
      if (typeof st.live.silence_ms === "number") CFG.silence_ms = st.live.silence_ms;
      if (typeof st.live.min_speech_ms === "number") CFG.min_speech_ms = st.live.min_speech_ms;
      if (typeof st.live.rms_threshold === "number") CFG.rms_threshold = st.live.rms_threshold;
      hintEl.textContent = "always listening · speak anytime · interrupt Aishe while speaking";
    }

    const da = (st.services || {}).deepagent;
    if (st.model) resourceModel.textContent = st.model;
    if (da && !da.up) {
      petSet("dead");
    } else if (petState === "dead" && !processing) {
      petSet("idle");
    }
  } catch (e) { /* best-effort */ }
}
setInterval(pollStatus, 3000);
pollStatus();
startAlwaysLive();

// tiny close affordance — Cmd+W / Cmd+Q also work
muteButton.addEventListener("click", async () => {
  micMuted = !micMuted;
  liveOn = !micMuted;
  muteButton.classList.toggle("muted", micMuted);
  micLabel.textContent = micMuted ? "MIC MUTED" : "MIC ACTIVE";
  if (micMuted) {
    if (utterRec) endUtterance();
    speechActive = false;
    setAgentActivity("muted", "", "○", "paused");
    petSet("idle");
  } else {
    await startAlwaysLive();
  }
});

document.getElementById("completion-open").addEventListener("click", () => {
  completionCard.hidden = true;
  chatEl.scrollTop = chatEl.scrollHeight;
});
document.getElementById("completion-dismiss").addEventListener("click", hideCompletion);
document.getElementById("approval-cancel").addEventListener("click", () => {
  hideApproval();
  setAgentActivity("stopped", "", "×", "cancelled");
});
document.getElementById("approval-allow").addEventListener("click", () => {
  hideApproval();
  setAgentActivity("approved", "", "→", "active");
  window.dispatchEvent(new CustomEvent("aishe-approval", { detail: { approved: true } }));
});

document.getElementById("close").addEventListener("click", () => window.close());

requestAnimationFrame(drawPet);
