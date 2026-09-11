---
keywords: :3010, Turbopack, exFAT, compiling-proxy, next start, webpack, #55
main_idea: Next 16 Turbopack panics on D: exFAT (os error 5) and leaves :3010 bound without HTML. Buyer path is webpack-dev or next start after build. Ready-check must wait for text/html, not TCP.
---

# :3010 compiling-proxy hang

PREFLIGHT: MISS at intake. Live log `web.up.log` showed repeated Turbopack FATAL:
`Access is denied (os error 5)` reading `apps/web/src/app/detect`, plus
`Failed to write app endpoint /_not-found/page`, then compaction failed.
Port stayed LISTENING (node start-server.js) while document GETs timed out.

Fix:
- `apps/web/package.json` `dev` is `next dev --webpack`
- `openvault up` / Electron prefer `next start` when `.next/BUILD_ID` exists
- `_html_ready` / `waitForHtml` require 200 `text/html` with `<html`, reject Compiling overlay
- Do not reuse a bound :3010 that is not serving HTML
- `src/serve.ts` KEY_UI_PORT default 3019 so the key-ui proof server cannot steal 3010
