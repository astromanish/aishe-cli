"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { Readable } = require("node:stream");
const test = require("node:test");

const servicesPath = path.join(__dirname, "..", "lib", "services.js");

function loadServices(fetchImpl) {
  const originalFetch = global.fetch;
  global.fetch = fetchImpl;
  delete require.cache[require.resolve(servicesPath)];
  const services = require(servicesPath);
  return { services, restore: () => { global.fetch = originalFetch; } };
}

test("chat accepts an AbortSignal and stops an in-flight agent request", async () => {
  let aborted = false;
  const { services, restore } = loadServices((_url, options) => {
    options.signal.addEventListener("abort", () => { aborted = true; });
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => {
        const err = new Error("aborted");
        err.name = "AbortError";
        reject(err);
      });
    });
  });

  const controller = new AbortController();
  const request = services.chat("hello", null, controller.signal);
  controller.abort();

  await assert.rejects(request, (error) => error.name === "AbortError");
  assert.equal(aborted, true);
  restore();
});

test("chat cancellation does not synthesize TTS after the agent is interrupted", async () => {
  let calls = 0;
  const { services, restore } = loadServices(async (url) => {
    calls += 1;
    if (url.endsWith("/stream")) {
      return { ok: true, text: async () => '{"event":"final","answer":"hello"}\n' };
    }
    return { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
  });

  const controller = new AbortController();
  controller.abort();
  await assert.rejects(services.chat("hello", null, controller.signal), (error) => error.name === "AbortError");
  assert.equal(calls, 0);
  restore();
});

test("live interface has no PTT or live-mode controls and starts VAD automatically", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "renderer", "index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "renderer", "app.js"), "utf8");
  assert.doesNotMatch(html, /id=["']ptt["']/);
  assert.doesNotMatch(html, /id=["']livebtn["']/);
  assert.match(app, /liveOn\s*=\s*true/);
  assert.match(app, /ensureMic\(\)/);
  assert.match(app, /stopChat/);
});

test("live window uses a horizontal desktop workspace", () => {
  const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
  assert.match(main, /ipcMain\.handle\(["']stop-chat["']/);
  assert.match(main, /services\.stopChat\(\)/);
  assert.match(main, /width: 1180/);
  assert.match(main, /height: 760/);
  assert.match(main, /minWidth: 940/);
});

test("chat emits TTS audio for a completed sentence before the agent stream ends", async () => {
  const { services, restore } = loadServices(async (url) => {
    if (url.endsWith("/stream")) {
      return {
        ok: true,
        body: Readable.from([
          '{"event":"token","content":"First sentence. "}\n',
          await new Promise((resolve) => setTimeout(() => resolve('{"event":"token","content":"Second sentence."}\n'), 20)),
          '{"event":"final","answer":"First sentence. Second sentence."}\n',
        ].map(Buffer.from)),
      };
    }
    return { ok: true, arrayBuffer: async () => new ArrayBuffer(2) };
  });

  const events = [];
  await services.chat("hello", (event) => events.push(event));
  const audioIndex = events.findIndex((event) => event.kind === "audio_chunk");
  const doneIndex = events.findIndex((event) => event.kind === "done");
  assert.notEqual(audioIndex, -1);
  assert.notEqual(doneIndex, -1);
  assert.ok(audioIndex < doneIndex);
  restore();
});

test("live interface presents agent activity, resource telemetry, completion, approval, and mute affordances", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "renderer", "index.html"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "..", "renderer", "app.js"), "utf8");
  for (const id of ["agent-rail", "resource-strip", "completion-card", "approval-card", "mute"]) {
    assert.ok(html.includes('id="' + id + '"'), "missing " + id);
  }
  assert.doesNotMatch(app, /toolDetails/);
  assert.doesNotMatch(app, /tool activity/);
  assert.doesNotMatch(html, /tool-group/);
  assert.match(app, /setAgentActivity/);
  assert.match(html, /finished while you were away/);
  assert.match(html, /grid-template-columns: minmax\(0, 0\.28fr\) minmax\(0, 0\.9fr\) minmax\(0, 0\.62fr\)/);
  assert.match(html, /class="center-pane"/);
  assert.match(html, /class="agent-pane"/);
  assert.match(html, /grid-column: 1/);
  assert.match(html, /grid-column: 2/);
  assert.match(html, /grid-column: 3/);
  assert.match(html, /◈ LIVE/);
  assert.match(html, /agent-head/);
  assert.match(html, /id="agent-copy"\x3eready/);
  assert.match(app, /setAgentActivity\("muted"/);
  assert.match(app, /showCompletion/);
  assert.match(app, /approval_required/);
  assert.match(app, /friendlyTool/);
  assert.match(app, /setSinkId\(\{ type: "none" \}\)/);
  assert.match(app, /track\.onended/);
  assert.match(app, /MIC MUTED/);
  assert.match(app, /showAgentError/);
  assert.doesNotMatch(app, /addLine\("err"/);
});
