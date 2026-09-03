"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("aishe", {
  status: () => ipcRenderer.invoke("status"),
  transcribe: (wavBase64) => ipcRenderer.invoke("transcribe", wavBase64),
  chat: (text) => ipcRenderer.invoke("chat", text),
  stopChat: () => ipcRenderer.invoke("stop-chat"),
  onChatEvent: (cb) => ipcRenderer.on("chat-event", (_e, ev) => cb(ev)),
  platform: process.platform,
});
