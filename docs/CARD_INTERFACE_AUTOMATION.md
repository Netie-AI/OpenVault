# Cursor + Claude card — Interface automation (idiot-proof custody)

**Authority:** [`CLIPDROP_CONTRACT.md`](CLIPDROP_CONTRACT.md) (Claude decisions).
This card is the spine; the contract wins on conflict.

**Product pivot (2026-07-26).** Lead with *copy a key → lands in OpenVault →
tested → Proxy*. Zero forms when we can avoid them.

Keys SoT stays OpenVault (PRODUCT_ROLES). AirGPT / Netie are thin clients —
**never queue/cache a secret on failure** (contract §1).

---

## Spine

**Copy → detect → store (DPAPI master) → precheck → open Proxy.**

Ship order: contract §7. Do **not** build `/api/keys/ingest` until AirGPT calls it.

---

## A1 — ClipDrop paste/drop (OpenVault)

Done: hero zone, infer → dialog, Add & open Proxy, pulse + provider chip.

## A2 — Register memory

Done: sessionStorage. File persist later with Electron.

## A3 — Empty / first-run

Done: catch zone only; no role 0/0.

## A4 — Wire after add

Done: navigate to `/proxy` (product surface, not a settings dump).

## Electron clipboard

Done with Settings toggle, **default OFF** (contract §3).

## Ingest endpoint

**Gated.** Spec in contract §2. Cursor builds when AirGPT is ready.

---

## Not this card

- Health sparklines, Argon2id / per-key DEKs, scraping, auto-signup, password-manager silent import.
