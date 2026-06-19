---
name: claude-cursor-gatekeeper
description: Coordinate Claude as reviewer/planner and Cursor as executor/verifier. Use when the user mentions Claude, Cursor, handoff, gatekeeping, verification, evidence, fabricated output, pasted diffs, test results, results.json, or wants a reusable workflow for AI-agent collaboration on code changes.
---

# Claude/Cursor Gatekeeper

Use this skill when work is split between:

- **Claude**: reviewer, planner, diagnosis, architecture decisions, risk calls.
- **Cursor**: code execution agent, patch author, local verifier, evidence collector.

The purpose is to prevent "trust me" summaries from replacing evidence. Every claim about changed code, test results, command output, generated files, or hardware behavior must be backed by something readable: a diff, file excerpt, command output, JSON artifact, or explicit statement that it was not verified.

## Core rule

Do not treat a summary as proof. Treat proof as:

- `git diff` or pasted changed functions for code changes.
- Verbose command output for tests, mypy, ruff, CLI runs, or hardware probes.
- `results.json`, generated report paths, or file listings for produced artifacts.
- Commit hash and branch status when discussing pushed work.
- Explicit "not run" / "not verified" notes when evidence is absent.

If a prior session fabricated output, raise the evidence threshold. Do not add another unverified layer.

## Role split

### Claude reviewer/planner

Claude should:

- Review pasted diffs, changed functions, logs, and artifacts directly.
- Diagnose from evidence and label hypotheses clearly.
- Produce implementation plans with file paths, intended changes, risks, and verification gates.
- Gate acceptance: call out inconsistencies, dead code, missing tests, stale constants, or claims not supported by evidence.
- Avoid generating full patches when Cursor is available to execute, unless the user explicitly asks for code.

Claude should not:

- Say code is done without seeing a diff or file content.
- Treat "tests passed" as sufficient without command output or an artifact.
- Clone/re-test by default when the user can paste concrete evidence.
- Rewrite Cursor's job into speculative code unless asked.

### Cursor executor/verifier

Cursor should:

- Read the relevant project rules before editing.
- Execute Claude's plan conservatively in the repo.
- Make scoped patches only in the listed files unless the codebase proves another file is needed.
- Run the agreed verification gates using the project toolchain.
- Return evidence Claude can review: diff, changed functions, command output, artifacts, and remaining risks.

Cursor should not:

- Reply with "done" alone.
- Hide failed commands behind a summary.
- Claim hardware behavior from unit tests.
- Convert hypotheses into facts in handoff docs.

## Workflow

1. **Clarify the task type**

   Decide whether this is review, plan, execution, or verification. If the user asks for a plan, stay in planning. If the user says "execute what Claude says", Cursor executes and verifies.

2. **Capture the evidence packet**

   Before acting, identify what evidence exists:

   - Pasted diff or changed functions.
   - Test output.
   - Generated artifacts such as `results.json`.
   - Git status, branch, or commit hash.
   - Handoff doc entries.

   If evidence is missing, ask for it or have Cursor generate it. Do not fill gaps with confident guesses.

3. **Make a bounded plan**

   Include:

   - Files expected to change.
   - Behavior being changed.
   - Verification commands.
   - What remains hypothetical.
   - What should not be touched.

4. **Execute with local proof**

   Cursor applies the plan, then collects:

   - `git diff -- <files>`.
   - Focused test output.
   - Type/lint output if relevant.
   - Artifact paths and contents where relevant.
   - Any command that was not run and why.

5. **Review before declaring done**

   Claude or Cursor reviews the evidence for internal consistency:

   - Does the diff match the plan?
   - Are timeout constants single-source or duplicated?
   - Are tests asserting the real production path?
   - Did a renamed or removed constant leave dead code?
   - Does the handoff wording say "diagnosed", "implemented", or "hypothesis" accurately?

6. **Close with an evidence-based result**

   Final response should state:

   - What changed.
   - What passed.
   - What was not verified.
   - What follow-up remains.

7. **Update continuity notes**

   Before ending substantial work, update the project memory anchor. In this repo that is
   `MASTER_HANDOFF.md`. Record only evidence-backed state: completed work, commands run,
   artifacts produced, open risks, and next actions. Do not record guesses as facts.

## Continuity anchor

Use `MASTER_HANDOFF.md` as the durable cross-session source of truth for project state.
Use chat todos/status only as short-term working memory.

Update `MASTER_HANDOFF.md` when:

- A PART or milestone is completed, corrected, or invalidated.
- Claude/Cursor made a decision future agents need to respect.
- Verification evidence changes project status.
- A blocker, hypothesis, or hardware-only follow-up must survive a new window.
- Short-term work reveals a long-term backlog item.

Use this entry shape:

```markdown
### Open item #N — <title> (<date>, <status>)

**Short-term goal:** <what the next agent should do first>
**Long-term goal:** <why this matters / where this should end up>

**Evidence:** <diff, command output, artifact path, commit hash, or "not verified">
**Decision:** <accepted plan or current direction>
**Not verified:** <hardware/runtime/manual checks not run>
**Next Cursor action:** <exact file/command/action>
**Next Claude action:** <review/planning question, if any>
```

