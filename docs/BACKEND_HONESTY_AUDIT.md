# OpenVault backend — honesty audit

Traced against `OpenMW/openmw/openvault/` on 2026-07-25. Line numbers are the
decorator or statement line. Companion to `OPENSHIP_APP_PLAN.md`; this file is
the evidence base for "what is real", so the new UI only binds to real things.

All routes live in one file (`app.py`, `create_app()`). No `APIRouter` exists
anywhere in the package yet — Stage 1 lanes introduce the first ones.

---

## 1. Security — fix before the app ships

These are not style points. OpenVault's pitch is "a strong one-stop key vault",
and today it is not one.

1. **`GET /api/keys/{key_id}/secret` (app.py:899) returns plaintext to any
   caller that can reach the port.** No API token, no origin check, no loopback
   enforcement in code — the bind address is only a CLI default. There is no
   authentication or authorization on *any* route.
2. **The master key has no KDF and sits beside the ciphertext.**
   `_load_or_create_master_key` (crypto.py:17-26) calls `Fernet.generate_key()`
   and writes the raw key to `~/.openvault/master.key` in plaintext, next to
   `keys.db`. No passphrase, no Argon2/scrypt/PBKDF2, no DPAPI, no Credential
   Manager. The `chmod(0o600)` on line 25 is a no-op on NTFS. Any process
   running as the user recovers every secret. The encryption defends against a
   stolen `keys.db` *alone* and nothing else.
3. **No unseal/lock state.** Once the process is up the vault is permanently
   open — no idle re-lock, no re-auth to reveal, no zeroization.
4. ~~**No access audit for secrets.**~~ **Closed 2026-07-27.** Reveal, create,
   update, delete, revoke, rotate, incident-kill, keyvault-upsert, and env-ingest
   all append to `~/.openvault/secret_audit.jsonl`, for API keys and for the new
   password / payment-card kinds alike. The same pass found that every custody
   *mutation* was ungated (only reveal had controls) and made them loopback-only.
   See [`SECRETS_CUSTODY.md`](SECRETS_CUSTODY.md) for the route-by-route table and
   for what item 1 above does and does not still cover.
5. **The GitHub PAT bypasses the vault entirely** — plaintext JSON at
   `~/.openvault/github/pat.json` (github_auth.py:153).
6. **`PrecheckLoop` decrypts and ships every enabled key upstream every 60s**
   (app.py:441) — 1,440 egress events per key per day, by default.
7. **Precheck treats HTTP 404 as `ok`** (precheck.py:57-58), so a wrong base URL
   reports a healthy key.

Lesser gaps: AES-128 via Fernet with no versioned envelope (no migration path to
AES-256-GCM), no expiry/TTL/scheduled rotation, no per-key spend accounting, no
secret history or backup-with-re-encryption (losing `master.key` is
irreversible), no RBAC, no tamper detection over the DB.

What is genuinely good: the key **lifecycle** (`active|revoked|rotated|
compromised` with `replaced_by` chaining, store.py:281-371), `incident_kill`,
the **rate limiter** (vault/ratelimit.py — dual refilling buckets, reserves
`prompt+max` up front and refunds against upstream `usage`), the Redis store's
atomic Lua reserve/refund, the fallback chain with per-key circuit breakers, and
the careful dry-run-by-default env import.

---

## 2. What the "Detection / Bottleneck / Data Flow / Middleware Gain" tabs show

### Actually measured (with `--mock-health` off)

- RAM total, logical cores — `psutil` (device_profile.py:210-217).
- GPU name, total VRAM — `pynvml` (device_profile.py:146-168).
- NVMe model and device path — `nvme_sentinel.inventory.discovery.list_devices()`,
  which shells out to `Get-PhysicalDisk` (inventory/windows.py:15-35).
- TBW estimate — real *if* NVMe admin passthrough succeeds; without
  Administrator it falls back to WMI counters lacking `data_units_written` and
  returns **0.0** (device_profile.py:246-262).

### Fabricated

