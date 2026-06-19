"""Copy the latest Cursor agent reply to the clipboard.

Default: the most recent assistant message only (one turn — avoids re-pasting
full transcript history). Use --all for the full chat export.

The VS Code task runs with cwd = workspace root, so --workspace is optional
(Path.cwd()) and the task never passes a spaced path through the shell.

Usage:
  uv run python scripts/copy_cursor_chat.py
  uv run python scripts/copy_cursor_chat.py --dry-run
  uv run python scripts/copy_cursor_chat.py --all
  uv run python scripts/copy_cursor_chat.py --list
  uv run python scripts/copy_cursor_chat.py --id <uuid>

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


def _format_latest_assistant(path: Path, *, include_tools: bool) -> str | None:
    for record in reversed(_load_records(path)):
        if record.get("role") != "assistant":
            continue
        body = _extract_message_text(record, include_tools=include_tools)
        if body:
            return body + "\n"
    return None


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
        # clip.exe expects UTF-16 LE with BOM; UTF-8 stdin becomes mojibake (e.g. ΓåÆ).
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
        description="Copy latest Cursor assistant reply (default) or full chat.",
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
    parser.add_argument(
        "--all",
        action="store_true",
        help="Copy the full transcript instead of the latest assistant message.",
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

    if args.all:
        export = _format_full_transcript(path, include_tools=args.include_tools)
    else:
        latest = _format_latest_assistant(path, include_tools=args.include_tools)
        if latest is None:
            print(f"No assistant message found in {path}", file=sys.stderr)
            return 1
        export = latest

    if args.dry_run:
        _configure_stdout_utf8()
        sys.stdout.write(export)
        return 0

    _copy_to_clipboard(export)
    mode = "full chat" if args.all else "latest reply"
    print(f"Copied {mode} from {path.parent.name} ({len(export)} chars) to clipboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
