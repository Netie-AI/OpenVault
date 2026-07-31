/**
 * OpenVault Electron Desktop App - Preload Script
 *
 * Secure bridge between renderer (Next.js) and main process (Electron).
 * Whitelist IPC channels; expose window.openvault to the renderer.
 */

const { contextBridge, ipcRenderer } = require("electron");

const DRAG_STYLE_ID = "openvault-electron-drag-region-style";
const DRAG_FALLBACK_ID = "openvault-electron-drag-region";
const DRAG_OBSERVER_KEY = "__openvaultMacDragRegionObserver";

function installMacDragRegion() {
  if (process.platform !== "darwin") return;

  const attach = () => {
    if (!document.head || !document.body) return;

    document.getElementById(DRAG_STYLE_ID)?.remove();
    document.getElementById(DRAG_FALLBACK_ID)?.remove();

    const style = document.createElement("style");
    style.id = DRAG_STYLE_ID;
    style.textContent = `
      header,
      .openvault-electron-drag-region {
        app-region: drag;
        -webkit-app-region: drag;
        user-select: none;
      }

      header a,
      header button,
      header input,
      header select,
      header textarea,
      header [role="button"],
      header [role="link"],
      header [tabindex]:not([tabindex="-1"]) {
        app-region: no-drag;
        -webkit-app-region: no-drag;
      }

      .openvault-electron-drag-region {
        position: fixed;
        top: 0;
        left: 96px;
        right: 180px;
        height: 46px;
        z-index: 9999;
      }
    `;

    const dragRegion = document.createElement("div");
    dragRegion.id = DRAG_FALLBACK_ID;
    dragRegion.className = "openvault-electron-drag-region";
    dragRegion.setAttribute("aria-hidden", "true");

    document.head.appendChild(style);
    document.body.appendChild(dragRegion);

    const syncDragFallback = () => {
      const hasHeader = Boolean(document.querySelector("header"));
      dragRegion.hidden = hasHeader;
      if (hasHeader) observer.disconnect();
    };
    const previousObserver = window[DRAG_OBSERVER_KEY];
    if (previousObserver) previousObserver.disconnect();

    const observer = new MutationObserver(syncDragFallback);
    observer.observe(document.body, { childList: true, subtree: true });
    window[DRAG_OBSERVER_KEY] = observer;
    window.setTimeout(() => observer.disconnect(), 5000);
    window.addEventListener("pagehide", () => observer.disconnect(), { once: true });
    syncDragFallback();
  };

  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", attach, { once: true });
  } else {
    attach();
  }
}

installMacDragRegion();

const VALID_CHANNELS = {
  invoke: [
    "openvault:getAppInfo",
    "openvault:pickFolder",
    "openvault:openExternal",
    "openvault:getClipboardWatch",
    "openvault:setClipboardWatch",
  ],
  receive: ["server-status", "openvault:clipboard-secret"],
};

function safeInvoke(channel, ...args) {
  if (!VALID_CHANNELS.invoke.includes(channel)) {
    return Promise.reject(new Error(`Blocked IPC invoke: ${channel}`));
  }
  return ipcRenderer.invoke(channel, ...args);
}

function safeOn(channel, callback) {
  if (!VALID_CHANNELS.receive.includes(channel)) return () => {};
  const handler = (_event, data) => callback(data);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld("openvault", {
  isDesktop: true,
  platform: process.platform,
  getAppInfo: () => safeInvoke("openvault:getAppInfo"),
  pickFolder: () => safeInvoke("openvault:pickFolder"),
  openExternal: (url) => safeInvoke("openvault:openExternal", url),
  getClipboardWatch: () => safeInvoke("openvault:getClipboardWatch"),
  setClipboardWatch: (enabled) => safeInvoke("openvault:setClipboardWatch", enabled),
  onServerStatus: (callback) => safeOn("server-status", callback),
  onClipboardSecret: (callback) =>
    safeOn("openvault:clipboard-secret", (data) => {
      if (data && typeof data.secret === "string") callback(data.secret);
    }),
});
