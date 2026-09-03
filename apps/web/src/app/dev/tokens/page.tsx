import type { CSSProperties } from "react";

/*
 * Lane A exit gate.
 *
 * Renders every --th-* and every --st-* name defined in styles/theme.css,
 * every shadcn bridge var, and a probe for each custom variant, then reads
 * each one back with getComputedStyle. A token that resolves to nothing is
 * outlined in red and counted in the banner — the whole point is that a
 * broken token is obvious in seconds here instead of two weeks later in a
 * component nobody thought to check.
 *
 * Token names are written out literally, never composed from templates, so
 * `grep -c -- "--th-" page.tsx` against theme.css is a valid audit.
 *
 * Plain server component. The read-back and the theme buttons run from one
 * inline script at the bottom; that script is attached to the server-rendered
 * HTML, so a client-side navigation INTO this route will not run it — open
 * /dev/tokens directly (or hard-reload) when auditing.
 */

export const metadata = {
  title: "Design tokens · OpenVault",
};

type Kind = "color" | "text" | "shadow" | "raw";

type Group = {
  title: string;
  note?: string;
  kind: Kind;
  tokens: string[];
};

/* ── --th-* : the 65 names defined in styles/theme.css ─────────────── */

const THEME_GROUPS: Group[] = [
  {
    title: "Foreground scale — --th-on-*",
    note: "Foreground at N% opacity. Black-based in light, white-based in dark/dim/glass/ink.",
    kind: "color",
    tokens: [
      "--th-on-05",
      "--th-on-08",
      "--th-on-10",
      "--th-on-12",
      "--th-on-16",
      "--th-on-20",
      "--th-on-30",
      "--th-on-40",
      "--th-on-50",
      "--th-on-60",
      "--th-on-70",
      "--th-on-80",
      "--th-on-90",
      "--th-on-95",
    ],
  },
  {
    title: "Surface fill scale — --th-sf-*",
    note: "Tinted background washes. `ink` makes these opaque grays — a .02 wash over #000 is invisible.",
    kind: "color",
    tokens: [
      "--th-sf-01",
      "--th-sf-02",
      "--th-sf-03",
      "--th-sf-04",
      "--th-sf-05",
      "--th-sf-06",
      "--th-sf-08",
    ],
  },
  {
    title: "Text",
    note: "Rendered as type, not as fill — a text token that fails is a legibility bug, not a colour bug.",
    kind: "text",
    tokens: [
      "--th-text-heading",
      "--th-text-title",
      "--th-text-strong",
      "--th-text-body",
      "--th-text-secondary",
      "--th-text-muted",
      "--th-text-label",
      "--th-text-hint",
      "--th-text-ghost",
    ],
  },
  {
    title: "Page / layout",
    kind: "color",
    tokens: [
      "--th-bg-page",
      "--th-bg-card",
      "--th-bg-subtle",
      "--th-bg-hover",
      "--th-bg-inset",
    ],
  },
  {
    title: "Borders",
    kind: "color",
    tokens: [
      "--th-bd-default",
      "--th-bd-subtle",
      "--th-bd-strong",
      "--th-bd-card",
    ],
  },
  {
    title: "Input",
    kind: "color",
    tokens: [
      "--th-input-bg",
      "--th-input-bd",
      "--th-input-text",
      "--th-input-placeholder",
      "--th-input-bg-focus",
      "--th-input-bd-focus",
    ],
  },
  {
    title: "Button — primary",
    kind: "color",
    tokens: ["--th-btn-bg", "--th-btn-text", "--th-btn-bg-hover"],
  },
  {
    title: "Button — ghost / outline",
    kind: "color",
    tokens: [
      "--th-btn-ghost-bd",
      "--th-btn-ghost-text",
      "--th-btn-ghost-bd-hover",
      "--th-btn-ghost-text-hover",
      "--th-btn-ghost-bg-hover",
    ],
  },
  {
    title: "Card",
    note: "`-bg-solid` is what Modal.tsx feeds through color-mix. In `glass` it is translucent on purpose.",
    kind: "color",
    tokens: [
      "--th-card-bg",
      "--th-card-bg-solid",
      "--th-card-bd",
      "--th-card-bd-hover",
    ],
  },
  {
    title: "Divider / Overlay",
    kind: "color",
    tokens: ["--th-divider", "--th-overlay"],
  },
  {
    title: "Dropdown",
    kind: "color",
    tokens: ["--th-dropdown-bg", "--th-dropdown-bd"],
  },
  {
    title: "Shadows",
    note: "Chips carry the shadow itself. `none` is a legitimate value in light and ink — not a failure.",
    kind: "shadow",
    tokens: ["--th-card-shadow", "--th-dropdown-shadow"],
  },
  {
    title: "Spinner",
    kind: "color",
    tokens: ["--th-spinner-track", "--th-spinner-fill"],
  },
];

