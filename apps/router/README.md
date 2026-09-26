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
npm install
npm run build
npm start
```

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
