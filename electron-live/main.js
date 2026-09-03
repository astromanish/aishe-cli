"use strict";
/**
 * Aishe Live — Electron main process.
 *
 * Owns the native window, grants mic access, and proxies renderer
 * requests (status / transcribe / chat) to the local Aishe services
 * via lib/services.js. No localhost web server — this IS the app.
 */

const { app, BrowserWindow, ipcMain, session } = require("electron");
const path = require("path");
const services = require("./lib/services");

// Voice assistant: audio must play without a user gesture every time.
app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");

function createWindow() {
  const win = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 940,
    minHeight: 620,
    title: "Aishe Live",
    frame: false, // pet-only: no traffic lights, no titlebar
    backgroundColor: "#0c0c0c",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
    },
  });

  win.once("ready-to-show", () => win.show());
  win.loadFile(path.join(__dirname, "renderer", "index.html"));
}

app.whenReady().then(() => {
  // Grant mic (and nothing else) — the app is a voice assistant.
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    const ok = permission === "media" || permission === "audioCapture" || permission === "microphone";
    callback(ok);
  });

  ipcMain.handle("status", () => services.health());
  ipcMain.handle("transcribe", (_e, wavBase64) => services.transcribe(String(wavBase64 || "")));
  ipcMain.handle("chat", async (e, text) => {
    const wc = e.sender;
    // stream live events (tool calls, tokens) to the renderer for status
    return await services.chat(String(text || ""), (ev) => {
      try { wc.send("chat-event", ev); } catch (_err) { /* window gone */ }
    });
  });
  ipcMain.handle("stop-chat", () => services.stopChat());

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  app.quit();
});
