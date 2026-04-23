from __future__ import annotations

import io

import pytest
from PIL import Image

from paperbase.preview import generate_first_page_preview


def _valid_single_page_pdf(width: int = 612, height: int = 792) -> bytes:
    """Build a valid single-page PDF via Pillow's PDF writer.

    Avoids a binary test fixture and avoids pulling reportlab in as a dep.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color="white").save(buffer, format="PDF")
    return buffer.getvalue()


def test_generate_first_page_preview_returns_webp_bytes_cropped_to_top_half():
    pdf_bytes = _valid_single_page_pdf(width=612, height=792)

    image_bytes = generate_first_page_preview(pdf_bytes)

    assert image_bytes is not None
    assert image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]

    rendered = Image.open(io.BytesIO(image_bytes))
    assert rendered.format == "WEBP"
    rendered_width, rendered_height = rendered.size
    source_ratio = 612 / 792
    rendered_ratio = rendered_width / (rendered_height * 2)
    assert abs(rendered_ratio - source_ratio) < 0.02
    assert rendered_width >= 600


def test_generate_first_page_preview_returns_none_on_garbage_bytes(caplog):
    with caplog.at_level("WARNING"):
        result = generate_first_page_preview(b"not a pdf at all")

    assert result is None
    assert any("preview" in record.message.lower() for record in caplog.records)


def test_generate_first_page_preview_returns_none_on_empty_bytes():
    assert generate_first_page_preview(b"") is None


@pytest.mark.parametrize("width,height", [(612, 792), (800, 1100), (300, 400)])
def test_preview_crops_to_approximately_half_height_across_page_sizes(width, height):
    pdf_bytes = _valid_single_page_pdf(width=width, height=height)

    image_bytes = generate_first_page_preview(pdf_bytes)

    assert image_bytes is not None
    rendered = Image.open(io.BytesIO(image_bytes))
    rendered_width, rendered_height = rendered.size
    # Rendered aspect of the cropped (top-half) image should match roughly
    # 2 * source aspect, since we keep full width and halve height.
    source_aspect = width / height
    rendered_aspect = rendered_width / (rendered_height * 2)
    assert abs(rendered_aspect - source_aspect) < 0.02
