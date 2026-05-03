from pathlib import Path

from gateway.markdown_table_images import (
    has_image_segments,
    parse_markdown_table,
    replace_markdown_tables_with_media_tags,
    render_table_png,
    segment_markdown_tables,
)


def _fake_renderer(table: str, output_dir):
    return str(Path(output_dir or "/tmp") / "table.png")


def test_detects_table_and_preserves_surrounding_text(tmp_path):
    text = "Before\n\n| A | B |\n|---|---:|\n| x | 10 |\n\nAfter"

    segments = segment_markdown_tables(text, output_dir=tmp_path, renderer=_fake_renderer)

    assert has_image_segments(segments)
    assert [segment.kind for segment in segments] == ["text", "image", "text"]
    assert segments[0].content == "Before\n\n"
    assert segments[1].content == str(tmp_path / "table.png")
    assert segments[2].content == "\nAfter"


def test_ignores_tables_inside_fenced_code_blocks(tmp_path):
    text = "```\n| A | B |\n|---|---|\n| x | y |\n```"

    segments = segment_markdown_tables(text, output_dir=tmp_path, renderer=_fake_renderer)

    assert len(segments) == 1
    assert segments[0].kind == "text"
    assert segments[0].content == text


def test_renderer_failure_uses_fenced_code_fallback(tmp_path):
    def failing_renderer(table: str, output_dir):
        raise RuntimeError("boom")

    text = "| A | B |\n|---|---|\n| x | y |"

    segments = segment_markdown_tables(text, output_dir=tmp_path, renderer=failing_renderer)

    assert not has_image_segments(segments)
    assert segments[0].content.startswith("```")
    assert "| A | B |" in segments[0].content


def test_replace_tables_with_media_tags(tmp_path):
    text = "| A | B |\n|---|---|\n| x | y |"

    output = replace_markdown_tables_with_media_tags(text, output_dir=tmp_path, renderer=_fake_renderer)

    assert output == f"MEDIA:{tmp_path / 'table.png'}"


def test_parse_markdown_alignment_and_cell_cleanup():
    headers, alignments, rows = parse_markdown_table(
        "| Name | Score | Link |\n|:---|---:|:---:|\n| **Ada** | `10` | [docs](https://example.com) |"
    )

    assert headers == ["Name", "Score", "Link"]
    assert alignments == ["left", "right", "center"]
    assert rows == [["Ada", "10", "docs"]]


def test_rendered_table_uses_transparent_dark_theme(tmp_path):
    from PIL import Image

    path = render_table_png("| 項目 | 値 |\n|---|---:|\n| 牛乳 | 198 |", output_dir=tmp_path)

    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0

        opaque_pixels = []
        for y in range(image.height):
            for x in range(image.width):
                pixel = image.getpixel((x, y))
                if pixel[3] > 0:
                    opaque_pixels.append(pixel)

    assert opaque_pixels
    assert any(pixel[:3] == (255, 255, 255) for pixel in opaque_pixels)
