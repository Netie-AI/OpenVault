# Third-party notices

OpenVault includes two forked applications. Their upstream licenses, copyright
notices and notice files are kept verbatim inside each app. This file records
where each fork came from, what was left out of the import, and the notices that
apply. Decision record: [`docs/decisions/DR-0018-fork-router-and-ship.md`](docs/decisions/DR-0018-fork-router-and-ship.md).

Changes made by Netie AI are Copyright (c) 2026 Netie AI and are released under
the same license as the file they change. Upstream copyright is never replaced.

---

## FreeRoute (`apps/router`)

| | |
|---|---|
| Upstream | OmniRoute, https://github.com/diegosouzapw/OmniRoute |
| Commit imported | `ae2ba35852d4e5a55486a1c0e6a779105564fd6d` (committed 2026-09-25) |
| License | MIT, Copyright (c) 2026 diegosouzapw. Kept at `apps/router/LICENSE` |
| Upstream notices | `apps/router/THIRD_PARTY_NOTICES.md`, kept verbatim |
| History | Imported as one squashed snapshot (commit `import(router): ...` on this repo). Upstream history is not carried; see the upstream repo at the commit above. |

OmniRoute's README states it started as a fork of 9router and as a TypeScript
port of CLIProxyAPI. Both are MIT; their notices follow, fetched from each
repo's `LICENSE` on 2026-09-26 (9router `f01fb909e37189008080632ddaf404f096345cde`,
CLIProxyAPI `ed980be34b9981735eaa16941956b1e8d9abfb7c`).

Left out of the import (not shipped here): `docs/`, `images/`, `tests/`,
`electron/`, `docker/`, `@omniroute/` plugins, `examples/`, `contrib/`,
`changelog.d/`, `skills/`, non-English UI message files, upstream agent
instruction files (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`), CI, deploy and
release manifests (`.github/`, Dockerfiles, compose files, `fly.toml`, Nix
flake), `scripts/i18n/`, `scripts/sre/`, `CHANGELOG.md`, `ROADMAP.md`.

### 9router

```
MIT License

Copyright (c) 2024-2026 decolua and contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### CLIProxyAPI

```
MIT License

Copyright (c) 2025-2005.9 Luis Pater
Copyright (c) 2025.9-present Router-For.ME

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.```

---

## FreeBuild (`apps/ship`)

| | |
|---|---|
| Upstream | Openship, https://github.com/oblien/openship |
| Commit imported | `aba12c8dbee5cd92673645796ae68616a8fa30e1` (committed 2026-09-26) |
| License | Apache License 2.0. Kept at `apps/ship/LICENSE` |
| Change notices | Modified files carry `Modified by Netie AI, 2026`; files without a comment syntax are listed in `apps/ship/NOTICE` |
| History | Imported as one squashed snapshot (commit `import(ship): ...` on this repo). Upstream history is not carried; see the upstream repo at the commit above. |

Left out of the import (not shipped here): `apps/web`,
`apps/desktop`, `apps/cli`, `apps/edge`, `packages/db-email`,
`packages/openship`, `docs/`, `fixtures/`, `docker/`, `bun.lock`, upstream
agent files, and the MaxMind GeoLite2 country database
(`apps/api/assets/geoip/GeoLite2-Country.mmdb`, which has its own license and is
not redistributed here). `apps/email` is included, minus ten onboarding and
pricing images over 500 KB under `apps/email/client/public/`.

---

## Trademarks

OmniRoute, 9router, CLIProxyAPI, Openship and Oblien are names of their
respective owners. They appear here only as attribution. The shipped products
are named FreeRoute and FreeBuild and do not use the upstream names or logos.
