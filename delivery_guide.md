# nvme-sentinel — Delivery & Presentation Guide

> How to run the project with Cursor/Windsurf, and how to present it when the interview panel asks you to walk through it.

---

## A. Agent delivery — the exact workflow

### A.1 Folder you hand to the agent

```
nvme-sentinel/                    ← empty directory you just `mkdir`ed
├── .cursorrules                  ← paste from task.md §0
├── implementation_plan.md        ← the architectural north star
└── task.md                       ← the runbook the agent executes
```

That's it. Do NOT pre-create `pyproject.toml`, `nvme_sentinel/`, or any code — T0.1 generates all of it. Agents get confused when half the scaffolding exists.

### A.2 The golden rule

**One task = one fresh chat.** Context bleed between phases is the #1 cause of agents ignoring rules they agreed to ten minutes ago. New task → Cmd+N → paste the prompt → attach `@implementation_plan.md` + any files the task modifies → run.

### A.3 Model selection in Cursor/Windsurf

| Task type | Model | Why |
|-----------|-------|-----|
| HIGH_RISK tasks (T1.3, T4.1, T4.2, T5.1, T5.2) | Claude Opus 4.7 | Protocol-sensitive; worth the tokens |
| Everything else | Claude Sonnet 4.7 | Fast iteration |
| Code review between tasks | Claude Opus 4.7 | Better at catching subtle bugs |

In Cursor: Cmd+K → "Models" → enable both → Cmd+. inside a chat to switch.

### A.4 The verification contract

After **every** task, run the task's Verification block yourself before moving on. If it fails:

1. Paste the failing output into the same chat.
2. Say: *"This verification failed. Fix it without reverting the previous fix."*
3. If the agent makes it worse twice, **revert the diff and start the task fresh** in a new chat. Don't let a drifting agent compound errors.

### A.5 Commit discipline

After each verified task:
```bash
git add -A
git commit -m "T<phase>.<task>: <one-line summary>"
```

This gives you a perfectly-auditable history to walk the interviewer through. Example:
```
T0.1: repo skeleton, uv, pyproject
T0.2: CI matrix skeleton
T1.1: HAL enums and exception hierarchy
T1.2: StorageInterface ABC + BaseAdapter
T1.3: Pydantic SMART + Identify models (byte-accurate)
...
```

When they ask *"walk me through how you built this,"* you open `git log --oneline` and read the story.

### A.6 Estimated wall-clock

| Working pattern | Timeline |
|-----------------|----------|
| Focused 4-hour blocks, one phase per block | **3–4 days** (realistic) |
| Evenings only, 2 hours per night | **7–10 days** |
| Weekend sprint | **2 days, brutal** |

Don't sprint if you can avoid it. Subtle NVMe bugs surface 24+ hours later when the second pair of eyes hits them.

---

## B. What makes this a *winning* submission (not just a working one)

The difference between "this candidate can code" and "this candidate gets hired" in an SSD validation interview is **density of protocol-aware decisions**. Every one of these, pulled out of the codebase during the interview, is a signal:

1. **`ctypes.sizeof(NvmePassthruCmd) == 72` assertion at module load** — you've been burned by struct layout drift before. Or you've read code by someone who was.
2. **Raw ioctl primary, nvme-cli fallback, capability-detected** — you've run 24-hour soaks and know subprocess overhead adds up.
3. **Byte-accurate mock adapter seeded from captured device dumps** — you understand shift-left isn't a slogan; it's a file in `adapters/`.
4. **128-bit SMART counters handled via `int.from_bytes`, with a comment about Python's arbitrary precision** — you've translated C structs to Python before and been bitten by overflow elsewhere.
5. **`--strict-markers` + `requires_nvme` + env-var-gated collection hook** — you've seen test suites that silently skip 80% of their tests in CI and not realised.
6. **Completion Queue Entry DW0 preserved in `CommandResult`** — you read the NVMe spec, not just the nvme-cli man page.
7. **Separate structlog event for the fallback transition** — you've had to debug a test that was "flaky" only to find it was running on the fallback path undetected.
8. **PlantUML source + rendered SVG both committed** — you've had reviewers give up on docs because they couldn't render them.

Drop at least three of these into your answer to any open question. Don't recite them — weave them.

---

## C. Presenting to the interviewer (12-minute walkthrough)

### C.1 The opening line

> *"This is nvme-sentinel — a cross-platform Python framework for NVMe SSD validation. The HAL surface is eight methods, and everything above it composes. Let me show you."*

Open `docs/architecture.svg`. Point at the three adapter boxes under one ABC.

### C.2 Follow task.md §"Interview presentation flow" verbatim

It's already a 12-minute script. Rehearse it twice before the interview, once out loud.

### C.3 When they ask "what was the hardest part?"

