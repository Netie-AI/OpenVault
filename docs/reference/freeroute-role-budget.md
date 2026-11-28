# FreeRoute role routing, spend caps, and response stamps (Refs #148)

Contract for Cortex #303/#304 on `POST /v1/chat/completions`. Code:
`OpenMW/openmw/openvault/vault/route_policy.py`. Config:
`OpenMW/openmw/openvault/vault/route_policy.json`.

## Request fields

| Field | Values | Behaviour |
|---|---|---|
| `role` | `sql` \| `tool` \| `reason` \| `summarize` \| `grain` \| `ontology` | Walks that role's ordered model list from the policy. Free models come first. On a provider failure the walk moves to the next entry in order. `model` is not used to pick the hop. |
| `role` omitted or `null` | - | Pool walk, same order as before. A paid hop still needs a USD cap. Without one it is 402 `paid_default` and is not sent. Free hops are unchanged. |
| `role` anything else (`""`, `"SQL"`, `"chat"`, `7`) | - | **422** `openvault_unknown_role`, with `allowed: ["sql","tool","reason","summarize","grain","ontology"]`. No upstream call. |
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
| `est_cost_usd` | from the policy price table. A free model costs `0.0`. A paid model without a price is the string `cost_unknown` (never null, never `0.0`). When usage is missing on a priced hop it is the reserved worst case | `0.0`, or `cost_unknown` when the refusal is an unpriced paid model |

Streams add `model` and `provider` to every `data:` chunk. The token and cost
fields go on the chunk that carries `usage`, so send
`stream_options.include_usage: true` to get them. The 401 auth refusal body is
unchanged and is not stamped.

## `budget_exceeded` refusal

HTTP **402** only when every hop has been considered and none served. No
upstream call is made for the refused hop. That hop is skipped, its cap name
is written to `hop_attempts`, and the walk tries the next hop. A provider
402 parks that key and is the same kind of skip. Prices, caps, and the rule
that an unpriced model is paid do not change.

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
`caller.monthly_usd`, `request.max_tokens_per_request` (`unit: "tokens"`),
`request.completions` (`unit: "completions"`, see Cap rules), or
`paid_default`. `paid_default` means a paid hop was reached and no USD cap is
set for that vault key or caller. That includes a request that omits `role`.
For a key cap, `id` is the first 8 characters of the vault key id.
`requested: null` means the paid model has no price, so the cap cannot be
checked, and `est_cost_usd` on that response is `cost_unknown`.

## Downgrade stamp

When a hop is refused by a cap (including `paid_default` and an unpriced paid
model) and a later hop serves, the response says so. The body fields are
`served_downgraded: true`, `downgraded_from` (the first refused hop,
`provider/model`), and `downgraded_to` (the hop that served). The headers are
`X-OpenVault-Served-Downgraded: true`, `X-OpenVault-Downgraded-From`, and
`X-OpenVault-Downgraded-To`. The same three values are stored on the usage
ledger row. A response that was not downgraded omits them. Streams put the
body fields on each `data:` object and the headers on the response.

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
| `grain` | groq `openai/gpt-oss-20b`, google `gemini-3.1-flash-lite`, openrouter `google/gemma-4-31b-it:free` |
| `ontology` | groq `openai/gpt-oss-120b`, openrouter `nvidia/nemotron-3-ultra-550b-a55b:free`, google `gemini-3.5-flash` |

`grain` (DMS grain AI: short classification and mapping) and `ontology` (DMS
ontology compile: structured JSON generation) are pinned to groq, google and
openrouter only, by the 2026-08-22 decision that DMS model calls never reach
DeepSeek. They carry no nvidia, sambanova or sea_lion hop.

The defaults mark these providers' models free: `groq/*`, `google/*`, `nvidia/*`,
`sambanova/*`, `sea_lion/*`, `openrouter/*:free`, and `local_qwen/*`. Every other
model is paid. The default file ships no paid prices, because a guessed number
would read as a measured one. An operator file adds the price for each paid
model. Default caps: none, and `max_tokens_per_request: null`.

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

## Anthropic opt-in

Anthropic is not in the default role lists. A vault key with `provider: anthropic`
does not receive `/v1/chat/completions` traffic on its own. The historical
pool-walk skip (`anthropic chat not via /v1 proxy yet`) stays in that case.
With `OPENVAULT_ROUTE_POLICY` unset, the walk is the shipped
`route_policy.json` and nothing else.

Founder decision 2026-11-27 07:27 SGT: Claude is the DMS serving path. The
example is `config/route_policy.claude.json`. It is not loaded by default.
Enable it with the env var set to that file (absolute path, or a path the
process can open):

```text
OPENVAULT_ROUTE_POLICY=/path/to/OpenVault/config/route_policy.claude.json
```

Unset the variable to go back to the shipped defaults. A role list in the
example replaces that role. The default free-model globs stay underneath.