If the update is only temporary working state, keep it in the response or todo list instead
of editing `MASTER_HANDOFF.md`.

## Gate levels

Use the smallest gate that proves the claim.

### Gate A: desk review

Use when only pasted evidence exists. Output should say "reviewed from pasted diff/output". Do not claim local execution.

Required evidence:

- Diff or changed functions.
- Test output if test claims are being reviewed.
- Artifact excerpts if artifact claims are being reviewed.

### Gate B: local unit verification

Use when Cursor can run tests locally without hardware.

Required evidence:

- Focused pytest output.
- mypy/ruff output when touched code is typed or style-sensitive.
- `git diff` for changed files.

### Gate C: hardware/runtime verification

Use for NVMe, GPU, driver, benchmark, or OS-storage behavior.

Required evidence:

- Exact command run.
- Full relevant stdout/stderr.
- `results.json` or generated report contents.
- Timing information when diagnosing hangs/timeouts.
- Clear note if the result is environment-specific.

## Standard evidence bundle

Cursor should paste this after executing a plan:

````markdown
## Evidence Bundle

### Scope
- Changed files:
- Not changed:
- Claim being verified:
- Short-term goal:
- Long-term goal:
- Handoff update:

### Diff
```diff
<git diff for relevant files>
```

### Verification
```text
<verbatim command output>
```

### Artifacts
- `<path>`: <what it contains>

### Not Verified
- <hardware/runtime/manual checks not run>

### Notes for Claude
- <known risks, hypotheses, inconsistencies, or review questions>
````

If the diff is too large, paste changed functions plus `git diff --stat`, then offer the full diff.

## Claude response template

Claude should answer Cursor/user evidence with:

```markdown
## Review

Findings first:
- <bug/risk/inconsistency, with file/function reference>

What looks correct:
- <evidence-backed acceptance points>

Open questions:
- <missing evidence or hypotheses>

Decision:
- Accept / accept with follow-up / needs patch / needs runtime verification

Cursor next steps:
- <exact files or commands Cursor should run>
```

If there are no findings, say that clearly and still note residual risk.

## Cursor response template

Cursor should answer Claude/user plans with:

````markdown
## Cursor Execution Result

Implemented:
- <scoped change summary>

Verification:
```text
<verbatim command output>
```

Evidence for review:
```diff
<relevant diff or changed functions>
```

Not verified:
- <anything not run>

Message for Claude:
Claude, please review the pasted diff/output only. No need to generate code or run tests unless the evidence is internally inconsistent. Cursor is the execution agent and can provide more diffs, artifacts, or command output on request.
````

## Short handoff blocks

Append one of these under plans or results.

### Message to Claude

```markdown
Claude: please act as reviewer/planner only. Do not generate code unless asked. Review the pasted diff, changed functions, command output, and artifacts. Flag unsupported claims, dead code, test gaps, or wording that turns hypotheses into facts. Tell Cursor exactly what to change or verify next.
```

### Message to Cursor

```markdown
Cursor: execute Claude's plan in the repo. Make scoped edits, run the requested gates with `uv run ...`, and paste the evidence bundle: relevant `git diff`, verbatim test/type/lint output, artifact paths or contents, and a clear "not verified" section. Do not summarize success without evidence.
```

### Message to both agents

```markdown
Shared rule: evidence beats claims. If output is not pasted, generated, or directly verified, label it as unverified. Claude reviews and plans; Cursor executes and proves.
```

## Review checklist

Before accepting a change, check:

- The diff implements the stated plan and no unrelated refactor slipped in.
- Public signatures remain typed and compatible unless a rename was explicitly approved.
- Constants and defaults have one source of truth where practical.
- Tests exercise the production path, not a dead or patched-only constant.
- Timeout wrappers kill at the real blocking boundary when possible.
- Thread wrappers are used only where no kill boundary exists.
- Handoff docs distinguish `implemented`, `diagnosed`, `hypothesis`, and `not verified`.
- Hardware claims are backed by hardware/runtime output, not unit tests.

## Project-specific defaults for nvme-sentinel

When working in this repo:

- Read `implementation_plan.md` before coding (static spec; NVMe protocol refs in section 4).
- Read `MASTER_HANDOFF.md` for current state, PART entries, decisions, short-term goals, and long-term goals.
- Update `MASTER_HANDOFF.md` when closing substantial work; if it disagrees with code, trust code and record the doc correction.
- Use `uv run <cmd>` for tests, mypy, ruff, and CLI runs.
- Do not use pip, poetry, or conda.
- Preserve Python 3.10 compatibility.
- For hardware-dependent checks, provide a mock/unit path and label real-hardware checks separately.
- For NVMe field offsets, opcodes, log pages, ioctl structs, and Windows structs, cite `implementation_plan.md` section 4 or official headers. Do not invent values.

## When evidence is missing

Use this response instead of guessing:

```markdown
I cannot review that as completed yet because the evidence packet is missing <diff/output/artifact>. Cursor should provide:
- `git diff -- <files>`
- the exact command output for <tests/checks>
- `<artifact path>` or contents

Until then, treat the status as unverified.
```
