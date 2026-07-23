# OpenVault provider catalog — OmniRoute / OpenRouter / Ollama absorb

## What we absorbed (patterns, not a full fork)

| Source | Absorbed into OpenVault |
|--------|-------------------------|
| **OmniRoute** | 4-tier fallback, free-tier surface, register links, downtime chips, circuit breaker |
| **OpenRouter** | Marketplace-style freemium entry + single-key multi-model hop |
| **LiteLLM** | Self-hosted OpenAI-compatible proxy slot |
| **Ollama** | Local free-forever OpenAI-compatible `/v1` |
| **Groq / Cerebras / Google / Mistral / DeepSeek…** | Free/freemium hops with register URLs |

**Not cloned:** OmniRoute’s 250-provider matrix, compression engines, quota-share DRR, Bifrost embed.

## APIs

| Route | Purpose |
|-------|---------|
| `GET /api/providers/catalog` | Full curated catalog |
| `GET /api/providers/free` | Free / freemium / local + register links |
| `GET /api/providers/coverage` | What Cortex / AirGPT / OpenVault still need |
| `POST /api/providers/{id}/downtime-check` | Uptime probe |
| `POST /api/providers/check-all-free` | Batch free/local uptime |
| `POST /api/vault/seed-essentials` | Seed local placeholders + list cloud register links |

## Cortex / AirGPT essentials

`POST /api/vault/seed-essentials` with `consumers: ["cortex","airgpt","openvault"]`:

1. Creates local placeholders for `ollama`, `cortex`, `litellm` (safe defaults).
2. Returns `pending_register[]` with **register_url** for every missing cloud key (OpenAI, Anthropic, OpenRouter, Groq, …).
3. After you paste real secrets via Vault UI / `POST /api/keys`, run **Precheck all**.

## UI

Vault tab → **Seed Cortex/AirGPT** · **Check free APIs** · register links · uptime buttons · coverage gaps.