| Displayed value | What it actually is |
|---|---|
| Device health % (94/78/88/72/96) | Constants. `nvme_health = 94 if seq_read >= 5.0 else 78`; CPU `health_pct: 96` hardcoded (demo_payload.py:68-69, 97) |
| `"telemetry": "native-nvme" \| "nvml" \| "psutil"` labels | Decorative strings, not evidence of a data source (demo_payload.py:80, 91, 102) |
| GPU memory bandwidth | Substring lookup in a hardcoded dict; anything unmatched returns `_DEFAULT_GPU_BANDWIDTH_GBPS = 100.0` (device_profile.py:81-91). An RTX 4050 laptop GPU matches nothing |
| NVMe sequential read GB/s | Writes a 64 MB temp file then re-reads it — entirely from the Windows page cache. **Measures RAM, not the SSD.** Also divides by nominal rather than measured elapsed time (device_profile.py:281-310) |
| Path trace / data flow / bottleneck hop | `build_mock_path_trace_report()` is called **unconditionally** at demo_payload.py:146, even with live detect enabled |

### The Bottleneck is a constant

Inputs are hardcoded: two admin records (2.5 ms/512 B, 1.2 ms/4096 B, adapter
`MockNvmeAdapter`) at `Profiler/nvme_profiler/path_trace.py:43-53`, and three
GPU-side hops (PCIe 5.0 ms, RAM→VRAM 30.0 ms, GPU compute 100.0 ms) at
`nsys.py:94-121`. `fuse.py:26-30` ranks by `duration/theoretical_bw` off a fixed
table, so:

**The bottleneck resolves to `ram_to_vram` on every machine, every time.**
`gpu_idle_pct_waiting_on_io` is likewise always **27.9%** (fuse.py:33-46).

The pressure metric is also dimensionally meaningless — seconds ÷ (bytes/second)
= s²/byte. It ranks; it is not a physical quantity.

### "Middleware Gain" is a constant too

`_middleware_comparison()` (demo_payload.py:107-132): `baseline_tok_s` comes from
the midpoint of a hardcoded per-tier table (model_router.py:45-51) — **no token
was ever generated or timed**. Then `idle_reduction = clamp(gpu_idle/100 × 0.62,
…)` and `speed_factor = 1 + idle_reduction × 1.35`. With the invariant 27.9%
this is always **≈ +23.4%**. `bottleneck_after` is hardcoded to `GPU_COMPUTE`.

> These numbers are not random. They are *constant and plausible*, which is
> worse — they read as telemetry.

### `last_admin_timings.json` is never written

Read at `observe/path.py:48`. A repo-wide grep finds only the reader, a UI
caption admitting it (`webui/index.html:968`), and a planning note. So
`_try_live_report()` always returns `None` and `/api/observe/path` and
`/api/health/bottleneck` fall through to mock **in every configuration**. They
have never returned live data.

The only producer of admin timings is `nvme_sentinel/hal/base.py:52`
(`log.info("admin_command_timing", …)` with `{opcode, duration_ms, status, nsid,
data_len, adapter}`) — exactly the shape `fuse_admin_timings` expects. Nothing
serializes it.

### `--mock-health` ships enabled

Defined at cli.py:177-179, consumed at exactly one place (app.py:471). **Both**
Windows launchers pass it — `Start-LocalMesh.ps1:50` and
`Start-NetieStack.ps1:101` — so in practice the shipped stack runs in demo mode
against `_DEMO_FALLBACK_PROFILE` (RTX 4050 / 6 GB / 32 GB / Micron 3400 /
6.8 GB/s / 600 TBW). It has **no effect** on the bottleneck routes either way.

---

## 3. The finding that unblocks Sentinel: SMART without Administrator

`nvme_sentinel/adapters/windows.py:227` opens the device
`GENERIC_READ|GENERIC_WRITE` for `IOCTL_STORAGE_PROTOCOL_COMMAND` (0x002DD3C8),
which is why SMART currently needs elevation.

**There is a read-only route that does not.** On Windows 10 1903+:

