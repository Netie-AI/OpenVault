# FreeBuild

FreeBuild is Netie's self-hosted deploy and host engine. Point it at a git
repository and it builds, deploys, routes, and TLS-terminates the app —
including the domain routing, container runtime, backups, and a dashboard to
run all of it from.

FreeBuild runs under [OpenVault](../../README.md), Netie's local control
plane. It does not run standalone against Netie's own infrastructure, and it
does not connect to any hosted service run by this project's upstream.

## What this edition is

This is the **self-hosted-only** edition. Concretely:

- There is no "connect this instance to the cloud" option, no cloud-only
  billing, and no cloud-managed GitHub App. Every one of those routes answers
  HTTP 501 with a machine-readable `hosted_cloud_disabled` code instead of
  silently failing or phoning the upstream vendor.
- Provider API keys (Cloudflare, and any future single-secret provider this
  catalog gains) are never stored in FreeBuild's own database. They live in
  OpenVault's KeyVault; FreeBuild reads them from there at the moment it needs
  them, caches the plaintext in memory for at most 60 seconds, and never
  writes it to disk or a log. A route that would otherwise save one of those
  keys into FreeBuild's own store answers HTTP 501 with
  `keys_managed_by_openvault` and points the operator at
  `http://127.0.0.1:3010/keys`.
- Telemetry that would phone the upstream project's vendor is off.

What is **not** disabled: everything else — building and deploying your own
apps, domains and TLS, backups, the server terminal, Docker migration, the
one-click app catalog, and so on. Those run exactly as the upstream project
built them.

## Run it

FreeBuild is one workspace: an API (Hono, Node) and a dashboard (Next.js).

```bash
npm install
npm run build
npm start
```

The API listens on `127.0.0.1:3030` by default; the dashboard on
`127.0.0.1:3031`. Both bind to loopback only unless you set
`OPENSHIP_API_HOST` / the dashboard's `HOSTNAME` to something else. See
`.env.example` for the full list of environment variables — names kept from
upstream (`OPENSHIP_*`, `CLOUD_MODE`, ...) are documented there; `CLOUD_MODE`
and `DEPLOY_MODE=cloud` are accepted but always forced off in this edition.

Storage needs Postgres by default (`DATABASE_URL`); leave it unset for an
embedded PGlite database during development.

## Keys

Provider API keys are managed by OpenVault, not by FreeBuild. Add them at
OpenVault's KeyVault (`http://127.0.0.1:3010/keys` by default, or wherever
`OPENVAULT_URL` points) and FreeBuild picks them up automatically — nothing to
configure here. See `packages/core/src/netie/keyvault.ts` for the client and
the exact contract it expects from OpenVault.

Not every credential FreeBuild handles is a "provider API key" in that sense.
Deployment environment variables for *your own* deployed apps, your login
session, a registry username paired with its password, SSH host keys needed
on disk, and outbound SMTP relay credentials all stay exactly where upstream
put them — moving those into a keys vault meant for provider API tokens would
not be a clean fit. See the fork report for the file-by-file list.

## Based on Openship

FreeBuild is a fork of [Openship](https://github.com/oblien/openship) by
Oblien, licensed Apache-2.0. See `NOTICE` and `THIRD_PARTY_NOTICES.md` at the
repository root for the full upstream credit and the list of files Netie
modified.
