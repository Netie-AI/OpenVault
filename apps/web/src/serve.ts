/**
 * Loopback key-UI proof server. Binds 127.0.0.1 only. Never :5000.
 */
import http from "node:http";
import type { KeyPath } from "./keys/render.ts";
import { renderPage } from "./keys/render.ts";

const HOST = "127.0.0.1";
const PORT = Number(process.env.KEY_UI_PORT || 3010);

function pathOf(url: string): KeyPath {
  if (url.startsWith("/byok")) return "byok";
  if (url.startsWith("/free")) return "free";
  return "subscribe";
}

const server = http.createServer((req, res) => {
  const url = req.url || "/";
  if (url === "/healthz") {
    res.writeHead(200, { "content-type": "text/plain" });
    res.end("ok\n");
    return;
  }
  const html = renderPage(pathOf(url));
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  res.end(html);
});

server.listen(PORT, HOST, () => {
  process.stdout.write(`key-ui loopback http://${HOST}:${PORT}/subscribe\n`);
});