/* ── --st-* : the 15 status names ──────────────────────────────────── */

const STATUS_GROUP: Group = {
  title: "Status — --st-*",
  note: "fg / bg / bd per status. These are the only non-grayscale tokens in the system.",
  kind: "color",
  tokens: [
    "--st-success-fg",
    "--st-success-bg",
    "--st-success-bd",
    "--st-danger-fg",
    "--st-danger-bg",
    "--st-danger-bd",
    "--st-warning-fg",
    "--st-warning-bg",
    "--st-warning-bd",
    "--st-info-fg",
    "--st-info-bg",
    "--st-info-bd",
    "--st-neutral-fg",
    "--st-neutral-bg",
    "--st-neutral-bd",
  ],
};

/* ── shadcn bridge : what Tailwind utilities actually read ─────────── */

const BRIDGE_GROUPS: Group[] = [
  {
    title: "shadcn bridge — surfaces",
    note: "These back bg-card, text-foreground, border-border… An empty one here breaks utilities, not .th-* classes.",
    kind: "color",
    tokens: [
      "--background",
      "--foreground",
      "--card",
      "--card-foreground",
      "--popover",
      "--popover-foreground",
      "--primary",
      "--primary-foreground",
      "--secondary",
      "--secondary-foreground",
      "--muted",
      "--muted-foreground",
      "--accent",
      "--accent-foreground",
      "--destructive",
      "--destructive-foreground",
      "--border",
      "--input",
      "--ring",
    ],
  },
  {
    title: "shadcn bridge — status aliases",
    kind: "color",
    tokens: [
      "--success",
      "--success-bg",
      "--success-border",
      "--danger",
      "--danger-bg",
      "--danger-border",
      "--warning",
      "--warning-bg",
      "--warning-border",
      "--info",
      "--info-bg",
      "--info-border",
      "--neutral",
      "--neutral-bg",
      "--neutral-border",
    ],
  },
  {
    title: "shadcn bridge — vivid solid fills",
    note: "Theme-independent by design: the -fg tones are text-tuned and too dark for a large fill.",
    kind: "color",
    tokens: [
      "--success-solid",
      "--danger-solid",
      "--warning-solid",
      "--info-solid",
      "--neutral-solid",
    ],
  },
  {
    title: "Geometry & layout",
    note: "--ov-topbar-h is the single source of the app's top offset. Only PageContainer may consume it.",
    kind: "raw",
    tokens: ["--radius", "--ov-topbar-h", "--th-backdrop"],
  },
];

const ALL_GROUPS: Group[] = [...THEME_GROUPS, STATUS_GROUP, ...BRIDGE_GROUPS];

const THEMES = ["light", "dim", "dark", "glass", "ink"] as const;

function Swatch({ name, kind }: { name: string; kind: Kind }) {
  const style = { "--tok": `var(${name})` } as CSSProperties;
  return (
    <div className="tok" data-token={name}>
      <div className={`tok-chip tok-chip--${kind}`} style={style}>
        {kind === "text" ? <span>Aa</span> : null}
        {kind === "raw" ? <span>◧</span> : null}
      </div>
      <code className="tok-name">{name}</code>
      <code className="tok-val" data-val>
        …
      </code>
    </div>
  );
}

