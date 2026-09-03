"use strict";
/**
 * Aishe Live — agent status stream API.
 *
 * A small, dependency-free, testable contract that turns the DeepAgent NDJSON
 * stream into a rich, human-readable live-agent status stream. Every activity
 * the backend reports becomes a typed status event with a friendly narration,
 * and an in-stream tracker accumulates per-tool timings, counts and bytes so a
 * turn can end with a real verbose summary (not a guess).
 *
 * The renderer consumes events via `window.aishe.onChatEvent` — this module is
 * the single place that knows how to interpret them. It is pure (no DOM, no
 * Node globals) and unit-testable in isolation.
 */

// ─── tool → human label/icon ────────────────────────────────────────────
const TOOL_META = {
  memory_search:       { icon: "◎", label: "searching saved context",   verb: "Recall" },
  memory_add:          { icon: "＋", label: "remembering something",    verb: "Remember" },
  memory_update:       { icon: "✎", label: "updating a saved memory",   verb: "Update" },
  memory_delete:       { icon: "⌫", label: "removing a memory",         verb: "Forget" },
  memory_status:       { icon: "◎", label: "reading memory status",     verb: "Memory" },
  web_search:          { icon: "⌕", label: "searching the web",         verb: "Search" },
  get_current_time:    { icon: "◷", label: "checking the current time", verb: "Time" },
  calculator:          { icon: "∑", label: "doing the math",            verb: "Compute" },
  word_stats:          { icon: "≡", label: "analysing the text",        verb: "Analyse" },
  file_read:           { icon: "▤", label: "reading a file",            verb: "Read" },
  file_write:          { icon: "✍", label: "writing to a file",         verb: "Write" },
  shell:               { icon: "⌗", label: "running a command",         verb: "Run" },
  guardrails:          { icon: "? ", label: "checking against guardrails", verb: "Guard" },
};
const DEFAULT_TOOL = { icon: "◆", label: "using a tool", verb: "Use" };

function friendlyTool(name) {
  const k = name || "";
  return TOOL_META[k] || (k.includes("_") || k.includes(".") ? DEFAULT_TOOL : {
    icon: "◆", label: "using " + k, verb: k.replace(/^./, (c) => c.toUpperCase()),
  });
}

// ---------------------------------------------------------------------------
// normalizeEvent — one raw DeepAgent NDJSON line → a directed status event.
// ---------------------------------------------------------------------------
function normalizeEvent(raw, tournCtx) {
  const ctx = tournCtx || { startedAt: Date.now(), tool: null };
  switch (raw.event) {
    case "start":
      return {
        kind: "stage", stage: "start",
        label: raw.model ? "agent online · " + raw.model : "agent online",
        now: nowMs(ctx.startedAt),
      };
    case "stage":
      return { kind: "stage", stage: raw.name || raw.stage, label: stageLabel(raw.name || raw.stage), now: nowMs(ctx.startedAt) };
    case "tool_call": {
      const meta = friendlyTool(raw.name);
      return {
        kind: "tool_start", name: raw.name,
        label: meta.label.toLowerCase(),
        icon: meta.icon,
        args: raw.args,
        started: Date.now(), now: nowMs(ctx.startedAt),
      };
    }
    case "tool_result": {
      const meta = friendlyTool(raw.name || (ctx.tool && ctx.tool.name));
      return {
        kind: "tool_end", name: raw.name || (ctx.tool && ctx.tool.name),
        label: meta.label.toLowerCase(),
        icon: meta.icon,
        result: raw.result,
        ok: raw.ok !== false,
        ms: raw.ms,
        now: nowMs(ctx.startedAt),
      };
    }
    case "token":
      return { kind: "token", content: raw.content || "", now: nowMs(ctx.startedAt) };
    case "error":
      return { kind: "error", message: raw.message || "unknown error", now: nowMs(ctx.startedAt) };
    case "final": {
      const t = Date.now() - ctx.startedAt;
      return { kind: "done", answer: raw.answer, ms: t, now: nowMs(ctx.startedAt) };
    }
    default:
      return null;
  }
}

function stageLabel(name) {
  switch (name) {
    case "reasoning": return "thinking it through";
    case "tooling": return "running tools";
    case "subagent": return "spawned a helper sub-agent";
    case "group": return "co-ordinating helpers";
    case "end": return "wrapping up";
    default: return name ? "phase · " + name : "working";
  }
}

function nowMs(t0) { return Date.now() - t0; }

// ---------------------------------------------------------------------------
// AgentStatusStream — an incremental accumulator: feed lines, read back the
// normalized event + a verbose summary of "everything that happened".
// ---------------------------------------------------------------------------
class AgentStatusStream {
  constructor(userMessage) {
    this.userMessage = userMessage;
    this.startedAt = Date.now();
    this.toolStart = {};          // name → {name, t0, args}
    this.tools = [];              // completed: {name,label,icon,ms,ok,result}
    this.active = null;
    this.tokenCount = 0;
    this.stages = ["start"];
    this.lastStage = "start";
  }

  feed(raw) {
    if (raw.event === "tool_call") {
      if (this.active) this.tools.push(this.active); // tool called without a result edge
      const meta = friendlyTool(raw.name);
      this.active = { name: raw.name, label: meta.label, icon: meta.icon, t0: Date.now(), ok: true };
      return normalizeEvent(raw, this);
    }
    if (raw.event === "tool_result") {
      if (this.active) {
        this.active.ms = Date.now() - this.active.t0;
        this.active.result = typeof raw.result === "string" ? raw.result.slice(0, 220) : "";
        this.active.ok = true;
        raw = { ...raw, name: this.active.name, ms: this.active.ms, result: this.active.result };
        this.tools.push(this.active);
        this.active = null;
      }
      return normalizeEvent(raw, this);
    }
    if (raw.event === "token") this.tokenCount += (raw.content || "").length;
    return normalizeEvent(raw, this);
  }

  summary() {
    if (this.active) this.tools.push(this.active);
    const totalMs = Date.now() - this.startedAt;
    return {
      model: this.model || "",
      message: this.userMessage || "",
      durationMs: totalMs,
      tokenCount: this.tokenCount,
      toolCount: this.tools.length,
      tools: this.tools.map((t) => ({
        name: t.name, label: t.label, icon: t.icon,
        ms: t.ms || 0, ok: t.ok !== false, result: t.result || "",
      })),
    };
  }
}

module.exports = { friendlyTool, normalizeEvent, AgentStatusStream, TOOL_META };