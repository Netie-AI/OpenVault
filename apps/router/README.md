# FreeRoute

FreeRoute is Netie's free AI gateway, part of OpenVault. It gives every tool
on your machine one OpenAI-compatible endpoint that routes to real AI
providers (OpenAI, Anthropic, Groq, OpenRouter, Google, Mistral, DeepSeek,
Together and more), using the API keys you already keep in OpenVault.

It does not pool consumer subscription accounts (Claude Code, Codex, Cursor,
GitHub Copilot, Antigravity, Kiro, and similar) and it does not relay a
browser chat session's cookies. Those features exist in the upstream project
this is built on; this edition turns them off. See "What is disabled and
why" below.

## Running FreeRoute under OpenVault

FreeRoute is one of OpenVault's services. OpenVault does not launch it yet
(see DR-0018 in the OpenVault repo). Start OpenVault first, then run:

```bash
npm ci
npm run build
npm start
```

`npm ci` installs exactly what `package-lock.json` pins. It works with the npm
that ships with Node 22 (npm 10) and with npm 11.

FreeRoute authenticates to OpenVault with the admin token OpenVault writes on
first start. It reads it from `$OPENVAULT_ADMIN_TOKEN_PATH`, else
`$OPENVAULT_HOME/admin_token`, else `~/.openvault/admin_token`. Point
`OPENVAULT_URL` at OpenVault if it is not on `http://127.0.0.1:5000`.

- Default port: `3020`. Override with the `PORT` environment variable.
- Default bind address: `127.0.0.1` (loopback only). Override with
  `OMNIROUTE_HOSTNAME` if you need FreeRoute reachable from another host -
  do this only on a trusted network.
- `npm run dev` runs it in development mode on the same port/bind rules.

## Where provider API keys live

FreeRoute never stores a provider's API key in its own database, files, or
environment. Every outgoing request resolves its key live from OpenVault's
KeyVault (`GET /api/keys`, `GET /api/keys/{id}/secret`), cached in memory for
at most 60 seconds. Add or change a provider key at OpenVault's key page,
not in FreeRoute's own dashboard:

**http://127.0.0.1:3010/keys**

Any request to save an API key into FreeRoute's own store returns
`HTTP 501 keys_managed_by_openvault` and points here instead. If OpenVault is
unreachable, FreeRoute fails loudly (`HTTP 503
openvault_keyvault_unreachable`) rather than falling back to a locally cached
key.

How a connection finds its key: if an enabled, active OpenVault key has the
same base URL as the connection (trailing slash and case ignored), that key
is used. Otherwise FreeRoute maps the connection's provider to an OpenVault
provider (`openai`, `anthropic`, `groq`, ...) and uses the highest-priority
key for it. So for an OpenAI-compatible endpoint, store the key in OpenVault
with that endpoint as its base URL, then add the connection in FreeRoute
with no key.

## Tests

| Command | Needs | What it checks |
|---|---|---|
| `npm test` | a build (`npm run build` or `npm run build:backend`) | policy unit tests, the KeyVault client against a stub, and a smoke run of the built server against a stub OpenVault |
| `npm run test:e2e` | a build, `uv` on PATH, and the OpenVault repo | the built server against a real OpenVault (see below) |

`npm run test:e2e` runs `netie/e2e-openvault.mjs`. It starts the real
OpenVault API from the OpenVault repo's `OpenMW/` directory with
`uv run openmw console` (default path `../../OpenMW` from this directory,
override with `OPENVAULT_OPENMW_DIR`), a fake OpenAI-compatible upstream, and
this build. It stores a provider key in OpenVault, routes a chat completion
through FreeRoute with an OpenVault-issued client token, and checks the
upstream got the vault key, OpenVault audited the reveal, the disabled
providers answer 501, a wrong admin token and a stopped OpenVault answer 503,
and the key never lands in FreeRoute's data directory or logs. Everything
runs on loopback in temp directories that are removed at the end.

## What is disabled, and why

OpenVault's job is to be the one place your API keys and provider
credentials live. A gateway that also pools other people's login sessions,
or your own subscription accounts, undermines that, and most upstream
providers' terms of service don't allow it either. FreeRoute hard-disables
three feature families from the upstream project, each with a named
`HTTP 501` response:

| Code | What it covers |
|---|---|
| `consumer_subscription_pooling_disabled` | Reusing a signed-in consumer subscription account (Claude Code, Codex/ChatGPT, Cursor, GitHub/GHE Copilot, Antigravity, Kiro, Kimi Coding, Trae, Devin, and similar) as a routable provider. |
| `consumer_session_relay_disabled` | Replaying a browser chat session's cookies or session token in place of an API key (chatgpt-web, claude-web, gemini-web, grok-web, deepseek-web, Microsoft 365 Copilot, duck.ai, HuggingChat, and similar), and the MITM/"Agent Bridge" subsystem that captures IDE traffic to reuse one. |
| `tls_fingerprint_stealth_disabled` | Impersonating a specific browser or CLI's TLS/HTTP fingerprint to evade bot detection. |

Every provider that takes a real, provider-issued API key keeps working.
`open-sse/netie/policy.ts` is the single place that classifies a provider
into one of these buckets (or leaves it enabled) from the provider registry's
own fields. See that file for exactly how, and for the full list of
affected providers.

## Credit

Based on [OmniRoute](https://github.com/diegosouzapw/OmniRoute) by
diegosouzapw (MIT), itself based on 9router (MIT). See
OpenVault's root `THIRD_PARTY_NOTICES.md` for the 9router license text, and
`/notices` in the running app.

## License

MIT, see `LICENSE`.
