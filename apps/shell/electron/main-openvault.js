/**
 * OpenVault Electron Desktop Shell
 *
 * Patterns from OmniRoute electron/main.js:
 * waitForServer, createTray, CSP, before-quit, single-instance, processTree.
 */

const {
  app,
  BrowserWindow,
  ipcMain,
  Tray,
  Menu,
  nativeImage,
  shell,
  session,
  dialog,
  clipboard,
} = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const { killProcessTree } = require("./processTree");

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
  process.exit(0);
}

const isDev = process.env.NODE_ENV === "development" || !app.isPackaged;
const USE_SHELL = process.platform === "win32";

// apps/shell/electron → repo root (OpenVault)
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const OPENMW_DIR = path.join(REPO_ROOT, "OpenMW");
const WEB_DIR = path.join(REPO_ROOT, "apps", "web");
const ASSETS_PATH = path.join(__dirname, "..", "assets");

const API_HOST = "127.0.0.1";
const API_PORT = 5000;
const WEB_PORT = 3010;
const API_HEALTH_URL = `http://${API_HOST}:${API_PORT}/api/healthz`;
const WEB_URL = `http://${API_HOST}:${WEB_PORT}`;

let mainWindow = null;
let tray = null;
let apiServer = null;
let webServer = null;
let serversStopped = false;
let clipboardTimer = null;
let lastClipboardText = "";

/** Cheap shape check — full provider inference stays in the Next renderer. */
function looksLikeApiSecretRough(text) {
  if (typeof text !== "string") return false;
  const value = text.trim();
  if (value.length < 16 || value.length > 512) return false;
  if (/\s/.test(value)) return false;
  return /^(sk-|sk-ant-|sk-or-v1-|gsk_|hf_|AIza|xai-|pplx-|r8_|csk-|nvapi-|fw_|tgp_v1_|ms-|ghp_|gho_|ghu_|ghs_|github_pat_)/i.test(
    value
  );
}

function startClipboardWatch() {
  if (clipboardTimer) return;
  clipboardTimer = setInterval(() => {
    try {
      const text = clipboard.readText();
      if (!text || text === lastClipboardText) return;
      lastClipboardText = text;
      if (!looksLikeApiSecretRough(text)) return;
      sendToRenderer("openvault:clipboard-secret", { secret: text.trim() });
    } catch {
      /* clipboard busy */
    }
  }, 800);
}

function stopClipboardWatch() {
  if (clipboardTimer) {
    clearInterval(clipboardTimer);
    clipboardTimer = null;
  }
}

/** Default OFF — CLIPDROP_CONTRACT §3: background poll needs a visible Settings toggle. */
let clipboardWatchEnabled = false;

function setClipboardWatchEnabled(enabled) {
  clipboardWatchEnabled = Boolean(enabled);
  if (clipboardWatchEnabled) startClipboardWatch();
  else stopClipboardWatch();
  return clipboardWatchEnabled;
}

//: label -> non-zero exit code, for the readiness dialog. sendToRenderer cannot
//: carry this: mainWindow is still null while the servers start, so a start-up
//: failure is dropped on the floor, and nothing in apps/web subscribes to
//: "server-status" anyway.
const lastProcessExit = {};

function sendToRenderer(channel, data) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, data);
  }
}

async function waitForServer(url, timeoutMs = 180000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.ok || res.status < 500) return true;
    } catch {
      /* server not ready yet */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  console.warn("[OpenVault] Server readiness timeout — showing window anyway:", url);
  return false;
}

async function waitForServerExit(proc, timeoutMs = 5000) {
  if (!proc) return;
  await Promise.race([
    new Promise((r) => proc.once("exit", r)),
    new Promise((r) =>
      setTimeout(() => {
        try {
          killProcessTree(proc, { signal: "SIGKILL" });
        } catch {
          /* already dead */
        }
        r();
      }, timeoutMs)
    ),
  ]);
}

function attachProcessLogging(proc, label) {
  proc.stdout?.on("data", (data) => {
    process.stdout.write(`[${label}] ${data}`);
  });
  proc.stderr?.on("data", (data) => {
    process.stderr.write(`[${label}:err] ${data}`);
  });
  proc.on("error", (err) => {
    console.error(`[OpenVault] Failed to start ${label}:`, err);
    sendToRenderer("server-status", { status: "error", service: label });
  });
  proc.on("exit", (code) => {
    console.log(`[OpenVault] ${label} exited with code:`, code);
    if (code) lastProcessExit[label] = code;
    sendToRenderer("server-status", { status: "stopped", service: label, code });
  });
}

