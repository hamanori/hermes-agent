from pathlib import Path

from gateway.markdown_table_images import (
    has_image_segments,
    parse_markdown_table,
    replace_markdown_tables_with_media_tags,
    render_table_png,
    render_table_pngs,
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


def test_replace_tables_with_multiple_media_tags(tmp_path):
    def multi_renderer(table: str, output_dir):
        return [str(tmp_path / "table-1.png"), str(tmp_path / "table-2.png")]

    text = "| A | B |\n|---|---|\n| x | y |"

    output = replace_markdown_tables_with_media_tags(text, output_dir=tmp_path, renderer=multi_renderer)

    assert output == f"MEDIA:{tmp_path / 'table-1.png'}MEDIA:{tmp_path / 'table-2.png'}"


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


def test_large_table_is_split_into_multiple_readable_images(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setenv("HERMES_DISCORD_TABLE_IMAGE_MAX_BODY_ROWS", "4")
    rows = "\n".join(f"| 店舗 | チラシ | セクション | 商品{i} | {i}円 | 1個 | - |" for i in range(9))
    table = "| 店舗 | チラシ | セクション | 商品 | 価格 | 単位 | 補足 |\n|---|---|---|---|---:|---|---|\n" + rows

    paths = render_table_pngs(table, output_dir=tmp_path)

    assert len(paths) == 3
    assert all("part" in Path(path).name for path in paths)
    for path in paths:
        with Image.open(path) as image:
            assert image.width <= 1100


def test_split_table_parts_keep_same_width_without_repeated_headers(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setenv("HERMES_DISCORD_TABLE_IMAGE_MAX_BODY_ROWS", "4")
    rows = "\n".join(
        [
            "| 店舗 | 短い | A |",
            "| 店舗 | とても長い商品名で列幅を決める値 | B |",
            "| 店舗 | 短い | C |",
            "| 店舗 | 短い | D |",
            "| 店舗 | 短い | E |",
            "| 店舗 | 短い | F |",
        ]
    )
    table = "| 店舗 | 商品 | 補足 |\n|---|---|---|\n" + rows

    paths = render_table_pngs(table, output_dir=tmp_path)

    assert len(paths) == 2
    widths = []
    heights = []
    for path in paths:
        with Image.open(path) as image:
            widths.append(image.width)
            heights.append(image.height)
    assert widths[0] == widths[1]
    assert heights[0] == heights[1]


def test_short_single_table_is_padded_to_body_row_target(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setenv("HERMES_DISCORD_TABLE_IMAGE_MAX_BODY_ROWS", "5")
    short_table = "| 商品 | 価格 |\n|---|---:|\n| 牛乳 | 198円 |"
    full_table = (
        "| 商品 | 価格 |\n|---|---:|\n"
        "| 牛乳 | 198円 |\n| 卵 | 258円 |\n| 豆腐 | 98円 |\n| 納豆 | 88円 |\n| 米 | 2980円 |"
    )

    short_path = render_table_pngs(short_table, output_dir=tmp_path / "short")[0]
    full_path = render_table_pngs(full_table, output_dir=tmp_path / "full")[0]

    with Image.open(short_path) as short_image, Image.open(full_path) as full_image:
        assert short_image.height == full_image.height
