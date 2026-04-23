"""First-page PDF preview rendering.

Produces a small WebP image of the top half of page 1, used as a paper thumbnail.
Renders headlessly with pypdfium2 — no browser, no system deps beyond the bundled
PDFium binary. All failures are swallowed and surfaced as ``None`` so preview
generation never aborts ingestion.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

_RENDER_SCALE = 1.5
_WEBP_QUALITY = 80


def generate_first_page_preview(pdf_bytes: bytes) -> bytes | None:
    try:
        import pypdfium2 as pdfium
        from PIL import Image  # noqa: F401  (imported for its side effect of registering WebP)
    except Exception:
        logger.warning("pypdfium2/Pillow unavailable; skipping preview generation")
        return None

    try:
        document = pdfium.PdfDocument(pdf_bytes)
        if len(document) == 0:
            return None
        page = document[0]
        pil_image = page.render(scale=_RENDER_SCALE).to_pil()
        width, height = pil_image.size
        cropped = pil_image.crop((0, 0, width, height // 2))
        buffer = io.BytesIO()
        cropped.save(buffer, format="WEBP", quality=_WEBP_QUALITY, method=4)
        return buffer.getvalue()
    except Exception:
        logger.warning("Failed to render first-page preview", exc_info=True)
        return None