Answer honestly — choose one of:
- *"Making the mock adapter byte-accurate. It's easy to write a mock that returns whatever makes the parser happy; the discipline was forcing the mock to replay real device dumps, which caught three parser bugs I would not have found otherwise."*
- *"Deciding between Protocol and ABC for the HAL. I went with ABC because fail-at-import beats fail-at-3am on a test bench. I wrote it up in `docs/design-decisions.md` — happy to walk through the tradeoff."*

Both are true, both show seniority, both invite the interviewer to engage on tradeoffs (which is what senior interviewers are testing for).

### C.4 When they ask "what would you do next?"

Four-item answer, in priority order:
1. **Zoned Namespaces support** — adds `zone_mgmt_send/recv` to the HAL; tests whether the abstraction is right.
2. **NVMe over Fabrics adapter** — TCP transport; same HAL, different transport layer, proves portability.
3. **Real-time SMART → Prometheus exporter** — metrics at `:9100/metrics`, feeds the existing stress/report pipeline with live data.
4. **Multi-device parallel stress orchestration** — `asyncio.gather` over per-device FioRunner instances; pins this to what real validation fleets look like.

### C.5 Red-flag answers to avoid

- *"I used `subprocess.run` because it was easier"* — says you've never profiled a test bench.
- *"I skipped the Windows adapter because the JD wanted Linux"* — the JD says **both**. Not having it is the red flag.
- *"I didn't write tests for the adapters because you can't test hardware in CI"* — the mock adapter exists precisely to refute this.
- *"I used ChatGPT to generate this"* — even if true, especially if true: don't volunteer it. If asked directly: *"I used an AI coding agent (Cursor with Claude) to accelerate implementation, with a written plan I authored and verified at every step. Happy to walk you through which decisions are mine."* Then walk them through the Design Decisions doc. That's a senior answer.

---

## D. Before you push to GitHub (the 30-minute polish pass)

Run, in order:

1. **Fresh clone sanity check**
   ```bash
   cd /tmp && git clone <your-repo> nvme-sentinel-fresh && cd nvme-sentinel-fresh
   uv sync && uv run pytest && uv run nvme-sentinel demo
   ```
   If any step fails from a fresh clone, fix it before the interview sees the repo.

2. **README front-door test** — ask a non-storage friend to open the README and tell you in 60 seconds what the project does. If they can't, simplify the elevator pitch.

3. **Git log prune** — squash noisy WIP commits (`git rebase -i`) so the history reads as 30 crisp task commits, not 300 fix-the-fix ones.

4. **Screenshots** — generate `reports/demo.html` and screenshot it into `docs/screenshots/report.png`. Embed in README. Interviewers skim.

5. **License + CONTRIBUTING** — add `LICENSE` (MIT) and `CONTRIBUTING.md` (a 30-line file). Signals you think about collaborators, not just code.

6. **Tag a release** — `git tag v0.1.0 && git push --tags`. `v0.1.0` on a repo, even a solo one, signals intent. `main` branch without a tag reads as "unfinished homework."

---

## E. If you have extra time (bonus moves that pull ahead)

Pick one. Don't do all three.

- **E.1 Docker dev container** — `.devcontainer/devcontainer.json` + Dockerfile that mounts `/dev/nvme*`, so a reviewer can `docker run -it --privileged --device=/dev/nvme0n1 nvme-sentinel` and see it run against their real device. Highest signal for a senior reviewer who has a dev machine with NVMe.

- **E.2 NVMe-MI support stub** — add a `nvme_sentinel/commands/mi.py` with a docstring sketching out the NVMe Management Interface command set. Even unimplemented, the file signals you know NVMe-MI exists.

- **E.3 Benchmark harness** — `scripts/benchmark_ioctl_vs_cli.py` that times 1000 SMART queries via both paths on the mock adapter and produces a chart. Quantifies *"subprocess latency dominates."*

---

## F. Quick reference: the file you hand the agent vs. the file you hand the interviewer

| File | For the coding agent? | For the interviewer? |
|------|----------------------|----------------------|
| `implementation_plan.md` | ✅ attach to every prompt | ✅ walk them through §2, §7 |
| `task.md` | ✅ paste prompts one at a time | ❌ internal runbook |
| `delivery_guide.md` (this file) | ❌ your notes only | ❌ your notes only |
| `.cursorrules` | ✅ in repo root | optional — shows discipline if asked |
| `docs/design-decisions.md` | ❌ | ✅ this is the key artefact |
| `docs/architecture.svg` | ❌ | ✅ open this first |
| `README.md` | ❌ | ✅ their first impression |
| `git log --oneline` | ❌ | ✅ their second impression |

---

**One final thing.** When the interviewer asks *"why did you pick this project?"* — don't say *"for the interview."* Say:

> *"Because a cross-platform NVMe HAL is the smallest project that exercises everything I'd actually do in this role — ioctl work, Windows storage APIs, protocol parsing, test automation, CI. If I'd built something smaller, we wouldn't have anything real to talk about. If I'd built something bigger, I'd have cut corners somewhere that matters."*

That's a senior answer. Good luck.