# PART 8 Research — R-6 SaaS Licensing for Local AI Middleware

## Patterns evaluated

| Pattern | Example | Pros | Cons for OpenMW |
|---------|---------|------|-----------------|
| **(a) Activation + heartbeat** | LM Studio Pro | Piracy resistance, usage analytics | Violates "no phone-home during inference" privacy pitch |
| **(b) Feature-gated tiers** | Ollama free + paid gateway | Simple UX, clear upsell | Needs online entitlement unless cached |
| **(c) Usage metering + credits** | OpenRouter | Aligns cost with AI compute | Poor fit — OpenMW runs on-device; no per-token cloud cost |
| **(d) Hardware fingerprint + offline license** | JetBrains, Keymint | Works air-gapped; privacy-preserving | Requires key distribution infra; re-issue on hardware swap |

## Recommendation for OpenMW

**Pattern (d) primary, with optional one-time activation phone-home.**

Rationale for a **consumer NVMe offload tool that runs completely on-device:**

1. **Privacy is the differentiator** — inference data never leaves the machine; license check must not require continuous connectivity.
2. **Feature tiers map cleanly** — Free / Pro / Studio gates on prefetch v3, KV quant, Unsloth (PART 8 tier table).
3. **Hardware fingerprint is stable enough** — `SHA256(cpu_id + nvme_serial)` stable across reboots, invalidates on major hardware change (acceptable for consumer SaaS).
4. **Graceful degradation beats hard lock** — expired license → Free tier, not crash (master plan + industry best practice).

### Implementation sketch (PART 8)

```
fingerprint = sha256(normalized_cpu_id + primary_nvme_serial)
license     = Ed25519-signed JWT (.lic file or pasted key)
verify      = local only — embedded public keys, no HTTPS at runtime
activation  = optional one-time POST to issue JWT bound to fingerprint
grace       = 7-day offline cache if activation server unreachable
```

### Tier gating alignment

| Tier | Price | Gated features |
|------|-------|----------------|
| Free | $0 | NANO+SMALL routing, basic prefetch |
| Pro | $9.99/mo | All tiers, flash-window, KV quant |
| Studio | $29.99/mo | Pro + Unsloth fine-tune + export |
| Enterprise | Custom | Multi-seat, API gateway |

### Anti-patterns to avoid

- Continuous heartbeat during inference (latency, privacy backlash).
- Pure usage metering without base subscription (OpenMW has no marginal cloud COGS).
- Hard lock on expiry (review bombing risk).

## Competitive positioning

OpenMW's pitch — "run models 2× bigger than your GPU, fine-tune locally" — requires
**offline-capable licensing** so users trust NVMe offload with proprietary workloads (legal, medical, code).

## Sources

- OpenMW master plan PART 8 spec
- Keymint offline Ed25519 verification docs
- Antigravity Lab local AI monetization patterns (desktop $20–60/mo, 7-day grace)
- Nalpeiron / Stripe AI SaaS pricing (hybrid subscription + usage — relevant for Enterprise tier only)
