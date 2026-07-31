# ClipDrop ingest contract + interface direction

Claude's decisions on [`CARD_INTERFACE_AUTOMATION.md`](CARD_INTERFACE_AUTOMATION.md).
Card **approved** — the spine is right. Below: the AirGPT bridge contract,
what is gated, motion direction, and how OpenVault/Cortex/Apps connect.

---

## 1. The rule that makes "no second vault" real

The card says AirGPT must be a thin client. That is a sentence, not an
enforcement. Here is the enforcement:

> **A client that cannot reach OpenVault must fail loudly and drop the secret.
> It may never queue, cache, retry-from-disk, or log it.**

A retry queue *is* a second vault — an unencrypted one, in a file nobody
audited, holding exactly the material the product exists to protect. This is
the single easiest way to undo the whole design, and it will look like good
engineering when someone adds it ("just so we don't lose the key if the app is
closed").

Concretely, AirGPT ClipDrop:

- holds the candidate secret **in memory only**, for the lifetime of the prompt
- on success: zeroes its copy, keeps only the returned `id` + masked preview
- on failure: shows *"OpenVault isn't running — open it and copy again"* and
  **discards**. It does not offer "save for later"
- never writes the secret to logs, telemetry, crash dumps, or `sessionStorage`

The same rule binds Netie Space and any future client.

---

## 2. `POST /api/keys/ingest` — the contract

A **separate endpoint** from `POST /api/keys`, deliberately. Same store, but a
different trust level, a different audit story, and idempotency semantics the
internal path should not have.

### Request

```http
POST /api/keys/ingest
X-OpenVault-Ingest: user-confirmed
X-OpenVault-Client: airgpt-clipdrop/1.4.0
Content-Type: application/json

{
  "secret":          "<the copied string>",
  "provider_hint":   "anthropic" | null,
  "source":          "clipboard" | "paste" | "drop" | "env" | "import",
  "captured_at":     1753500000.0,
  "register_intent": { "provider_id": "groq", "clicked_at": 1753499880.0 } | null,
  "label_hint":      "Groq free tier" | null
}
```

### Four rules the server enforces

**1. Loopback + intent header, same as reveal.** No header → `428`. Off
loopback → `403`. The header is the machine-readable assertion that *a human
clicked*, and a client that sends it without a click has lied — which is a
client bug we can name, not an ambiguity we have to guess at.

**2. The server re-infers. `provider_hint` is a tiebreaker, never authority.**
OpenVault runs its own `inferProvider` on the secret. If its inference and the
hint disagree, server inference wins and the response says so. A buggy or
compromised client must not be able to mislabel a key into the wrong provider —
that would route the user's traffic, and their spend, somewhere they did not
choose.

**3. Idempotent on the secret.** Clipboard pollers fire repeatedly; the same
string arriving twice must not create two keys. Dedupe on
`SHA-256(secret)` — stored as a column, never the secret itself — and return
`409` with the existing id. The user copying their key twice is not an error
worth a scary message.

**4. Refuse what does not look like a credential.** If the string fails
`looksLikeApiSecret` *and* there is no matching `register_intent`, return `422`
without storing. The clipboard is full of bank passwords, addresses and chat
messages. **A vault that stores everything is a liability, not a feature.**

### Responses

```jsonc
// 201 — stored
{ "id": "a1b2…", "provider": "anthropic", "label": "Anthropic key",
  "masked_secret": "sk-a…********", "precheck": "pending",
  "provider_source": "server_inferred",     // or "hint_agreed" | "register_intent"
  "next": "/proxy?highlight=a1b2…" }

// 409 — already have it
{ "duplicate_of": "a1b2…", "label": "Anthropic key", "next": "/proxy?highlight=a1b2…" }

// 422 — not a credential, nothing stored
{ "stored": false, "reason": "not_credential_shaped",
  "message": "That doesn't look like an API key, so nothing was saved." }
```

**The response never echoes the secret.** Masked only. A response body ends up
in devtools, HAR files and screen recordings.

### Also required

- **Rate limit it.** A clipboard poller in a loop must not be able to hammer
  the store. Reuse `vault/ratelimit.py`; a low ceiling is fine.
- **Audit every ingest** to the existing `secret_audit.jsonl`:
  `{event: "secret_ingest", client, source, provider, key_id, dedupe_hit}`.
  Never the secret.
- **`register_intent` is advisory.** It may raise confidence enough to accept a
  weak-shaped secret, but it may not override rule 4's refusal on something
  that looks like a sentence or a bank password.

---

## 3. Gated — do not build

| Idea | Verdict |
|---|---|
| Scraping provider dashboards for keys | **No.** Automating an authenticated session against a third party's ToS, and the failure mode is silent breakage on every redesign. |
| Auto-filling or submitting signup forms | **No.** Account creation on someone's behalf is the user's action, not ours. |
| Creating provider accounts automatically | **No.** |
| Bulk-importing a password manager's export | **Only as an explicit file the user chooses**, shown as a reviewable list before anything is stored. Never silent, never background. |
| Reading the clipboard while OpenVault is not focused | **Only in Electron, only with the poll visible in Settings and a way to turn it off.** A background clipboard reader the user forgot about is spyware, regardless of intent. |
| Storing clipboard content that failed detection | **No.** Not even hashed, not even "for improving detection". |

Remembering *what the user clicked* (register intent) is fine — it is their own
action, stored locally, and it never leaves the machine.

---

## 4. UX copy — the mindset, and the thing to never write

The internal mindset is **"never make the user do what we can do."** That is
correct and it should drive every decision here.

The product copy must never *express* it. Nothing in the UI may imply the user
is slow, and nothing may be cute about their security. Concretely:

| Don't | Do |
|---|---|
| "We detected a key! 🎉" | "Anthropic key detected" |
| "Oops! That didn't work" | "Anthropic rejected this key — it may be revoked" |
| "Saving your key securely…" | "Stored · testing…" |
| "Easy mode" / "Simple setup" | (say nothing; just be simple) |
| "0/0 keys working" | (render nothing until there is a key) |

Three copy rules:

1. **Name the provider, not the action.** "Anthropic key detected" tells them
   we understood. "Key detected" makes them check.
2. **A failure states the cause and the next move**, in that order, in one
   sentence. "Anthropic rejected this key — paste a new one" beats "Precheck
   failed (401)".
3. **Never claim a state we have not observed.** No green tick until precheck
   returns. This is the same rule as the LIVE badge, applied to keys.

On the register-return prompt, the card's draft is good. Tighten to:

> **Groq key detected** — you registered a minute ago.
> `[Add & open Proxy]` `[Not now]`

Not "Looks like the Groq key you just registered — Add & open Proxy?" — a
question invites deliberation about a thing we are already confident about.

---

## 5. Motion — what earns an animation

The pivot is a *feel* problem, so this is not decoration. Principle:

> **Motion exists to show causality.** If it does not explain what caused what,
> or where a thing came from, cut it.

### The one moment that matters

Copy → detect is the product's entire promise. It must read as *the app
noticed*, not as a dialog appearing:

1. Clipboard hit → the drop zone **pulses once** (scale 1 → 1.02 → 1, 180ms).
   That is the "I saw that".
2. The provider chip **crossfades in** with the inferred name (120ms). Not a
   slide — the identity was determined, it did not travel.
3. The dialog **rises 8px + fades** over 220ms, `cubic-bezier(.2,.8,.2,1)`.
   Origin matters: it must appear anchored to the drop zone, not centred from
   nowhere, so the eye tracks the causal chain.

### Numbers, so this is not left to taste

| Thing | Duration | Easing |
|---|---|---|
| State change (hover, toggle, chip) | 120–160ms | `ease-out` |
| Entrance (dialog, row, banner) | 200–260ms | `cubic-bezier(.2,.8,.2,1)` |
| Exit | 140–180ms | `ease-in` — leaving is faster than arriving |
| Page transition | 180ms crossfade | no slide; the top bar is fixed |
| Attention pulse | 180ms, **once** | never loop |

### Rules

- **No spinner under 400ms.** A flash of loading is worse than a still frame.
  The precheck "Testing…" state is the exception because it is genuinely long —
  and it must resolve to a real status, never decay into a grey dot.
- **New rows animate in; existing rows never re-animate.** A list that
  re-shuffles on every poll is the fastest way to make an app feel broken.
- **One thing moves at a time.** If the dialog is entering, the row behind it
  does not.
- **`prefers-reduced-motion: reduce` removes movement, not feedback.** The
  pulse becomes a 1-frame border-colour change. Never leave the user with no
  confirmation.
- **Never animate on data refresh.** Polling must be invisible. If a number
  changes, change it — do not count up to it.

The tokens (`--ov-topbar-h`, the `--th-*` scale) already exist; add
`--ov-dur-fast: 140ms`, `--ov-dur-enter: 220ms`, `--ov-ease-enter` so these are
not retyped per component.

---

## 6. OpenVault ↔ Cortex ↔ Apps

The connection the pivot implies, stated so it is not invented per-app:

> **Apps get a gateway, not a key.**

```
   ┌─────────────┐  keys never leave  ┌──────────────────┐
   │  OpenVault  │◄───────────────────│  ClipDrop / paste │
   │  keys SoT   │                    └──────────────────┘
   │  :5000/v1   │
   └──────┬──────┘
          │  OpenAI-compatible endpoint, key injected server-side
     ┌────┴────┬──────────────┐
     ▼         ▼              ▼
  Cortex    Netie Space    user's app
```

Three rules:

1. **No consumer ever receives a secret.** They point their OpenAI-compatible
   client at `http://127.0.0.1:5000/v1` and OpenVault injects the credential,
   applies the budget, and handles fallback. This is what makes "one-stop
   vault" true rather than a slogan — the key exists in exactly one process.
2. **Cortex is a consumer, not a peer, for keys.** It already probes
   `/api/cortex/status`; for model calls it should go through `:5000/v1` like
   everything else. Any Cortex-side credential store is the same violation as
   an AirGPT queue file.
3. **One endpoint, forever.** `:5000/v1`. OmniRoute's `:20128` stays external
   reference only. Two gateways means two credential paths, and the second one
   is always the one nobody hardened.

**Consequence worth stating:** the Proxy page is not a settings screen, it is
*the product surface* — the place a user sees that their key is working and
what is using it. Landing there after add (A4) is correct for that reason, not
just as a convenience.

---

## 7. Ship order

1. **A1 ClipDrop in OpenVault** — paste/drop only. Proves the spine with no IPC.
2. **A3 empty-state honesty** — trivial, and it is what the user sees first.
3. **A2 register memory** — `sessionStorage` first; the file comes with Electron.
4. **Electron clipboard poll** — needs the Settings toggle from §3 in the same slice.
5. **`/api/keys/ingest`** — build it when AirGPT is ready to call it, not before.
   The endpoint is worthless without a client and would sit untested.
6. **Ship PAT ClipDrop** — same detector, different destination.

Health history stays parked. It was the right *second* thing and the wrong
*first* thing.
