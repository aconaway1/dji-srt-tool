#!/usr/bin/env python3
"""
DJI SRT Subtitle Processor
Converts per-frame DJI telemetry SRT files into clean, readable subtitle files.

Usage:
    python dji_srt_tool.py input.SRT [output.srt]
    python dji_srt_tool.py input.SRT --scan        # just show available fields
"""

import re
import sys
import curses
import argparse
from datetime import timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────
#  SRT PARSING
# ─────────────────────────────────────────────

TIMECODE_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
FIELD_RE = re.compile(r"\[([a-zA-Z_][a-zA-Z0-9_ ]*?)\s*:\s*([^\]]+?)\s*\]")

# DJI sometimes packs multiple key:value pairs in a single bracket, e.g.
# [rel_alt: 12.34 abs_alt: 52.88]
# This regex handles that sub-structure within a bracket's value.
MULTI_FIELD_RE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*([^\s:]+)(?=\s+[a-zA-Z_]|$)")


@dataclass
class Frame:
    index: int
    start: timedelta
    end: timedelta
    fields: dict[str, str]


def parse_timecode(h, m, s, ms) -> timedelta:
    return timedelta(hours=int(h), minutes=int(m), seconds=int(s), milliseconds=int(ms))


def parse_srt(path: Path) -> list[Frame]:
    """Parse a DJI SRT file into a list of Frame objects."""
    text = path.read_text(encoding="utf-8", errors="replace")

    # Split into blocks separated by blank lines
    blocks = re.split(r"\n\s*\n", text.strip())
    frames = []

    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue

        # Line 0: index
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue

        # Line 1: timecodes
        tc_match = TIMECODE_RE.search(lines[1])
        if not tc_match:
            continue
        start = parse_timecode(*tc_match.group(1, 2, 3, 4))
        end   = parse_timecode(*tc_match.group(5, 6, 7, 8))

        # Remaining lines: telemetry text (strip font tags etc.)
        body = " ".join(lines[2:])
        body = re.sub(r"<[^>]+>", "", body)  # strip HTML tags

        # Extract all [field: value] pairs.
        # DJI sometimes packs multiple pairs in one bracket e.g.:
        #   [rel_alt: 12.34 abs_alt: 52.88]
        fields = {}
        for m in FIELD_RE.finditer(body):
            raw_key = m.group(1).strip().lower().replace(" ", "_")
            raw_val = m.group(2).strip()
            sub_matches = list(MULTI_FIELD_RE.finditer(f"{raw_key}: {raw_val}"))
            if len(sub_matches) > 1:
                for sm in sub_matches:
                    fields[sm.group(1).lower()] = sm.group(2).strip()
            else:
                fields[raw_key] = raw_val

        frames.append(Frame(index=index, start=start, end=end, fields=fields))

    return frames


def discover_fields(frames: list[Frame]) -> dict[str, list[str]]:
    """Return {field_name: [sample_value, ...]} from a frame list."""
    seen: dict[str, list[str]] = {}
    for frame in frames:
        for k, v in frame.fields.items():
            if k not in seen:
                seen[k] = []
            if len(seen[k]) < 3 and v not in seen[k]:
                seen[k].append(v)
    return seen


# ─────────────────────────────────────────────
#  CHANGE DETECTION + OUTPUT GENERATION
# ─────────────────────────────────────────────

def format_subtitle_line(fields: dict[str, str], selected: list[str], labels: dict[str, str]) -> str:
    """Format selected fields as a compact single line: Val1 | Val2 | Val3"""
    parts = []
    for key in selected:
        if key in fields:
            label = labels.get(key, "")
            val = fields[key]
            parts.append(f"{label}{val}" if label else val)
    return " | ".join(parts)