function startApiServer() {
  console.log("[OpenVault] Starting FastAPI on", API_HEALTH_URL);
  sendToRenderer("server-status", { status: "starting", service: "api", port: API_PORT });

  apiServer = spawn(
    "uv",
    [
      "run",
      "--directory",
      OPENMW_DIR,
      "openmw",
      // `console`, not `serve`: the Typer app registers console/demo-ui/doctor/
      // infer/route/train and nothing else, so `serve` exited 2 on every cold
      // start. Electron is the browser here, so keep the CLI from opening a
      // second one.
      "console",
      "--host",
      API_HOST,
      "--port",
      String(API_PORT),
      "--no-open-browser",
    ],
    {
      cwd: OPENMW_DIR,
      env: { ...process.env },
      stdio: "pipe",
      windowsHide: true,
      shell: USE_SHELL,
    }
  );
  attachProcessLogging(apiServer, "api");
}

function startWebServer() {
  const npmScript = process.env.OPENVAULT_DEV === "1" ? "dev" : "start";
  console.log("[OpenVault] Starting Next.js on", WEB_URL, `(${npmScript})`);
  sendToRenderer("server-status", { status: "starting", service: "web", port: WEB_PORT });

  webServer = spawn("npm", ["--prefix", WEB_DIR, "run", npmScript], {
    cwd: REPO_ROOT,
    env: { ...process.env },
    stdio: "pipe",
    windowsHide: true,
    shell: USE_SHELL,
  });
  attachProcessLogging(webServer, "web");
}

function stopServers() {
  if (apiServer) {
    killProcessTree(apiServer, { signal: "SIGTERM" });
    apiServer = null;
  }
  if (webServer) {
    killProcessTree(webServer, { signal: "SIGTERM" });
    webServer = null;
  }
}

function setupContentSecurityPolicy() {
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const scriptSrc = isDev
      ? "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:"
      : "script-src 'self' 'unsafe-inline' blob:";

    const csp = [
      "default-src 'self'",
      "base-uri 'self'",
      "object-src 'none'",
      "frame-ancestors 'none'",
      "frame-src 'none'",
      "child-src 'none'",
      "form-action 'self'",
      "connect-src 'self' http://127.0.0.1:5000 http://127.0.0.1:3010",
      scriptSrc,
      "script-src-attr 'none'",
      "style-src 'self' 'unsafe-inline'",
      "font-src 'self' data:",
      "img-src 'self' data: blob:",
      "media-src 'self' data: blob:",
      "worker-src 'self' blob:",
      "manifest-src 'self'",
    ].join("; ");

    callback({
      responseHeaders: {
        ...details.responseHeaders,
        "Content-Security-Policy": [csp],
      },
    });
  });
}

