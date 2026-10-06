# FreeRoute role routing, spend caps, and response stamps (Refs #148)

Contract for Cortex #303/#304 on `POST /v1/chat/completions`. Code:
`OpenMW/openmw/openvault/vault/route_policy.py`. Config:
`OpenMW/openmw/openvault/vault/route_policy.json`.

## Request fields

| Field | Values | Behaviour |
|---|---|---|
| `role` | `sql` \| `tool` \| `reason` \| `summarize` | Walks that role's ordered model list from the policy. Free models come first. On a provider failure the walk moves to the next entry in order. `model` is not used to pick the hop. |
| `role` omitted or `null` | - | Unchanged: the pre-role pool walk. Configured caps still apply. |
| `role` anything else (`""`, `"SQL"`, `"chat"`, `7`) | - | **422** `openvault_unknown_role`, with `allowed: ["sql","tool","reason","summarize"]`. No upstream call. |
| `role` with `strict` (or `X-OpenVault-Strict`) or `local_only` | - | **422** `openvault_role_conflict`, with `conflicts_with`. |
| `service_id` | string, max 128 | Budget scope for a loopback caller, e.g. `cortex`. An issued `ov_` key is always its own scope (its key id), and this field is ignored for keyed callers. Default: the loopback host. |

Neither `role` nor `service_id` is forwarded upstream.

## Response stamps

Every JSON response from the gateway carries these top-level fields. That
includes errors and fallbacks. The existing `served_provider`, `served_model`,
and `served_local` fields stay.

| Field | Served | Nothing served |
|---|---|---|
| `model` | catalog id actually sent to the hop that served (after any fallback) | `null` |
| `provider` | provider id of that hop, e.g. `groq` | `null` |
| `tokens_in` | upstream `usage.prompt_tokens`, or `null` if upstream sent no usage | `0` |
| `tokens_out` | upstream `usage.completion_tokens`, or `null` | `0` |
| `est_cost_usd` | from the policy price table. A free model costs `0.0`. A paid model without a price is `null`. When usage is missing it is the reserved worst case | `0.0` |

Streams add `model` and `provider` to every `data:` chunk. The token and cost
fields go on the chunk that carries `usage`, so send
`stream_options.include_usage: true` to get them. The 401 auth refusal body is
unchanged and is not stamped.

## `budget_exceeded` refusal

HTTP **402**. No upstream call is made for the refused hop. The walk never
moves on to a paid model to find room.

```json
{
  "error": {
    "type": "openvault_budget_exceeded",
    "reason": "budget_exceeded",
    "cap": "caller.daily_usd",
    "message": "caller.daily_usd is 1.0 USD; 0.99 spent, this request needs ~0.02",
    "cap_detail": {
      "scope": "caller", "id": "cortex", "limit_name": "daily_usd",
      "limit": 1.0, "spent": 0.99, "requested": 0.02, "unit": "usd",
      "provider": "openai", "model": "gpt-4o-mini"
    },
    "details": ["..."]
  },
  "model": null, "provider": null, "tokens_in": 0, "tokens_out": 0, "est_cost_usd": 0.0,
  "served_provider": null, "served_model": null, "served_local": false
}
```

`cap` is one of `key.daily_usd`, `key.monthly_usd`, `caller.daily_usd`,
`caller.monthly_usd`, `request.max_tokens_per_request` (`unit: "tokens"`), or
`paid_default`. `paid_default` means a role walk reached a paid model and no USD
cap is set for that vault key or caller. For a key cap, `id` is the first 8
characters of the vault key id. `requested: null` means the paid model has no
price, so the cap cannot be checked.

## Policy file

- Shipped defaults: `OpenMW/openmw/openvault/vault/route_policy.json`.
- Operator override: `OPENVAULT_ROUTE_POLICY=/path/to/policy.json`, read on
  every request. A role list there replaces that role. `models` entries are
  checked before the defaults (an exact `provider/model` beats a glob). `caps`
  entries merge by id. If the file is unreadable or invalid, the gateway
  returns 503 `openvault_route_policy_invalid`. It does not run without caps.

Default role lists. All are free tier on providers OpenVault already supports.
The catalog has no Qwen2.5-Coder-32B id, so `sql` uses the closest Qwen-class
catalog ids.

| Role | Order |
|---|---|
| `sql` | groq `qwen/qwen3.8-27b`, openrouter `qwen/qwen3.8-27b:free`, sea_lion `aisingapore/Qwen-SEA-LION-v4.5-27B-IT` |
| `tool` | groq `openai/gpt-oss-20b`, google `gemini-3.1-flash-lite`, nvidia `openai/gpt-oss-20b` |
| `reason` | groq `openai/gpt-oss-120b`, sambanova `gpt-oss-120b`, nvidia `nvidia/nemotron-3-super-120b-a12b`, openrouter `nvidia/nemotron-3-ultra-550b-a55b:free` |
| `summarize` | google `gemini-3.1-flash-lite`, groq `openai/gpt-oss-20b`, openrouter `google/gemma-4-31b-it:free`, sambanova `gemma-4-31B-it` |

The defaults mark these providers' models free: `groq/*`, `google/*`, `nvidia/*`,
`sambanova/*`, `sea_lion/*`, `openrouter/*:free`, and `local_qwen/*`. Every other
model is paid. No paid prices ship, because a guessed number would read as a
measured one. The operator adds the price for each paid model. Default caps:
none, and `max_tokens_per_request: null`.

Example override. Replace the `<...>` placeholders with the provider's own
published rate. The file will not load until you do. Do not use `0`: a paid
model priced at 0 never moves a cap.

```text
{
  "roles": {"sql": [{"provider": "groq", "model": "qwen/qwen3.8-27b"},
                    {"provider": "openai", "model": "gpt-4o-mini"}]},
  "models": {"openai/gpt-4o-mini": {"usd_per_1m_in": <in>, "usd_per_1m_out": <out>}},
  "caps": {
    "max_tokens_per_request": 4096,
    "keys": {"<vault key id>": {"daily_usd": 2, "monthly_usd": 20}},
    "callers": {"cortex": {"daily_usd": 1, "monthly_usd": 10, "max_tokens_per_request": 2048}}
  }
}
```

## Cap rules

- A model is paid unless the policy marks it `"free": true`.
- On a `role` request, a paid hop runs only if the vault key it would spend, or
  the caller, has a `daily_usd` or `monthly_usd` cap. Without one the hop is
  refused as `paid_default`. A request without `role` keeps the pool walk, so a
  paid pooled key still serves it when no cap applies.
- Each applicable cap is checked before the hop: spent in the window, plus
  in-flight holds in this process, plus this hop's worst case (prompt estimate
  plus the output budget actually sent). Windows are UTC day and UTC month.
- Spend persists in keys.db table `route_spend`. It sits beside `usage_events`
  and uses the same `event_id`. There is no new store.
- `max_tokens_per_request` is the smaller of the global and the per-caller
  value. A larger `max_tokens` is refused. With no `max_tokens`, the cap is
  sent. It also bounds the reasoning floor: a reasoning model that needs more
  than the cap is skipped.
- Holds are per process. Two gateway processes that share one keys.db can each
  admit one hop past a cap before the other's row lands.
