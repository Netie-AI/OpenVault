import { BYOK, CORTEX_KEY_LABEL, FREE, SUBSCRIBE } from "./copy.ts";
import { honestByokLabel } from "./byok.ts";

export type KeyPath = "subscribe" | "byok" | "free";

export interface SubscribeView {
  issuedToken?: string;
}

export interface ByokView {
  pastedSecret?: string;
  pastedProviderName?: string;
}

function esc(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function renderSubscribe(view: SubscribeView = {}): string {
  const issued = view.issuedToken
    ? `<div class="issued" data-testid="subscribe-issued">
        <p class="issued-label">${esc(SUBSCRIBE.issuedHeading)}</p>
        <code class="issued-token">${esc(view.issuedToken)}</code>
        <p class="hint">${esc(SUBSCRIBE.issuedHint)}</p>
      </div>`
    : `<p class="hint">${esc(SUBSCRIBE.emptyHint)}</p>`;

  return `<section id="subscribe-screen" data-testid="subscribe-screen" data-path="subscribe">
  <h1>${esc(SUBSCRIBE.title)}</h1>
  <p class="lead">${esc(SUBSCRIBE.lead)}</p>
  ${issued}
  <button type="button" data-action="issue-cortex">${esc(SUBSCRIBE.button)}</button>
  <p class="disclosure" data-testid="subscribe-disclosure">${esc(SUBSCRIBE.disclosure)}</p>
</section>`;
}

export function renderByok(view: ByokView = {}): string {
  const secret = view.pastedSecret ?? "";
  const label = honestByokLabel(view.pastedProviderName ?? "", secret);
  const labelBlock = secret
    ? `<p class="byok-label" data-testid="byok-honest-label">Stored as <strong>${esc(label || BYOK.unknownProvider)}</strong></p>`
    : "";

  return `<section id="byok-screen" data-testid="byok-screen" data-path="byok">
  <h1>${esc(BYOK.title)}</h1>
  <p class="lead">${esc(BYOK.lead)}</p>
  <label>${esc(BYOK.pasteLabel)}
    <input type="password" name="byok-secret" autocomplete="off" value="${esc(secret)}" />
  </label>
  ${labelBlock}
  <button type="button" data-action="store-byok">${esc(BYOK.storeButton)}</button>
</section>`;
}

export function renderFree(): string {
  const steps = FREE.steps
    .map(
      (step) => `<li class="step" data-testid="free-step-${step.n}">
      <span class="n">${step.n}</span>
      <div>
        <h2>${esc(step.title)}</h2>
        <p>${esc(step.body)}</p>
      </div>
    </li>`,
    )
    .join("");

  return `<section id="free-screen" data-testid="free-screen" data-path="free">
  <h1>${esc(FREE.title)}</h1>
  <p class="lead">${esc(FREE.lead)}</p>
  <ol class="steps">${steps}</ol>
  <p class="hint">${esc(FREE.cortexHint)}</p>
</section>`;
}

export function renderPath(path: KeyPath, view: SubscribeView & ByokView = {}): string {
  if (path === "subscribe") return renderSubscribe(view);
  if (path === "byok") return renderByok(view);
  return renderFree();
}

export function renderPage(path: KeyPath, view: SubscribeView & ByokView = {}): string {
  const body = renderPath(path, view);
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OpenVault — ${esc(path === "subscribe" ? SUBSCRIBE.title : path === "byok" ? BYOK.title : FREE.title)}</title>
  <style>
    :root { color-scheme: dark; --ink:#eef4f0; --muted:#9aa8a0; --bg:#101612; --card:#1a221c; --line:#2a362e; --go:#1f8a5b; }
    body { margin:0; font:16px/1.45 system-ui,sans-serif; background:var(--bg); color:var(--ink); }
    main { max-width:40rem; margin:0 auto; padding:28px 18px 64px; }
    nav { display:flex; gap:8px; margin-bottom:18px; }
    nav a { color:var(--ink); text-decoration:none; border:1px solid var(--line); padding:8px 12px; border-radius:999px; }
    nav a[aria-current="page"] { background:var(--ink); color:var(--bg); }
    section { background:var(--card); border:1px solid var(--line); border-radius:18px; padding:22px; }
    h1 { font-size:1.6rem; margin:0 0 8px; }
    h2 { font-size:1.05rem; margin:0 0 4px; }
    .lead,.hint,.disclosure { color:var(--muted); }
    .disclosure { margin-top:18px; font-size:0.92rem; }
    button { background:var(--go); color:#fff; border:0; border-radius:999px; padding:10px 14px; font-weight:600; cursor:pointer; }
    input { width:100%; margin-top:6px; padding:10px; border-radius:10px; border:1px solid var(--line); background:#0e1410; color:var(--ink); }
    .issued { margin:16px 0; padding:12px; border:1px dashed var(--line); border-radius:12px; }
    .issued-token { display:block; margin-top:6px; word-break:break-all; }
    .steps { list-style:none; padding:0; margin:16px 0; }
    .step { display:flex; gap:12px; margin:0 0 14px; }
    .n { width:28px; height:28px; border-radius:50%; background:var(--go); color:#fff; display:grid; place-items:center; font-weight:700; }
  </style>
</head>
<body>
  <main>
    <nav>
      <a href="/subscribe" ${path === "subscribe" ? 'aria-current="page"' : ""}>Subscribe</a>
      <a href="/byok" ${path === "byok" ? 'aria-current="page"' : ""}>Bring your key</a>
      <a href="/free" ${path === "free" ? 'aria-current="page"' : ""}>Free keys</a>
    </nav>
    ${body}
  </main>
</body>
</html>`;
}

export { CORTEX_KEY_LABEL };
