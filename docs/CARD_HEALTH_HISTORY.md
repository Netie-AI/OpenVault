# Cursor card — provider health history

Designed by Claude; mechanical to build. Turns the vault from a store into
something you *check*. Do this after the current C-queue.

**Why this one:** we already run a precheck against every enabled key every 60s
(`PrecheckLoop`, `app.py:441`) and throw the result away except for the latest.
A key that fails one probe in ten is indistinguishable from a healthy one
today, which is exactly the failure users cannot diagnose themselves.

---

## H1 — Persist precheck results

**Files:** `OpenMW/openmw/openvault/vault/health_store.py` (new),
`vault/precheck.py` (call it), `tests/test_health_store.py` (new).

SQLite table beside `keys.db`:

```sql
CREATE TABLE IF NOT EXISTS precheck_history (
  key_id      TEXT NOT NULL,
  checked_at  REAL NOT NULL,
  status      TEXT NOT NULL,   -- ok | auth_fail | rate_limit | error | timeout
  latency_ms  REAL,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS ix_precheck_key_time
  ON precheck_history (key_id, checked_at DESC);
```

**Retention is not optional.** At 60s intervals this is 1,440 rows per key per
day. Prune to the last 7 days (or 2,000 rows per key, whichever is smaller) on
every write path, and add a test that inserting 5,000 rows leaves ≤ 2,000.
Unbounded growth on a user's disk is a bug, not a detail.

**Write on transition, plus a heartbeat.** Storing every probe is mostly
duplicate rows. Store a row when `status` changes, and otherwise at most one
row per 15 minutes per key. Same information, ~2% of the volume.

**Proof:** `uv run pytest tests/test_health_store.py -q` green; pruning test
included.

---

## H2 — Expose it

**File:** `OpenMW/openmw/openvault/routers/` — a new router, **not** `app.py`.

```
GET /api/keys/{key_id}/health?window=24h
  -> { key_id, window, samples: [{t, status, latency_ms}],
       uptime_pct, p50_latency_ms, p95_latency_ms,
       current_status, last_change_at }
```

Rules:
- `uptime_pct` counts `ok` over total samples **in the window**, not all time.
- If there are fewer than 3 samples, return the samples and set
  `uptime_pct: null`. Do not compute a percentage from one data point — a
  single probe rendering as "100% uptime" is the same class of lie as the
  RTX 4050.
- `rate_limit` is **not** a failure. Count it separately; a rate-limited key is
  working, just busy.

**Proof:** endpoint returns 200 with a real shape; a key with 1 sample returns
`uptime_pct: null`.

---

## H3 — Sparkline in the vault

**Files:** `apps/web/src/lib/api/keys.ts`, the key row component.

- A ~60px inline sparkline per key row: green `ok`, amber `rate_limit`, red
  everything else. Latency as the line, status as the point colour.
- Under it, one line of plain text: `99.2% over 24h · p95 340ms`. Do not make
  the user hover to learn whether their key works.
- Fewer than 3 samples → render the dots and the words "not enough data", never
  a percentage.
- Clicking the sparkline opens the existing key modal on a History tab.

**Proof:** build exits 0; a key with a forced auth failure shows red points and
an uptime below 100%.

---

## H4 — Use it in the fallback chain

**File:** `vault/fallback.py`.

The circuit breaker currently reacts only to live failures. With history, a key
that has failed 3 of the last 5 probes should be **deprioritised before** a
request is routed to it, rather than after it fails one.

Keep this conservative: deprioritise, never auto-disable. Silently disabling a
user's key is worse than one slow request, and an auto-disable that fires on a
transient outage is very hard for a user to diagnose.

**Proof:** unit test — a key with 3/5 recent failures sorts below a healthy key
of the same role.

---

## Not in this card

- Alerting/notifications on key failure. Needs a decision about *where* an
  alert goes for a local-first app with no server.
- Per-key cost/usage. Related and equally valuable, but it is a separate card:
  the rate limiter already measures the tokens, so it is a plumbing job rather
  than a new measurement.
