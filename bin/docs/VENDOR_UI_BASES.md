# Vendor UI bases (steal / build-on)

- FreeBuild: `D:\OpenVault\vendor\openship` (https://github.com/oblien/openship) — Ship tab mirrors dashboard deploy config
- OmniRoute: `D:\OpenVault\vendor\OmniRoute` (https://github.com/diegosouzapw/OmniRoute) — Vault/Engine provider cards + theme

Run FreeBuild dashboard (needs Bun):
```
cd D:\OpenVault\vendor\openship
bun install
bun run dev
```

OpenVault console still serves `OpenMW/webui` with theme switcher: Glass | FreeBuild | OmniRoute.
