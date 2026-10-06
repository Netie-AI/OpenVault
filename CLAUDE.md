# CLAUDE.md

Claude Code reads this file. Binding rules are in [AGENTS.md](AGENTS.md).

## Full tier order

Full tier is auth, HttpGuard, `admin_token`, keys, custody, unseal, FreeRoute routing, prove-host redeploys, and #135. The order is PR Bot CLEAR, then Security YES, then PR Bot undrafts and squashes (`--match-head-commit`), then R-0003 (Estate Verify) and Gating on the merge SHA, then a prove-host redeploy only with founder GO. A head move voids the CLEAR and Security YES.

## Role

Review diffs, plan the next gate, and prioritize [PARKING_LOT.md](PARKING_LOT.md). Cursor executes the patch and pastes evidence. Do not rewrite a file Cursor is already editing.

## Code rules

- Three `uv sync` roots: the repo root, `OpenMW/`, and `Profiler/`. Use `uv run` and `uv add`. Do not use pip, poetry, or conda.
- `npm` only, in `apps/web` and `apps/shell`.
- Do not delete a test to make a build pass. If a test is wrong, explain why and ask.
- On a HIGH_RISK task, name the file paths first and wait for confirmation.
- Do not rename a public API silently.
- No `print()` for logging. Use structlog.
- `nvme_sentinel/` and `Profiler/` are the hardware-protocol library. CI runs mypy strict and coverage on `hal/` and `adapters/`. Field offsets and opcodes come from [docs/reference/nvme-sentinel-spec.md](docs/reference/nvme-sentinel-spec.md). Cite section 4 in comments. Do not invent an offset.
- Python 3.10, 3.11, and 3.12. No newer syntax. Annotate every public function. No `typing.Any` on a public signature. No unjustified type ignore.
- Flat layout: `nvme_sentinel/` at the repo root, tests in `tests/`.
- Hardware tests use `@pytest.mark.requires_nvme` and a mock path.
- Linux ioctl structs follow `<linux/nvme_ioctl.h>`. Windows structs follow `ntddstor.h` / `storport.h`. Use `ctypes.Structure` with `_pack_ = 1` where the header packs, and assert `ctypes.sizeof` at import.
- Do not call `os.system`. Do not call subprocess without `check=` and `timeout=`.
- A new top-level dependency needs the right `pyproject.toml` entry and a reason an existing dependency does not cover it.
- Comments are for protocol references and non-obvious intent.

The previous long copy, including the generated Netie block, is in [docs/archive/CLAUDE-removed-2026-10-06.md](docs/archive/CLAUDE-removed-2026-10-06.md).
