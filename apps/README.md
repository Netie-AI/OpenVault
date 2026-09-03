# OpenVault apps — one app, built on FreeBuild's UI and OmniRoute's shell

## Layout

| Path | Role |
|------|------|
| `apps/web` | **The OpenVault app.** Next 16 + Tailwind v4, wearing FreeBuild's token system |
| `apps/shell` | **Electron desktop shell**, adapted from `vendor/OmniRoute/electron` |
| `apps/cli/openvault_cli.py` | CLI: `up` / `demo` / `app` / `doctor` |
| `vendor/*` | Upstream clones we copy **from**. Gitignored. Not services we run |

## Run

```powershell
python D:\OpenVault\apps\cli\openvault_cli.py demo
# or
powershell -ExecutionPolicy Bypass -File D:\OpenVault\scripts\windows\Start-OpenVaultDemo.ps1
```

That starts the custody API on `:5000` (mock health for a full demo) and the app
on `:3010`, installing web dependencies on first run. `openvault doctor` checks
the environment first. Everyday non-demo: `openvault up`.

## The toolchain constraint — read this before touching dependencies

**`D:` is exFAT.** exFAT supports no hardlinks, no symlinks and no junctions.
Bun and pnpm both need them for their content-addressed stores, so **both fail
here**. Use **npm only**, in every tree, always.

An earlier version of this file blamed OneDrive. That was wrong, and it led to
a second install being kept at `C:\Users\OoiJianHong\openvault-web` as a
workaround — a source-of-truth split. There is now **one tree**: this one.

Two further consequences of exFAT on this volume:

- Clusters are 1 MB, and ~99.7% of `node_modules` files are smaller than that,
  so each burns a whole cluster. Expect the on-disk footprint to be roughly an
  order of magnitude larger than the logical size. `.next` pays the same tax.
- npm's rename-then-remove retire path has failed here against a dirty tree.
  Before any reinstall: kill every `node` process, then
  `cmd /c rmdir /s /q node_modules` — **not** PowerShell's `Remove-Item`.

If npm ever becomes unworkable, the escape hatch is a fixed NTFS VHDX mounted
from `D:` (restores 4 KB clusters and links while the bytes stay on the same
spindle), or moving the repo to `C:`. Keeping a second install on `C:` is not
an option we return to.

## What we took, and what we did not

Decided in `docs/OPENSHIP_APP_PLAN.md`. Short version: we **fork** rather than
run either vendor stack.

**Taken:**
- FreeBuild's design system — `styles/theme.css` token set, `components/ui/*`,
  `components/shared/*`. It is provably clean: nothing under those paths
  imports `@repo/*`.
- FreeBuild's *detection intelligence* (stack table, root scoring) and its SSE
  frame contract for streaming build logs.
- OmniRoute's Electron shell and its Next middleware (`src/proxy.ts` + authz
  pipeline) — both taken essentially wholesale, Next→Next and JS→JS.
- OmniRoute's routing algorithms, ported to Python into the existing gateway.

**Not taken, deliberately:**
- FreeBuild's dashboard cannot run standalone — its layout redirects to
  `/login` without a better-auth session, and its build executor depends on a
  proprietary `oblien` package that is not in the repo. Running their stack
  would give us a Deploy button that cannot deploy.
- OmniRoute's LLM gateway as a *second process*. It carries its own credential
  store, which would directly contradict OpenVault being the one place keys
  live. We ported the algorithms into `vault/` instead.
- `vendor/OmniRoute/docker` — it contains only a VNC+Chromium sidecar.

There are no iframes. `:20128` and `:3001` are gone from the topology.

## Legacy

The old `OpenMW/webui/index.html` console is **removed**. Humans use
`http://127.0.0.1:3010/`. `:5000/` redirects there.