function GroupBlock({ group }: { group: Group }) {
  return (
    <section className="th-card p-5 mb-4">
      <h2 className="th-text-title text-sm font-semibold tracking-tight">
        {group.title}
        <span className="th-text-ghost font-normal ms-2">{group.tokens.length}</span>
      </h2>
      {group.note ? (
        <p className="th-text-hint text-xs mt-1 mb-4 max-w-3xl leading-relaxed">{group.note}</p>
      ) : (
        <div className="mb-4" />
      )}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        {group.tokens.map((t) => (
          <Swatch key={t} name={t} kind={group.kind} />
        ))}
      </div>
    </section>
  );
}

export default function TokensPage() {
  return (
    /* pt-topbar comes from --ov-topbar-h. This page renders outside
       PageContainer during Stage 1; once routes are wrapped, drop it. */
    <div className="th-page pt-topbar">
      <style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />

      <div className="mx-auto max-w-[1600px] px-6 lg:px-8 py-8">
        <header className="mb-6">
          <h1 className="th-text-heading text-2xl font-semibold tracking-tight">
            Design tokens
          </h1>
          <p className="th-text-body text-sm mt-1 max-w-3xl leading-relaxed">
            Every token in <code className="tok-inline">src/styles/theme.css</code>, plus the{" "}
            <code className="tok-inline">glass</code> and <code className="tok-inline">ink</code>{" "}
            skins, read back with <code className="tok-inline">getComputedStyle</code>. Switch
            themes below; anything that fails to resolve is outlined in red.
          </p>
        </header>

        <div className="th-card p-4 mb-6 flex flex-wrap items-center gap-3">
          <span className="th-text-label text-xs uppercase tracking-wider">Theme</span>
          {THEMES.map((t) => (
            <button
              key={t}
              type="button"
              data-set-theme={t}
              className="theme-btn th-btn-ghost text-sm px-3 py-1.5 rounded-lg"
            >
              {t}
            </button>
          ))}
          <button
            type="button"
            data-set-theme=""
            className="theme-btn th-btn-ghost text-sm px-3 py-1.5 rounded-lg"
            title="Remove data-theme entirely — falls through to the :root block"
          >
            unset
          </button>

          <div className="ms-auto flex items-center gap-4 text-xs">
            <span className="th-text-hint">
              active: <b id="tok-theme" className="th-text-strong">…</b>
            </span>
            <span id="tok-status" className="tok-status">
              <b id="tok-count">…</b> tokens · <b id="tok-empty">…</b> empty
            </span>
          </div>
        </div>

        {ALL_GROUPS.map((g) => (
          <GroupBlock key={g.title} group={g} />
        ))}

        {/* ── Variant probes ──────────────────────────────────────────
            These are the failure the plan calls the #1 silent breakage:
            a dark-based skin missing from the @custom-variant selector
            compiles every dark: utility to nothing. Each box states the
            themes in which it must change, so a miss is readable. */}
        <section className="th-card p-5 mb-4">
          <h2 className="th-text-title text-sm font-semibold tracking-tight">
            Custom-variant probes
          </h2>
          <p className="th-text-hint text-xs mt-1 mb-4 max-w-3xl leading-relaxed">
            Each box is neutral by default and takes a status colour only under its own variant. If
            a box stays neutral in a theme it names, that variant selector in{" "}
            <code className="tok-inline">globals.css</code> is missing that theme.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="probe border bg-neutral-bg text-neutral border-neutral-border dark:bg-success-bg dark:text-success dark:border-success-border">
              <b>dark:</b>
              <span>green in dark · glass · ink</span>
            </div>
            <div className="probe border bg-neutral-bg text-neutral border-neutral-border dim:bg-warning-bg dim:text-warning dim:border-warning-border">
              <b>dim:</b>
              <span>amber in dim only</span>
            </div>
            <div className="probe border bg-neutral-bg text-neutral border-neutral-border glass:bg-info-bg glass:text-info glass:border-info-border">
              <b>glass:</b>
              <span>blue in glass only</span>
            </div>
            <div className="probe border bg-neutral-bg text-neutral border-neutral-border ink:bg-danger-bg ink:text-danger ink:border-danger-border">
              <b>ink:</b>
              <span>red in ink only</span>
            </div>
          </div>
        </section>

        {/* ── Primitive surfaces ────────────────────────────────────── */}
        <section className="th-card p-5 mb-4">
          <h2 className="th-text-title text-sm font-semibold tracking-tight">
            Surfaces in situ
          </h2>
          <p className="th-text-hint text-xs mt-1 mb-4 max-w-3xl leading-relaxed">
            The same tokens as components render them. In <code className="tok-inline">glass</code>{" "}
            the card and dropdown below pick up{" "}
            <code className="tok-inline">backdrop-filter</code> from glass.css.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="th-card p-4">
              <div className="th-text-title text-sm font-medium">.th-card</div>
              <div className="th-text-muted text-xs mt-1">card-bg · card-bd · card-shadow</div>
              <button type="button" className="th-btn text-sm px-3 py-1.5 rounded-lg mt-3">
                .th-btn
              </button>
            </div>
            <div className="th-dropdown p-4 rounded-xl">
              <div className="th-text-title text-sm font-medium">.th-dropdown</div>
              <div className="th-text-muted text-xs mt-1">dropdown-bg · bd · shadow</div>
            </div>
            <div className="rounded-xl border border-border bg-card text-card-foreground p-4">
              <div className="text-sm font-medium">bg-card / border-border</div>
              <div className="text-muted-foreground text-xs mt-1">
                Tailwind utilities via the bridge
              </div>
              <input
                className="th-input mt-3 w-full text-sm px-3 py-1.5 rounded-lg"
                placeholder=".th-input placeholder"
                aria-label="input token sample"
              />
            </div>
          </div>
        </section>

        {/* ── Build-integrity probes ────────────────────────────────── */}
        <section className="th-card p-5 mb-10">
          <h2 className="th-text-title text-sm font-semibold tracking-tight">
            Build integrity
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
            <div>
              <div
                id="tok-anim-probe"
                className="probe border border-neutral-border animate-in fade-in slide-in-from-bottom-2 duration-700"
              >
                <b>tw-animate-css</b>
                <span id="tok-anim" className="th-text-hint">
                  …
                </span>
              </div>
              <p className="th-text-hint text-xs mt-2 leading-relaxed">
                Reads <code className="tok-inline">animation-name</code> off the box. Vendor
                globals.css omits the import, which turns every{" "}
                <code className="tok-inline">animate-in</code> in the primitives into a no-op.
              </p>
            </div>
            <div>
              <div className="probe border border-neutral-border">
                <b>@theme inline</b>
                <span className="th-text-hint">
                  If a swatch stops changing on theme switch, someone dropped{" "}
                  <code className="tok-inline">inline</code>.
                </span>
              </div>
              <p className="th-text-hint text-xs mt-2 leading-relaxed">
                Plain <code className="tok-inline">@theme</code> snapshots values at build time;
                only <code className="tok-inline">@theme inline</code> keeps them live{" "}
                <code className="tok-inline">var()</code> refs.
              </p>
            </div>
          </div>
        </section>
      </div>

      <script dangerouslySetInnerHTML={{ __html: PAGE_JS }} />
    </div>
  );
}

