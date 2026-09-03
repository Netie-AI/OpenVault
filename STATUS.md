# OpenVault - Status

> Canonical "what's true right now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-08-28. **HUMAN_STOP** still holds for #18 HT1-HT5.
Human runbook: [`docs/ONE_SEAT_DEMO.md`](docs/ONE_SEAT_DEMO.md) (Spaceship `/ht1-demo/`,
not the homepage). This laptop: `E:\OpenVault` + `E:\Cortex` (`D:\` copies missing).
`:5000`/`:3010` up. Cortex constructor mount `:8010` up (`packs.dms.constructor_app`).
Constructor live path: `http://127.0.0.1:8010/cortex/constructor/` (bound; ov_ issued).
`:8778` is sketch, no fetch. FreeRoute `auto` parks 429 hops last; meters on;
catalog `priced` false; no NVIDIA classifier. Unhealthy pool is typed 503, not
empty. Ship preview is loopback-only (not HT1). Live site is Spaceship. Do not
buy a second host. Agents cannot check HT1-HT5 off.

**UI:** `:3010` / `:5000`. Scripted demo:
`cd OpenMW && uv run --no-sync python scripts/one_seat_demo.py`

## Distance

~72%. #12-#17 closed as shipped; #13 reopened for F17/F18 (bak + dump-import). Demo #18 open
until HT1-HT5. **Landing already lives on Spaceship** (`https://netie.ai`).
Spaceship FTP adapter exists but will not overwrite live without
`OPENVAULT_SPACESHIP_ALLOW_PUBLISH=1`. FreeRoute meters; **pricing is not**.

## HUMAN_STOP (#18)

1. HT1 Live Spaceship/CF/Coolify/Netlify/VPS URL under gate
2. HT2 Live FreeRoute + real vaulted keys
3. HT3 Unseal/lock UX on `:3010`
4. HT4 Phase 0 Cortex smoke via OpenVault
5. HT5 Secrets-at-ship inject; no plaintext leak

## Next

| # | Status |
|---|--------|
| #13 CUSTODY reopen | **#37 CLOSED.** #38 importer landed; Chrome dump sealed (357). Next [#39](https://github.com/Netie-AI/OpenVault/issues/39) agent retrieve |
| #18 DEMO epic | **Sit now** - founder runs HT1-HT5 via ONE_SEAT_DEMO.md. Agents cannot check these off |
| #33 EPIC host+meter | Open - children closed; blocked on #18 HT1 |
| Skills library | **NEEDS-YOU.** PRODUCT_ROLES amendment across four repos |
| Pricing | **NEEDS-YOU.** DR-0009 (a) - we carry provider cost |
| Serving-engine / LB | OpenVault owns engine pick. Multi-node not built - PARKING_LOT |

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