```
DeviceIoControl(IOCTL_STORAGE_QUERY_PROPERTY)
  STORAGE_PROPERTY_QUERY.PropertyId = StorageDeviceProtocolSpecificProperty
  STORAGE_PROTOCOL_SPECIFIC_DATA {
      ProtocolType = ProtocolTypeNvme,
      DataType     = NVMeDataTypeLogPage,
      RequestValue = 0x02          // SMART / Health log page
  }
```
on a volume handle opened with `dwDesiredAccess = 0`. This returns the full NVMe
SMART log **without Administrator**, and lets us time the same Identify/SMART
round-trips that `last_admin_timings.json` needs.

### Replacement sources, by privilege

**No admin required:** disk model/bus/boot flag (`MSFT_PhysicalDisk`); wear %,
temperature, power-on hours, read/write error totals
(`MSFT_StorageReliabilityCounter` — **already implemented** at
`adapters/_wmi_fallback.py:12-59`, just not wired to the health cards); SSD-vs-HDD
(`StorageDeviceSeekPenaltyProperty`); full SMART (above); live throughput/queue/
latency (`psutil.disk_io_counters(perdisk=True)` deltas, or PDH
`\PhysicalDisk(*)\…` via `Get-Counter`); honest uncached read benchmark
(`FILE_FLAG_NO_BUFFERING` with sector-aligned buffers, or `diskspd -Sh`); all
NVML GPU metrics including `nvmlDeviceGetUtilizationRates`,
`nvmlDeviceGetPcieThroughput`, and real bandwidth from
`nvmlDeviceGetMemoryBusWidth × nvmlDeviceGetMaxClockInfo(…, CLOCK_MEM) × 2 / 8`.

**Admin required:** raw NVMe admin passthrough
(`IOCTL_STORAGE_PROTOCOL_COMMAND`), ETW per-IO disk tracing
(`Microsoft-Windows-Kernel-Disk`), smartctl / nvme-cli.

**Not obtainable from any sensor:** "Middleware Gain". It needs an A/B harness —
same prompt through baseline vs optimized path, tokens from the response `usage`,
wall time from `perf_counter()`. Until that exists the number should not render.

---

## 4. Endpoint status — the non-obvious ones

Most routes are real (accounts, keys, cloud shares/sessions, mesh, providers,
fallback, ratelimit, playwright smoke, deploy/ship plan storage). Worth knowing:

| Route | Status |
|---|---|
| `GET /api/health/bottleneck`, `GET /api/observe/path` | **mock, always** (§2) |
| `GET /api/health/devices` | partial — profile real, health %/path trace/gain fabricated |
| `POST /api/freeide/invoke` | returns canned instruction text and URLs; **invokes nothing** (local_mesh.py:396-449) |
| `POST /api/ship/github/connect` | prints the `gh auth login` command; performs no login |
| `POST /api/ship/library/upload-session` | creates a staging dir, but **no upload route exists** — files must be copied by hand |
| `POST /api/ship/pick-folder` | real tkinter dialog, but opens on the **server** desktop and hangs 300s if headless |
| `POST /api/deploy/one-press` | forces `simulate` for `local_demo`/`aws_guide`, and for `openship_cloud` without a token (app.py:1016-1018) |
| `POST /api/ship/engine` | clone/detect/CI/build real; **hosting step is simulated** unless a remote FreeBuild is configured (engine.py:308-340) |
| `PUT /api/ship/budget` | `spent_usd_estimate` is operator-typed; no billing API feeds it |
| `POST /v1/chat/completions` | `stream` rejected (app.py:1338); **Anthropic keys skipped** by the proxy (proxy.py:51-54); Gemini native shape unhandled |
| `GET /api/fallback/status` | exact duplicate of `GET /api/fallback` |

**Broken when configured correctly:** with `OPENSHIP_URL` + `OPENSHIP_TOKEN`
set, `execute_openship_plan` requires a `project_id`, but neither
`/api/deploy/{id}/execute` nor `/api/freebuild/{id}/execute` ever supplies one —
so it takes the error branch at `openship.py:343-350` and marks every step
`fail`. Configuring FreeBuild makes execution fail *harder* than leaving it
unconfigured, which silently simulates.