| Role | First hop | Why |
|------|-----------|-----|
| `sql`, `tool`, `summarize` | `claude-sonnet-5-5` | DMS answer and SQL serving. These are the lighter rungs in the default ladder. |
| `reason` | `claude-opus-5-5` | Escalation and hard work. |
| `grain`, `ontology` | `claude-opus-5-5` | Named by the founder for Opus. |

After the Claude hop, each role keeps its current free hops whose provider is
`groq`, `google`, `openrouter`, or `nvidia`, in the same order. `sea_lion` and
`sambanova` are left off this example. `grain` and `ontology` had no nvidia
hop, so they still do not.

Prices in that file are the Claude API base rates, USD per million tokens, from
<https://platform.claude.com/docs/en/about-claude/pricing> (model ids
`claude-sonnet-5-5` and `claude-opus-5-5`,
<https://platform.claude.com/docs/en/about-claude/models/overview>):

| Model | Input | Output |
|-------|-------|--------|
| Claude Sonnet 5.5 | $2 | $10 |
| Claude Opus 5.5 | $4 | $20 |

The stamp uses those two rates. Published cache reads on these two models are
0.05x the base input price, and cache writes are higher. The adapter counts
cache tokens inside `prompt_tokens`, so a cache hit is stamped at the base
input rate. Batch pricing is not used.

The one spend knob is `caps.callers.dms.daily_usd`, set to `20`. Both Claude
models draw from that one daily ceiling. A rough day at these rates is about
$0.02 for a 4k-in / 1k-out Sonnet answer and about $0.16 for a 20k-in / 4k-out
Opus compile, so $20 is about a thousand Sonnet answers or about a hundred
Opus compiles. Change that one number. Do not copy it onto a second cap.

The cap binds when the caller id is `dms`. Loopback DMS sends
`"service_id": "dms"`. An issued `ov_` key is its own caller id and ignores
`service_id`; move the same number onto `caps.keys.<vault key id>` and remove
the caller entry so there is still one number. The vault key `provider` must
be `anthropic`. The free-text label is not a gate. The key is sent as
`x-api-key` and is not logged.

Without `usd_per_1m_in` and `usd_per_1m_out` the hop is refused as unpriced
(`requested: null`, `est_cost_usd` is `cost_unknown`) and nothing is posted.
The walk tries the next hop. Without a `daily_usd` or `monthly_usd` on the
key or the caller, the hop is refused as `paid_default` whether or not the
request names a role. The same #148 caps and `model` / `provider` / `tokens_in` /
`tokens_out` / `est_cost_usd` stamps apply. A 401, 429, or 529 (overload,
mapped to 503) walks to the next hop in that role.

`stream: true` is not translated. If a later hop can stream, the walk uses it.
If anthropic was the only hop, the response is 400
`openvault_anthropic_unsupported`.

## Cap rules

- A model is paid unless the policy marks it `"free": true`.
- A paid hop runs only if the vault key it would spend, or the caller, has a
  `daily_usd` or `monthly_usd` cap. Without one the hop is refused as
  `paid_default` (402). A request without `role` still walks the pool, but a
  paid pooled key does not serve when no cap applies.
- Each applicable cap is checked before the hop: spent in the window, plus
  in-flight holds in this process, plus this hop's worst case (prompt estimate
  plus the output budget actually sent, once per completion). Windows are UTC
  day and UTC month.
- Completions: a capped paid hop reserves `n` times `best_of` completions
  (each defaults to 1). Each must be a whole number from 1 to 128. Anything
  else (a string, float, bool, zero, negative, over 128) cannot be bounded, so
  that hop is refused as `request.completions` and no upstream call is made.
- Output bound: a capped paid hop always sends the output budget it reserved.
  A caller who named none gets `max_tokens` set to the 1024 default, or
  `max_completion_tokens` if that is the field they used. A streamed one also
  gets `stream_options.include_usage: true` (other `stream_options` keys are
  kept), so the ledger records the usage frame instead of the hold, and the
  client receives that frame. A free hop is sent exactly as the caller wrote
  it. A paid hop with no cap is not sent.
- Spend persists in keys.db table `route_spend`. It sits beside `usage_events`
  and uses the same `event_id`. There is no new store.
- `max_tokens_per_request` is the smaller of the global and the per-caller
  value. A larger `max_tokens` is refused. With no `max_tokens`, the cap is
  sent. It also bounds the reasoning floor: a reasoning model that needs more
  than the cap is skipped.
- Holds are per process. Two gateway processes that share one keys.db can each
  admit one hop past a cap before the other's row lands.
- If the ledger write fails for a paid hop that served, the response is
  unchanged but the hold is not released: it keeps counting against the caps
  until the process restarts, and an error `usage_ledger_write_failed_hold_kept`
  is logged. A hop that served nothing releases its hold as usual.
- An unreadable policy file returns 503 with the file name only. The full path
  and the OS error go to the log (`route_policy_unreadable`).
