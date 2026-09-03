"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const AgentStatus = require(path.join(__dirname, "..", "lib", "agent-status.js"));
const services = require(path.join(__dirname, "..", "lib", "services.js"));

test("friendlyTool maps known tools to a human narration + icon", () => {
  assert.equal(AgentStatus.friendlyTool("memory_search").label, "searching saved context");
  assert.equal(AgentStatus.friendlyTool("web_search").icon, "⌕");
  assert.equal(AgentStatus.friendlyTool("calculator").label, "doing the math");
  assert.match(AgentStatus.friendlyTool("something_unknown").label, /using/);
});

test("normalizeEvent maps start/stage/tool_call/tool_result/token/final to typed status events", () => {
  const ctx = { startedAt: Date.now(), tool: null };
  const start = AgentStatus.normalizeEvent({ event: "start", model: "m" }, ctx);
  assert.equal(start.kind, "stage");
  assert.ok(start.label.includes("agent online"));

  const stage = AgentStatus.normalizeEvent({ event: "stage", name: "tooling" }, ctx);
  assert.equal(stage.kind, "stage");
  assert.equal(stage.label, "running tools");

  const ts = AgentStatus.normalizeEvent({ event: "tool_call", name: "memory_search", args: "{}" }, ctx);
  assert.equal(ts.kind, "tool_start");
  assert.equal(ts.icon, "◎");

  const te = AgentStatus.normalizeEvent({ event: "tool_result", name: "memory_search", result: "ok", ms: 12 }, ctx);
  assert.equal(te.kind, "tool_end");

  const tok = AgentStatus.normalizeEvent({ event: "token", content: "hi" }, ctx);
  assert.equal(tok.kind, "token");
  assert.equal(tok.content, "hi");

  const done = AgentStatus.normalizeEvent({ event: "final", answer: "bye" }, ctx);
  assert.equal(done.kind, "done");
});

test("AgentStatusStream accumulates per-tool timing/counts and yields a verbose summary", async () => {
  const stream = new AgentStatus.AgentStatusStream("tell me");
  stream.model = "test-model";
  await new Promise((r) => setTimeout(r, 5));
  stream.feed({ event: "tool_call", name: "memory_search", args: "{}" });
  await new Promise((r) => setTimeout(r, 8));
  stream.feed({ event: "tool_result", result: "found" });
  stream.feed({ event: "token", content: "Answer text here." });
  stream.feed({ event: "final", answer: "Answer text here." });

  const s = stream.summary();
  assert.equal(s.toolCount, 1);
  assert.equal(s.tools.length, 1);
  assert.equal(s.tools[0].name, "memory_search");
  assert.ok(s.tools[0].ms >= 5, "per-tool duration captured");
  assert.ok(s.durationMs >= 5);
  assert.ok(s.tokenCount >= 17, "token chars counted");
  assert.equal(s.message, "tell me");
});

test("services.chat emits verbose typed events (stage/tool_start/tool_end/done) with a summary", async () => {
  const originalFetch = global.fetch;
  global.fetch = async (url) => {
    const body = [
      { event: "start", model: "m" },
      { event: "stage", name: "tooling" },
      { event: "tool_call", name: "calculator", args: "{ }" },
      { event: "tool_result", result: "42" },
      { event: "final", answer: "it is 42" },
    ].map((o) => JSON.stringify(o) + "\n").join("");
    if (url.endsWith("/stream")) {
      const { Readable } = require("node:stream");
      return { ok: true, body: Readable.from([Buffer.from(body)]) };
    }
    return { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
  };
  try {
    const kinds = [];
    const res = await services.chat("x", (ev) => kinds.push(ev.kind));
    assert.ok(kinds.includes("start"));
    assert.ok(kinds.includes("tool_start"));
    assert.ok(kinds.includes("tool_end"));
    assert.ok(kinds.includes("done"));
    assert.equal(res.summary.toolCount, 1);
    assert.equal(res.summary.tools[0].name, "calculator");
  } finally {
    global.fetch = originalFetch;
  }
});