const PAGE_CSS = `
.tok { display: flex; flex-direction: column; gap: 6px; padding: 6px; border-radius: 12px; }
.tok-chip {
  position: relative;
  height: 44px;
  border-radius: 10px;
  border: 1px solid var(--th-bd-default);
  overflow: hidden;
}
/* Checkerboard so the alpha in an rgba token is visible instead of guessed. */
.tok-chip--color {
  background-image:
    linear-gradient(45deg, rgba(128,128,128,.30) 25%, transparent 25%),
    linear-gradient(-45deg, rgba(128,128,128,.30) 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, rgba(128,128,128,.30) 75%),
    linear-gradient(-45deg, transparent 75%, rgba(128,128,128,.30) 75%);
  background-size: 12px 12px;
  background-position: 0 0, 0 6px, 6px -6px, -6px 0;
}
.tok-chip--color::after { content: ""; position: absolute; inset: 0; background: var(--tok); }
.tok-chip--text { display: grid; place-items: center; background-color: var(--th-bg-card); }
.tok-chip--text span { color: var(--tok); font-size: 18px; font-weight: 700; letter-spacing: -.02em; }
.tok-chip--shadow { background-color: var(--th-bg-card); box-shadow: var(--tok); }
.tok-chip--raw { display: grid; place-items: center; background-color: var(--th-bg-card); }
.tok-chip--raw span { color: var(--th-text-ghost); font-size: 16px; }
.tok-name { font-size: 11px; line-height: 1.3; color: var(--th-text-secondary); word-break: break-all; }
.tok-val { font-size: 10px; line-height: 1.3; color: var(--th-text-hint); word-break: break-all; }
.tok-inline { font-size: .92em; color: var(--th-text-strong); }

/* The alarm. Loud on purpose — this is the entire reason the page exists. */
.tok[data-empty] { outline: 2px solid #ff3b30; outline-offset: 0; background-color: rgba(255,59,48,.10); }
.tok[data-empty] .tok-val { color: #ff3b30; font-weight: 700; }
.tok-status { color: var(--th-text-hint); }
.tok-status[data-bad="1"] { color: #ff3b30; font-weight: 700; }

.theme-btn[data-active] {
  border-color: var(--th-btn-ghost-bd-hover);
  color: var(--th-btn-ghost-text-hover);
  background-color: var(--th-btn-ghost-bg-hover);
}

/* Deliberately sets NO border here. This <style> is unlayered, and unlayered
   rules beat Tailwind's @layer utilities no matter the specificity — a border
   declared here would override every dark:/dim:/glass:/ink: border-* utility
   on the probes below and silently kill half of each probe's signal. The
   probes carry "border border-neutral-border" as utilities instead. */
.probe {
  display: flex; flex-direction: column; gap: 4px;
  padding: 12px 14px; border-radius: 12px;
  font-size: 12px;
}
.probe b { font-size: 13px; }
#tok-anim-probe[data-ok="0"] { outline: 2px solid #ff3b30; }
#tok-anim-probe[data-ok="0"] #tok-anim { color: #ff3b30; font-weight: 700; }
`;

