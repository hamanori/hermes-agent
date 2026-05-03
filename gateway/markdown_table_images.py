"""Render Discord-bound Markdown pipe tables as PNG image segments.

Discord does not render GFM pipe tables. This module detects simple Markdown
pipe-table blocks outside fenced code blocks and replaces only those blocks with
local PNG image segments for Discord delivery. If rendering fails, callers get a
fenced-code fallback segment so delivery never stops because of table rendering.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal, Sequence

logger = logging.getLogger(__name__)

SegmentKind = Literal["text", "image"]
_FALSE_VALUES = {"0", "false", "no", "off"}
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)]*)\)")
_MARKDOWN_MARKERS_RE = re.compile(r"(\*\*|__|~~|`)")
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@dataclass(frozen=True)
class MarkdownTableSegment:
    kind: SegmentKind
    content: str
    source: str = ""

    @property
    def is_image(self) -> bool:
        return self.kind == "image"


def discord_table_images_enabled() -> bool:
    raw = os.getenv("HERMES_DISCORD_TABLE_IMAGES", "").strip().lower()
    return raw not in _FALSE_VALUES


def has_image_segments(segments: Sequence[MarkdownTableSegment]) -> bool:
    return any(segment.kind == "image" for segment in segments)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]

    cells: list[str] = []
    buf: list[str] = []
    escaped = False
    for char in stripped:
        if escaped:
            buf.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "|":
            cells.append("".join(buf).strip())
            buf = []
            continue
        buf.append(char)
    cells.append("".join(buf).strip())
    return cells


def _is_table_row(line: str, expected_cols: int | None = None) -> bool:
    if "|" not in line:
        return False
    cells = _split_table_row(line)
    if len(cells) < 2:
        return False
    if expected_cols is not None and len(cells) != expected_cols:
        return False
    return True


def _is_separator_row(line: str) -> bool:
    if not _TABLE_SEPARATOR_RE.match(line):
        return False
    return len(_split_table_row(line)) >= 2


def _iter_table_blocks(text: str) -> Iterable[tuple[int, int, str]]:
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)

    in_fence = False
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.rstrip("\r\n")
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue

        if i + 1 < len(lines) and _is_table_row(line) and _is_separator_row(lines[i + 1].rstrip("\r\n")):
            header_cols = len(_split_table_row(line))
            separator_cols = len(_split_table_row(lines[i + 1].rstrip("\r\n")))
            if header_cols == separator_cols and header_cols > 1:
                j = i + 2
                while j < len(lines):
                    candidate = lines[j].rstrip("\r\n")
                    if not candidate.strip():
                        break
                    if not _is_table_row(candidate, expected_cols=header_cols):
                        break
                    j += 1
                end = offsets[j] if j < len(lines) else len(text)
                start = offsets[i]
                yield start, end, text[start:end].rstrip("\r\n")
                i = j
                continue
        i += 1


def _clean_cell_text(value: str) -> str:
    text = value.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = _MARKDOWN_LINK_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MARKDOWN_MARKERS_RE.sub("", text)
    return text.strip()


def parse_markdown_table(table_markdown: str) -> tuple[list[str], list[str], list[list[str]]]:
    lines = [line.strip() for line in table_markdown.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("table requires header and separator rows")
    headers = [_clean_cell_text(cell) for cell in _split_table_row(lines[0])]
    alignments = _parse_alignments(lines[1], len(headers))
    rows = [[_clean_cell_text(cell) for cell in _split_table_row(line)] for line in lines[2:]]
    return headers, alignments, rows


def _parse_alignments(separator_line: str, column_count: int) -> list[str]:
    aligns: list[str] = []
    for cell in _split_table_row(separator_line):
        raw = cell.strip()
        left = raw.startswith(":")
        right = raw.endswith(":")
        if left and right:
            aligns.append("center")
        elif right:
            aligns.append("right")
        else:
            aligns.append("left")
    while len(aligns) < column_count:
        aligns.append("left")
    return aligns[:column_count]


def _default_output_dir() -> Path:
    configured = os.getenv("HERMES_DISCORD_TABLE_IMAGE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".hermes" / "cache" / "discord-table-images"


def render_table_png(table_markdown: str, output_dir: str | Path | None = None) -> str:
    """Render a Markdown table block to a PNG file and return its path.

    Pillow is imported lazily so non-Discord paths and fallback tests do not
    require image dependencies.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:  # pragma: no cover - depends on runtime deps
        raise RuntimeError("Pillow is required to render Markdown table images") from exc

    headers, alignments, rows = parse_markdown_table(table_markdown)
    all_rows = [headers] + rows
    font = _load_font(ImageFont, bold=False, size=26)
    header_font = _load_font(ImageFont, bold=True, size=27)
    padding_x = 18
    padding_y = 12
    outer_padding = 24
    line_gap = 6
    max_table_width = 1600
    min_col_width = 110
    max_col_width = 360

    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)

    natural_widths: list[int] = []
    for col_index in range(len(headers)):
        widest = 0
        for row_index, row in enumerate(all_rows):
            value = row[col_index] if col_index < len(row) else ""
            row_font = header_font if row_index == 0 else font
            widest = max(widest, _text_width(draw, value, row_font))
        natural_widths.append(min(max(widest + padding_x * 2, min_col_width), max_col_width))

    available = max_table_width - outer_padding * 2
    total_width = sum(natural_widths)
    if total_width > available:
        scale = available / total_width
        col_widths = [max(min_col_width, int(width * scale)) for width in natural_widths]
    else:
        col_widths = natural_widths

    wrapped_rows: list[list[list[str]]] = []
    row_heights: list[int] = []
    for row_index, row in enumerate(all_rows):
        row_font = header_font if row_index == 0 else font
        wrapped_row: list[list[str]] = []
        max_lines = 1
        for col_index, width in enumerate(col_widths):
            value = row[col_index] if col_index < len(row) else ""
            wrapped = _wrap_text_to_width(draw, value, row_font, max(width - padding_x * 2, 40))
            wrapped_row.append(wrapped)
            max_lines = max(max_lines, len(wrapped))
        wrapped_rows.append(wrapped_row)
        line_height = _line_height(draw, row_font)
        row_heights.append(padding_y * 2 + max_lines * line_height + (max_lines - 1) * line_gap)

    width = sum(col_widths) + outer_padding * 2
    height = sum(row_heights) + outer_padding * 2
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    y = outer_padding
    for row_index, wrapped_row in enumerate(wrapped_rows):
        row_height = row_heights[row_index]
        x = outer_padding
        row_font = header_font if row_index == 0 else font
        for col_index, lines in enumerate(wrapped_row):
            col_width = col_widths[col_index]
            text_y = y + padding_y
            for line in lines:
                text_width = _text_width(draw, line, row_font)
                align = alignments[col_index] if row_index != 0 else "left"
                if align == "right":
                    text_x = x + col_width - padding_x - text_width
                elif align == "center":
                    text_x = x + max((col_width - text_width) // 2, padding_x)
                else:
                    text_x = x + padding_x
                draw.text((text_x, text_y), line, font=row_font, fill="#ffffff")
                text_y += _line_height(draw, row_font) + line_gap
            x += col_width
        if row_index == 0 or row_index < len(wrapped_rows) - 1:
            line_alpha = 120 if row_index == 0 else 48
            draw.line(
                [(outer_padding, y + row_height), (width - outer_padding, y + row_height)],
                fill=(255, 255, 255, line_alpha),
                width=1,
            )
        y += row_height

    out_dir = Path(output_dir).expanduser() if output_dir is not None else _default_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(table_markdown.encode("utf-8")).hexdigest()[:10]
    path = out_dir / f"table-{int(time.time() * 1000)}-{digest}.png"
    image.save(path, format="PNG", optimize=True)
    return str(path)


def _load_font(image_font_module, *, bold: bool, size: int):
    candidates = [
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc" if bold else "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for candidate in candidates:
        try:
            if Path(candidate).exists():
                return image_font_module.truetype(candidate, size=size)
        except Exception:
            continue
    return image_font_module.load_default()


def _text_width(draw, text: str, font) -> int:
    if not text:
        return 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _line_height(draw, font) -> int:
    bbox = draw.textbbox((0, 0), "Hg", font=font)
    return max(bbox[3] - bbox[1], 24)


def _wrap_text_to_width(draw, text: str, font, max_width: int) -> list[str]:
    if not text:
        return [""]
    output: list[str] = []
    for source_line in str(text).splitlines() or [""]:
        if _text_width(draw, source_line, font) <= max_width:
            output.append(source_line)
            continue
        current = ""
        for char in source_line:
            candidate = current + char
            if current and _text_width(draw, candidate, font) > max_width:
                output.append(current)
                current = char
            else:
                current = candidate
        if current:
            output.append(current)
    return output or [""]


def segment_markdown_tables(
    text: str,
    *,
    output_dir: str | Path | None = None,
    renderer: Callable[[str, str | Path | None], str] | None = None,
    enabled: bool | None = None,
) -> list[MarkdownTableSegment]:
    if not text or "|" not in text or "-" not in text:
        return [MarkdownTableSegment("text", text)] if text else []
    if enabled is False or (enabled is None and not discord_table_images_enabled()):
        return [MarkdownTableSegment("text", text)]

    render = renderer or render_table_png
    segments: list[MarkdownTableSegment] = []
    cursor = 0
    matched = False
    for start, end, table_markdown in _iter_table_blocks(text):
        matched = True
        if start > cursor:
            segments.append(MarkdownTableSegment("text", text[cursor:start]))
        try:
            path = render(table_markdown, output_dir)
            if Path(path).suffix.lower() not in _IMAGE_EXTS:
                raise RuntimeError(f"renderer returned non-image path: {path}")
            segments.append(MarkdownTableSegment("image", str(path), source=table_markdown))
        except Exception as exc:
            logger.warning(
                "Discord Markdown table image render failed (%d chars, %d lines): %s",
                len(table_markdown),
                table_markdown.count("\n") + 1,
                exc,
            )
            segments.append(MarkdownTableSegment("text", _fenced_table_fallback(table_markdown)))
        cursor = end
    if not matched:
        return [MarkdownTableSegment("text", text)]
    if cursor < len(text):
        segments.append(MarkdownTableSegment("text", text[cursor:]))
    return _merge_adjacent_text_segments(segments)


def replace_markdown_tables_with_media_tags(
    text: str,
    *,
    output_dir: str | Path | None = None,
    renderer: Callable[[str, str | Path | None], str] | None = None,
    enabled: bool | None = None,
) -> str:
    parts: list[str] = []
    for segment in segment_markdown_tables(text, output_dir=output_dir, renderer=renderer, enabled=enabled):
        if segment.kind == "image":
            parts.append(f"MEDIA:{segment.content}")
        else:
            parts.append(segment.content)
    return "".join(parts)


def _fenced_table_fallback(table_markdown: str) -> str:
    stripped = table_markdown.strip("\n")
    return f"```\n{stripped}\n```"


def _merge_adjacent_text_segments(segments: Sequence[MarkdownTableSegment]) -> list[MarkdownTableSegment]:
    merged: list[MarkdownTableSegment] = []
    for segment in segments:
        if segment.kind == "text" and not segment.content:
            continue
        if merged and segment.kind == "text" and merged[-1].kind == "text":
            previous = merged.pop()
            merged.append(MarkdownTableSegment("text", previous.content + segment.content))
        else:
            merged.append(segment)
    return merged
