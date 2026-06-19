"""Copy Cursor agent output to the clipboard for Claude handoff.

Default: all assistant text after the last Read tool anchor (prefers
nvme_sentinel/cli.py offset 160 when present), through end of transcript.
Skips tool-only records and [REDACTED] stubs.

Modes:
  (default)  since last Read anchor → end
  --latest     single most recent assistant message
  --turn       all assistant messages since last user message
  --all        full transcript

Usage:
  uv run python scripts/copy_cursor_chat.py
  uv run python scripts/copy_cursor_chat.py --dry-run
  uv run python scripts/copy_cursor_chat.py --latest
  uv run python scripts/copy_cursor_chat.py --all

Bind via .vscode/tasks.json label "Copy Cursor Chat" + Ctrl+Shift+Alt+C.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def _cursor_project_slug(workspace: Path) -> str:
    resolved = workspace.resolve()
    drive = resolved.drive.rstrip(":").lower()
    rest = resolved.relative_to(resolved.anchor)
    rest_slug = str(rest).replace("\\", "-").replace("/", "-").replace(" ", "-")
    return f"{drive}-{rest_slug}"


def _transcripts_dir(workspace: Path) -> Path:
    slug = _cursor_project_slug(workspace)
    return Path.home() / ".cursor" / "projects" / slug / "agent-transcripts"


def _list_top_level_transcripts(transcripts_dir: Path) -> list[Path]:
    if not transcripts_dir.is_dir():
        return []
    files: list[Path] = []
    for child in transcripts_dir.iterdir():
        if not child.is_dir() or child.name == "subagents":
            continue
        candidate = child / f"{child.name}.jsonl"
        if candidate.is_file():
            files.append(candidate)
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _strip_user_query(text: str) -> str:
    text = re.sub(r"^<user_query>\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*</user_query>\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _normalize_text(text: str) -> str:
    """Repair common UTF-8-as-CP1252 mojibake and normalize punctuation."""
    fixes = (
        ("ΓåÆ", "→"),
        ("ΓÇö", "—"),
        ("ΓÇô", "–"),
        ("ΓÇÖ", "'"),
        ("ΓÇ£", '"'),
        ("ΓÇ¥", '"'),
        ("ΓÇª", "…"),
    )
    for bad, good in fixes:
        text = text.replace(bad, good)
    return text


def _path_endswith(path_value: object, suffix: str) -> bool:
    if not isinstance(path_value, str):
        return False
    normalized = path_value.replace("\\", "/").lower()
    return normalized.endswith(suffix.lower())


def _read_tool_matches(
    block: dict[str, object],
    *,
    path_suffix: str | None,
    offset: int | None,
) -> bool:
    if block.get("type") != "tool_use" or block.get("name") != "Read":
        return False
    raw_input = block.get("input")
    if not isinstance(raw_input, dict):
        return False
    if path_suffix is not None and not _path_endswith(raw_input.get("path"), path_suffix):
        return False
    if offset is not None:
        block_offset = raw_input.get("offset")
        if not isinstance(block_offset, int) or block_offset != offset:
            return False
    return True


def _record_has_read_tool(
    record: dict[str, object],
    *,
    path_suffix: str | None = None,
    offset: int | None = None,
) -> bool:
    message = record.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, dict) and _read_tool_matches(
            block,
            path_suffix=path_suffix,
            offset=offset,
        ):
            return True
    return False


def _find_read_anchor_index(
    records: list[dict[str, object]],
    *,
    path_suffix: str | None,
    offset: int | None,
) -> int | None:
    """Return index of the last matching Read tool anchor, or any Read, or None."""
    for i in range(len(records) - 1, -1, -1):
        if records[i].get("role") != "assistant":
            continue
        if _record_has_read_tool(records[i], path_suffix=path_suffix, offset=offset):
            return i
    for i in range(len(records) - 1, -1, -1):
        if records[i].get("role") != "assistant":
            continue
        if _record_has_read_tool(records[i], path_suffix=None, offset=None):
            return i
    return None


def _extract_message_text(
    record: dict[str, object],
    *,
    include_tools: bool,
) -> str | None:
    role = record.get("role")
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip() and text.strip() != "[REDACTED]":
                parts.append(text.strip())
        elif include_tools and block_type == "tool_use":
            name = block.get("name", "tool")
            parts.append(f"[tool: {name}]")

    if not parts:
        return None
    body = "\n".join(parts)
    if role == "user":
        body = _strip_user_query(body)
    body = re.sub(r"\n*\[REDACTED\]\s*$", "", body, flags=re.IGNORECASE)
    body = _normalize_text(body.strip())
    return body or None


def _load_records(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def _last_user_index(records: list[dict[str, object]]) -> int | None:
    last_user = -1
    for i, record in enumerate(records):
        if record.get("role") == "user":
            last_user = i
    return last_user if last_user >= 0 else None


def _collect_assistant_text(
    records: list[dict[str, object]],
    start: int,
    end: int | None,
    *,
    include_tools: bool,
) -> list[str]:
    bodies: list[str] = []
    limit = end if end is not None else len(records)
    for record in records[start:limit]:
        if record.get("role") != "assistant":
            continue
        body = _extract_message_text(record, include_tools=include_tools)
        if body:
            bodies.append(body)
    return bodies


def _format_latest_assistant(
    records: list[dict[str, object]],
    *,
    include_tools: bool,
) -> str | None:
    for record in reversed(records):
        if record.get("role") != "assistant":
            continue
        body = _extract_message_text(record, include_tools=include_tools)
        if body:
            return body + "\n"
    return None


def _format_since_last_user(
    records: list[dict[str, object]],
    *,
    include_tools: bool,
) -> str | None:
    last_user = _last_user_index(records)
    if last_user is None:
        return None
    bodies = _collect_assistant_text(
        records,
        last_user + 1,
        None,
        include_tools=include_tools,
    )
    if not bodies:
        return None
    return "\n\n".join(bodies) + "\n"


def _format_since_read_anchor(
    records: list[dict[str, object]],
    *,
    path_suffix: str,
    offset: int,
    through_end: bool,
    include_tools: bool,
) -> str | None:
    anchor = _find_read_anchor_index(records, path_suffix=path_suffix, offset=offset)
    if anchor is None:
        return _format_since_last_user(records, include_tools=include_tools)
    end = None if through_end else _last_user_index(records)
    bodies = _collect_assistant_text(
        records,
        anchor + 1,
        end,
        include_tools=include_tools,
    )
    if not bodies:
        return _format_latest_assistant(records, include_tools=include_tools)
    return "\n\n".join(bodies) + "\n"


def _format_full_transcript(path: Path, *, include_tools: bool) -> str:
    sections: list[str] = []
    for record in _load_records(path):
        role = record.get("role")
        if role not in {"user", "assistant"}:
            continue
        body = _extract_message_text(record, include_tools=include_tools)
        if body:
            label = "User" if role == "user" else "Assistant"
            sections.append(f"=== {label} ===\n{body}")

    header = f"# Cursor chat export\n# source: {path}\n\n"
    return header + "\n\n".join(sections) + "\n"


def _copy_to_clipboard(text: str) -> None:
    text = _normalize_text(text)
    if sys.platform == "win32":
        payload = b"\xff\xfe" + text.encode("utf-16-le")
        subprocess.run(
            ["clip"],
            input=payload,
            check=True,
            timeout=30,
        )
        return
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text, text=True, encoding="utf-8", check=True, timeout=30)
        return
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )


def _configure_stdout_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy Cursor assistant output for Claude handoff.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace root (default: current working directory).",
    )
    parser.add_argument(
        "--id",
        help="Transcript UUID. Default: most recently updated chat in this workspace.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available transcript IDs (newest first) and exit.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--latest",
        action="store_true",
        help="Copy only the single most recent assistant message.",
    )
    mode.add_argument(
        "--turn",
        action="store_true",
        help="Copy all assistant messages since the last user message.",
    )
    mode.add_argument(
        "--all",
        action="store_true",
        help="Copy the full transcript.",
    )
    parser.add_argument(
        "--anchor-path",
        default="nvme_sentinel/cli.py",
        help="Preferred Read anchor path suffix (default: nvme_sentinel/cli.py).",
    )
    parser.add_argument(
        "--anchor-offset",
        type=int,
        default=160,
        help="Preferred Read anchor line offset (default: 160).",
    )
    parser.add_argument(
        "--through-end",
        action="store_true",
        help="Include assistant output after the last user message (default: stop before it).",
    )
    parser.add_argument(
        "--include-tools",
        action="store_true",
        help="Include one-line [tool: ...] markers for assistant tool calls.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print export to stdout instead of copying.",
    )
    args = parser.parse_args()

    workspace = args.workspace if args.workspace is not None else Path.cwd()
    transcripts_dir = _transcripts_dir(workspace)
    transcripts = _list_top_level_transcripts(transcripts_dir)
    if not transcripts:
        print(f"No transcripts found under {transcripts_dir}", file=sys.stderr)
        return 1

    if args.list:
        for path in transcripts:
            print(path.parent.name)
        return 0

    if args.id:
        path = transcripts_dir / args.id / f"{args.id}.jsonl"
        if not path.is_file():
            print(f"Transcript not found: {path}", file=sys.stderr)
            return 1
    else:
        path = transcripts[0]

    records = _load_records(path)

    if args.all:
        export = _format_full_transcript(path, include_tools=args.include_tools)
        mode_label = "full chat"
    elif args.latest:
        latest = _format_latest_assistant(records, include_tools=args.include_tools)
        if latest is None:
            print(f"No assistant message found in {path}", file=sys.stderr)
            return 1
        export = latest
        mode_label = "latest reply"
    elif args.turn:
        turn = _format_since_last_user(records, include_tools=args.include_tools)
        if turn is None:
            print(f"No assistant turn found in {path}", file=sys.stderr)
            return 1
        export = turn
        mode_label = "current turn"
    else:
        anchored = _format_since_read_anchor(
            records,
            path_suffix=args.anchor_path,
            offset=args.anchor_offset,
            through_end=args.through_end,
            include_tools=args.include_tools,
        )
        if anchored is None:
            print(f"No anchored assistant output found in {path}", file=sys.stderr)
            return 1
        export = anchored
        mode_label = "since Read anchor"

    if args.dry_run:
        _configure_stdout_utf8()
        sys.stdout.write(export)
        return 0

    _copy_to_clipboard(export)
    print(f"Copied {mode_label} from {path.parent.name} ({len(export)} chars) to clipboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