const PAGE_JS = `
(function () {
  var root = document.documentElement;

  function refresh() {
    var cs = getComputedStyle(root);
    var nodes = document.querySelectorAll('[data-token]');
    var empty = 0;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var v = (cs.getPropertyValue(n.getAttribute('data-token')) || '').trim();
      var out = n.querySelector('[data-val]');
      if (v) {
        n.removeAttribute('data-empty');
        if (out) out.textContent = v;
      } else {
        empty++;
        n.setAttribute('data-empty', '');
        if (out) out.textContent = 'EMPTY - does not resolve';
      }
    }

    var active = root.getAttribute('data-theme') || '';
    document.getElementById('tok-theme').textContent = active || '(unset -> :root)';
    document.getElementById('tok-count').textContent = String(nodes.length);
    document.getElementById('tok-empty').textContent = String(empty);
    document.getElementById('tok-status').setAttribute('data-bad', empty ? '1' : '0');

    var btns = document.querySelectorAll('[data-set-theme]');
    for (var j = 0; j < btns.length; j++) {
      if (btns[j].getAttribute('data-set-theme') === active) btns[j].setAttribute('data-active', '');
      else btns[j].removeAttribute('data-active');
    }

    // tw-animate-css sets animation-name: enter on .animate-in. 'none' means
    // the import is missing and every animate-* class is silently dead.
    var probe = document.getElementById('tok-anim-probe');
    var out2 = document.getElementById('tok-anim');
    if (probe && out2) {
      var an = getComputedStyle(probe).animationName;
      var ok = !!an && an !== 'none';
      out2.textContent = 'animation-name: ' + an + (ok ? '  OK - import active' : '  BROKEN - import missing');
      probe.setAttribute('data-ok', ok ? '1' : '0');
    }
  }

  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    var b = t.closest('[data-set-theme]');
    if (!b) return;
    var name = b.getAttribute('data-set-theme');
    if (name) root.setAttribute('data-theme', name);
    else root.removeAttribute('data-theme');
    refresh();
  });

  refresh();
  // A cold load can read blank before the stylesheet has applied.
  window.addEventListener('load', refresh);
})();
`;