def td_to_srt(td: timedelta) -> str:
    total_ms = int(td.total_seconds() * 1000)
    ms  = total_ms % 1000
    s   = (total_ms // 1000) % 60
    m   = (total_ms // 60000) % 60
    h   = total_ms // 3600000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_output_srt(
    frames: list[Frame],
    selected_fields: list[str],
    labels: dict[str, str],
    min_interval_ms: int,
) -> str:
    """
    Walk frames, emit a new subtitle block only when:
      1. At least min_interval_ms has elapsed since the last emitted block, AND
      2. The visible content has actually changed.
    The subtitle block stays on screen until the next change or end of clip.
    """
    output_blocks = []
    last_emit_time: Optional[timedelta] = None
    last_content: Optional[str] = None
    last_start: Optional[timedelta] = None

    min_td = timedelta(milliseconds=min_interval_ms)
    pending: list[tuple[timedelta, timedelta, str]] = []  # (start, end, content)

    def flush_pending(next_start: timedelta):
        """Extend the last pending block's end time up to next_start."""
        if pending:
            s, _, c = pending[-1]
            pending[-1] = (s, next_start, c)

    for frame in frames:
        content = format_subtitle_line(frame.fields, selected_fields, labels)
        if not content:
            continue

        elapsed = (frame.start - last_emit_time) if last_emit_time is not None else min_td

        changed = content != last_content
        interval_ok = elapsed >= min_td

        if changed and interval_ok:
            flush_pending(frame.start)
            pending.append((frame.start, frame.end, content))
            last_emit_time = frame.start
            last_content = content
        else:
            # Extend current block's end time
            if pending:
                s, _, c = pending[-1]
                pending[-1] = (s, frame.end, c)

    # Build SRT text
    out_lines = []
    for i, (start, end, content) in enumerate(pending, 1):
        out_lines.append(str(i))
        out_lines.append(f"{td_to_srt(start)} --> {td_to_srt(end)}")
        out_lines.append(content)
        out_lines.append("")

    return "\n".join(out_lines)


# ─────────────────────────────────────────────
#  CURSES TUI
# ─────────────────────────────────────────────

HELP_TEXT = [
    "  ↑/↓   navigate fields",
    "  SPACE  toggle field on/off",
    "  a      select all / none",
    "  l      edit label for field",
    "  +/-    adjust min interval",
    "  ENTER  process & save",
    "  q      quit",
]

def run_tui(stdscr, frames: list[Frame], output_path: Path, default_interval_ms: int = 2000):
    """Interactive field selector TUI."""
    curses.curs_off = False
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)    # selected highlight
    curses.init_pair(2, curses.COLOR_GREEN, -1)                   # enabled checkmark
    curses.init_pair(3, curses.COLOR_YELLOW, -1)                  # interval / label
    curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLUE)    # header/footer bar
    curses.init_pair(5, curses.COLOR_CYAN, -1)                    # sample values
    curses.init_pair(6, curses.COLOR_RED, -1)                     # disabled

    field_info = discover_fields(frames)
    all_fields = list(field_info.keys())

    # State
    cursor = 0
    enabled = {k: False for k in all_fields}
    labels: dict[str, str] = {}
    interval_ms = default_interval_ms
    scroll_offset = 0
    status_msg = ""

    def draw():
        nonlocal scroll_offset
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        # ── Header ──────────────────────────────────────────────
        header = f" DJI SRT Processor  ·  {output_path.name}  ·  {len(frames)} frames "
        safe_addstr(0, 0, header.ljust(w - 1)[: w - 1], curses.color_pair(4) | curses.A_BOLD)

        # ── Interval bar ────────────────────────────────────────
        interval_str = f"  Min interval: {interval_ms:,} ms  (+/- to adjust)"
        safe_addstr(1, 0, interval_str.ljust(w - 1)[: w - 1], curses.color_pair(3))

        # ── Column headers ──────────────────────────────────────
        col_header = f"  {'':2}  {'Field':<22}  {'Label':<12}  Sample values"
        safe_addstr(2, 0, col_header[: w - 1], curses.A_UNDERLINE)

        # ── Field list ──────────────────────────────────────────
        list_top = 3
        list_bottom = h - len(HELP_TEXT) - 3
        visible_rows = list_bottom - list_top

        # Scroll to keep cursor visible
        if cursor < scroll_offset:
            scroll_offset = cursor
        elif cursor >= scroll_offset + visible_rows:
            scroll_offset = cursor - visible_rows + 1

        for row_i in range(visible_rows):
            fi = row_i + scroll_offset
            if fi >= len(all_fields):
                break
            key = all_fields[fi]
            is_cursor = (fi == cursor)
            is_on = enabled[key]
            label = labels.get(key, "")
            samples = ", ".join(field_info[key][:3])

            check = "●" if is_on else "○"
            line = f"  {check}   {key:<22}  {label:<12}  {samples}"
            line = line[:w]

            if is_cursor:
                safe_addstr(list_top + row_i, 0, line.ljust(w - 1)[: w - 1], curses.color_pair(1) | curses.A_BOLD)
            else:
                attr = curses.color_pair(2) if is_on else curses.color_pair(6)
                safe_addstr(list_top + row_i, 0, line[: w - 1], attr)

        # ── Help block ──────────────────────────────────────────
        help_top = h - len(HELP_TEXT) - 2
        divider = "─" * w
        safe_addstr(help_top, 0, divider[: w - 1])
        for i, line in enumerate(HELP_TEXT):
            safe_addstr(help_top + 1 + i, 0, line[: w - 1])

        # ── Status / footer ─────────────────────────────────────
        enabled_count = sum(1 for v in enabled.values() if v)
        footer = f"  {enabled_count} field(s) selected  {('  ' + status_msg) if status_msg else ''}"
        safe_addstr(h - 1, 0, footer.ljust(w - 1)[: w - 1], curses.color_pair(4))

        stdscr.refresh()

    def prompt_label(current_key: str) -> str:
        """Inline prompt for editing a label."""
        h, w = stdscr.getmaxyx()
        prompt = f" Label for '{current_key}' (blank = no label): "
        safe_addstr(h - 1, 0, prompt.ljust(w - 1)[: w - 1], curses.color_pair(3) | curses.A_BOLD)
        stdscr.refresh()
        curses.echo()
        curses.curs_set(1)
        try:
            val = stdscr.getstr(h - 1, len(prompt), 20).decode("utf-8").strip()
        except Exception:
            val = ""
        curses.noecho()
        curses.curs_set(0)
        return val

    curses.curs_set(0)

    def safe_addstr(y, x, text, *attrs):
        """addstr that won't crash on the last cell of the screen."""
        h, w = stdscr.getmaxyx()
        # Clamp text so it never fills the very last cell (bottom-right corner)
        max_len = (w - x - 1) if y == h - 1 else (w - x)
        text = text[:max_len]
        if not text:
            return
        try:
            if attrs:
                stdscr.addstr(y, x, text, *attrs)
            else:
                stdscr.addstr(y, x, text)
        except curses.error:
            pass

    while True:
        draw()
        key = stdscr.getch()

        if key == curses.KEY_UP:
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_DOWN:
            cursor = min(len(all_fields) - 1, cursor + 1)
        elif key == ord(" "):
            k = all_fields[cursor]
            enabled[k] = not enabled[k]
            status_msg = ""
        elif key == ord("a"):
            any_on = any(enabled.values())
            for k in all_fields:
                enabled[k] = not any_on
        elif key == ord("l"):
            k = all_fields[cursor]
            new_label = prompt_label(k)
            if new_label:
                labels[k] = new_label + ": "
            else:
                labels.pop(k, None)
            status_msg = f"Label for '{k}' set."
        elif key == ord("+") or key == ord("="):
            interval_ms = min(30000, interval_ms + 500)
        elif key == ord("-"):
            interval_ms = max(100, interval_ms - 500)
        elif key in (curses.KEY_ENTER, 10, 13):
            selected = [k for k in all_fields if enabled[k]]
            if not selected:
                status_msg = "⚠  Select at least one field first!"
                continue
            # Generate and write
            srt_text = generate_output_srt(frames, selected, labels, interval_ms)
            output_path.write_text(srt_text, encoding="utf-8")
            status_msg = f"✓ Saved {output_path} ({srt_text.count(chr(10))} lines)"
            draw()
            stdscr.getch()
            return
        elif key == ord("q"):
            return


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Convert DJI telemetry SRT files into clean subtitle files."
    )
    parser.add_argument("input", help="Input .SRT file from DJI drone")
    parser.add_argument("output", nargs="?", help="Output .srt path (default: input_clean.srt)")
    parser.add_argument("--scan", action="store_true", help="Print available fields and exit")
    parser.add_argument(
        "--interval", type=int, default=2000,
        help="Starting minimum interval in ms between subtitle updates (default: 2000)"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {input_path}...", end=" ", flush=True)
    frames = parse_srt(input_path)
    print(f"{len(frames)} frames found.")

    if not frames:
        print("No telemetry frames found. Is this a DJI SRT file?", file=sys.stderr)
        sys.exit(1)

    if args.scan:
        fields = discover_fields(frames)
        print(f"\nAvailable fields ({len(fields)}):\n")
        for k, samples in fields.items():
            print(f"  {k:<25}  e.g. {', '.join(samples)}")
        print()
        return

    output_path = Path(args.output) if args.output else input_path.with_name(
        input_path.stem + "_clean.srt"
    )

    curses.wrapper(run_tui, frames, output_path, args.interval)
    print(f"\nDone. Output: {output_path}")


if __name__ == "__main__":
    main()