function createWindow() {
  const platformWindowOptions =
    process.platform === "darwin"
      ? { titleBarStyle: "hiddenInset", trafficLightPosition: { x: 16, y: 16 } }
      : { titleBarStyle: "default" };

  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: "OpenVault",
    icon: path.join(ASSETS_PATH, "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload-openvault.js"),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
      webviewTag: false,
    },
    show: false,
    backgroundColor: "#0a0a0a",
    autoHideMenuBar: true,
    ...platformWindowOptions,
  });

  mainWindow.loadURL(WEB_URL);

  if (isDev) {
    mainWindow.webContents.openDevTools({ mode: "detach" });
  }

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const parsedUrl = new URL(url);
      if (["http:", "https:"].includes(parsedUrl.protocol)) {
        shell.openExternal(url);
      } else {
        console.warn("[OpenVault] Blocked unsafe protocol:", parsedUrl.protocol);
      }
    } catch {
      console.error("[OpenVault] Blocked invalid URL:", url);
    }
    return { action: "deny" };
  });

  mainWindow.on("close", (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      mainWindow.hide();
    }
    return false;
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function createTray() {
  if (tray) {
    tray.destroy();
    tray = null;
  }

  const iconPath = path.join(ASSETS_PATH, "tray-icon.png");
  let icon;
  try {
    icon = nativeImage.createFromPath(iconPath);
    if (icon.isEmpty()) icon = nativeImage.createEmpty();
    if (process.platform === "darwin" && !icon.isEmpty()) {
      icon = icon.resize({ width: 20, height: 20 });
      icon.setTemplateImage(true);
    }
  } catch {
    icon = nativeImage.createEmpty();
  }

  tray = new Tray(icon);

  const contextMenu = Menu.buildFromTemplate([
    {
      label: "Open OpenVault",
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.focus();
        }
      },
    },
    {
      label: "Open in Browser",
      click: () => shell.openExternal(WEB_URL),
    },
    { type: "separator" },
    {
      label: "Quit",
      click: () => {
        app.isQuitting = true;
        app.quit();
      },
    },
  ]);

  tray.setToolTip("OpenVault");
  tray.setContextMenu(contextMenu);

  tray.on("double-click", () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

function setupIpcHandlers() {
  ipcMain.handle("openvault:getAppInfo", () => ({
    name: app.getName(),
    version: app.getVersion(),
    platform: process.platform,
    isDev,
    apiPort: API_PORT,
    webPort: WEB_PORT,
    repoRoot: REPO_ROOT,
  }));

  ipcMain.handle("openvault:pickFolder", async () => {
    const result = await dialog.showOpenDialog(mainWindow ?? undefined, {
      properties: ["openDirectory"],
    });
    if (result.canceled || !result.filePaths.length) {
      return { ok: false, path: "", error: "cancelled" };
    }
    return { ok: true, path: result.filePaths[0], error: null };
  });

  ipcMain.handle("openvault:openExternal", (_event, url) => {
    try {
      const parsedUrl = new URL(url);
      if (["http:", "https:"].includes(parsedUrl.protocol)) {
        shell.openExternal(url);
      }
    } catch {
      console.error("[OpenVault] Blocked invalid URL:", url);
    }
  });

  ipcMain.handle("openvault:getClipboardWatch", () => ({
    enabled: clipboardWatchEnabled,
  }));

  ipcMain.handle("openvault:setClipboardWatch", (_event, enabled) => ({
    enabled: setClipboardWatchEnabled(Boolean(enabled)),
  }));
}

app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  }
});

app.whenReady().then(async () => {
  setupContentSecurityPolicy();
  setupIpcHandlers();

  startApiServer();
  startWebServer();

  const apiReady = await waitForServer(API_HEALTH_URL);
  if (apiReady) {
    sendToRenderer("server-status", { status: "running", service: "api", port: API_PORT });
  } else {
    // Do not open a window that silently cannot work. Every panel proxies
    // /ov-api/* to the custody API, so without it the app paints fine and then
    // fails on every action - which reads as "the backend is flaky today"
    // rather than "the backend was never started" (R-0011).
    const code = lastProcessExit.api;
    dialog.showErrorBox(
      "OpenVault custody API did not start",
      [
        `Nothing is answering ${API_HEALTH_URL}.`,
        code ? `The API process exited with code ${code}.` : "The API process never became ready.",
        "",
        "Every panel in this app talks to that API, so it would open unable to do anything.",
        "Start it by hand to see the real error:",
        `  cd ${OPENMW_DIR}`,
        `  uv run --no-sync openmw console --host ${API_HOST} --port ${API_PORT}`,
      ].join("\n")
    );
  }

  await waitForServer(WEB_URL, 120000);
  sendToRenderer("server-status", { status: "running", service: "web", port: WEB_PORT });

  createWindow();
  createTray();
  // Clipboard watch stays OFF until Settings enables it (CLIPDROP_CONTRACT §3 / §7).
});

app.on("will-quit", () => {
  stopClipboardWatch();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", async (event) => {
  if ((apiServer || webServer) && !serversStopped) {
    event.preventDefault();
    app.isQuitting = true;

    const apiToStop = apiServer;
    const webToStop = webServer;
    stopServers();

    await Promise.all([waitForServerExit(apiToStop), waitForServerExit(webToStop)]);

    serversStopped = true;
    app.quit();
  } else {
    app.isQuitting = true;
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  } else if (mainWindow) {
    mainWindow.show();
  }
});

process.on("uncaughtException", (error) => {
  console.error("[OpenVault] Uncaught Exception:", error);
});

process.on("unhandledRejection", (reason) => {
  console.error("[OpenVault] Unhandled Rejection:", reason);
});
