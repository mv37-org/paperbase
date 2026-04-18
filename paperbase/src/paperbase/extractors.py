"""Extraction pipeline for converting papers into normalized artifacts."""

from __future__ import annotations

import io
import json
import re
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import shorten

import requests
from bs4 import BeautifulSoup

from .models import PaperMetadata
from .utils import csv_bytes, make_slug, slugify

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "paperbase/0.1.0 (paper extractor)"})


@dataclass(slots=True)
class RawArtifact:
    path: str
    data: bytes
    content_type: str | None = None


@dataclass(slots=True)
class SectionArtifact:
    title: str
    slug: str
    markdown: str


@dataclass(slots=True)
class FigureArtifact:
    figure_id: str
    caption: str
    file_extension: str
    content_type: str | None
    image_bytes: bytes | None = None
    source_url: str | None = None


@dataclass(slots=True)
class TableArtifact:
    table_id: str
    caption: str | None
    rows: list[list[str]]

    def json_payload(self) -> list[dict[str, str]]:
        if not self.rows:
            return []
        header = self.rows[0]
        records: list[dict[str, str]] = []
        for row in self.rows[1:]:
            padded = row + [""] * max(0, len(header) - len(row))
            records.append({header[index]: padded[index] for index in range(len(header))})
        return records


@dataclass(slots=True)
class ExtractionOutput:
    source_type: str
    text_markdown: str
    sections: list[SectionArtifact] = field(default_factory=list)
    figures: list[FigureArtifact] = field(default_factory=list)
    tables: list[TableArtifact] = field(default_factory=list)
    raw_artifacts: list[RawArtifact] = field(default_factory=list)


def fetch_html(arxiv_id: str) -> str | None:
    response = SESSION.get(f"https://arxiv.org/html/{arxiv_id}", timeout=30)
    if response.status_code == 200 and "<html" in response.text[:1000].lower():
        return response.text
    return None


def fetch_source(arxiv_id: str) -> bytes | None:
    response = SESSION.get(
        f"https://arxiv.org/e-print/{arxiv_id}",
        timeout=30,
        headers={"Accept": "application/x-eprint-tar"},
    )
    if response.status_code == 200:
        return response.content
    return None


def fetch_pdf(arxiv_id: str, pdf_url: str | None = None) -> bytes | None:
    response = SESSION.get(pdf_url or f"https://arxiv.org/pdf/{arxiv_id}.pdf", timeout=30)
    if response.status_code == 200:
        return response.content
    return None


def _clean_html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    filtered = [line for line in lines if line]
    merged: list[str] = []
    for line in filtered:
        if not merged or len(line) < 3:
            merged.append(line)
        elif len(merged[-1]) < 60 and not merged[-1].endswith((".", ":", "!", "?")):
            merged[-1] += f" {line}"
        else:
            merged.append(line)
    return "\n\n".join(merged)


def _extract_sections(markdown: str) -> list[SectionArtifact]:
    section_pattern = re.compile(
        r"^(?:\d+\.?\s+)?(Abstract|Introduction|Related Work|Background|"
        r"Methodology|Method|Methods|Approach|Model|Architecture|"
        r"Experiments?|Results?|Evaluation|Analysis|Discussion|"
        r"Conclusion|Conclusions|Limitations|Future Work|"
        r"Acknowledgments?|Acknowledgements?|References|Appendix|"
        r"Supplementary|Training|Datasets?|Implementation|Setup|"
        r"Ablation|Broader Impact|Ethics)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(section_pattern.finditer(markdown))
    if not matches:
        return [SectionArtifact(title="Full Text", slug="full-text", markdown=markdown)]
    sections: list[SectionArtifact] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        title = re.sub(r"^\d+\.?\s*", "", match.group(1)).strip().title()
        sections.append(
            SectionArtifact(
                title=title,
                slug=make_slug(title, seen),
                markdown=markdown[start:end].strip(),
            )
        )
    return sections


def _download_image(url: str) -> tuple[bytes | None, str | None, str]:
    response = SESSION.get(url, timeout=30)
    if response.status_code != 200:
        return None, None, "bin"
    content_type = response.headers.get("content-type")
    extension = "png"
    if content_type:
        if "jpeg" in content_type or "jpg" in content_type:
            extension = "jpg"
        elif "svg" in content_type:
            extension = "svg"
        elif "gif" in content_type:
            extension = "gif"
        elif "webp" in content_type:
            extension = "webp"
    return response.content, content_type, extension


def _extract_figures_from_html(html: str) -> list[FigureArtifact]:
    soup = BeautifulSoup(html, "html.parser")
    figures: list[FigureArtifact] = []
    for index, figure in enumerate(soup.find_all("figure"), start=1):
        image = figure.find("img")
        if image is None or not image.get("src"):
            continue
        source_url = image["src"]
        if source_url.startswith("/"):
            source_url = f"https://arxiv.org{source_url}"
        elif not source_url.startswith("http"):
            source_url = f"https://arxiv.org/html/{source_url}"
        caption_tag = figure.find("figcaption")
        caption = caption_tag.get_text(" ", strip=True) if caption_tag else ""
        image_bytes, content_type, extension = _download_image(source_url)
        figures.append(
            FigureArtifact(
                figure_id=f"figure-{index}",
                caption=caption[:1000],
                file_extension=extension,
                content_type=content_type,
                image_bytes=image_bytes,
                source_url=source_url,
            )
        )
    return figures


def _extract_tables_from_html(html: str) -> list[TableArtifact]:
    soup = BeautifulSoup(html, "html.parser")
    tables: list[TableArtifact] = []
    for index, table in enumerate(soup.find_all("table"), start=1):
        rows: list[list[str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if cells:
                rows.append([cell.get_text(" ", strip=True) for cell in cells])
        if not rows:
            continue
        caption_tag = table.find("caption")
        tables.append(
            TableArtifact(
                table_id=f"table-{index}",
                caption=caption_tag.get_text(" ", strip=True)[:1000] if caption_tag else None,
                rows=rows,
            )
        )
    return tables


def _extract_latex_text(source_bytes: bytes) -> str | None:
    try:
        if source_bytes[:2] == b"\x1f\x8b" or source_bytes[:4] == b"PK\x03\x04":
            file_obj = io.BytesIO(source_bytes)
            try:
                with tarfile.open(fileobj=file_obj, mode="r:gz") as archive:
                    tex_files = [member for member in archive.getmembers() if member.name.endswith(".tex")]
                    if not tex_files:
                        return None
                    for member in tex_files:
                        candidate = archive.extractfile(member)
                        if candidate is None:
                            continue
                        content = candidate.read().decode("utf-8", errors="replace")
                        if "\\begin{document}" in content:
                            return content
                    candidate = archive.extractfile(tex_files[0])
                    if candidate is None:
                        return None
                    return candidate.read().decode("utf-8", errors="replace")
            except tarfile.TarError:
                return None
        text = source_bytes.decode("utf-8", errors="replace")
        if "\\begin{document}" in text or "\\section" in text:
            return text
    except Exception:
        return None
    return None


def _latex_to_markdown(latex: str) -> str:
    text = re.sub(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", r"\nAbstract\n\1\n", latex, flags=re.DOTALL)
    text = re.sub(r"\\(?:sub)*section\*?\{([^}]*)\}", r"\n\1\n", text)
    text = re.sub(r"\\paragraph\*?\{([^}]*)\}", r"\n\1\n", text)
    text = re.sub(r"\\(?:text(?:bf|it|tt|rm|sf)|emph|textsc)\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:cite|citep|citet|ref|eqref|label)\{[^}]*\}", "", text)
    text = re.sub(
        r"\\(?:begin|end)\{(?:equation|align|figure|table|tabular|itemize|enumerate|proof|theorem|lemma|definition|corollary|remark|example|algorithm|algorithmic|lstlisting)\*?\}",
        "\n",
        text,
    )
    text = re.sub(r"\\item\s*", "\n- ", text)
    text = re.sub(r"\\\\", "\n", text)
    text = re.sub(r"\\\w+\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\\w+\b", "", text)
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"\$+([^$]+)\$+", r"\1", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n\n".join(lines)


def _docling_export_dict(document: object) -> dict:
    if hasattr(document, "export_to_dict"):
        return document.export_to_dict()
    if hasattr(document, "export_to_json"):
        exported = document.export_to_json()
        return json.loads(exported) if isinstance(exported, str) else exported
    return {}


def _scan_docling_tables(payload: object, tables: list[TableArtifact]) -> None:
    if isinstance(payload, dict):
        lower_values = " ".join(str(value) for value in payload.values()).lower()
        if "table" in lower_values:
            rows = payload.get("rows") or payload.get("data") or payload.get("cells")
            if isinstance(rows, list) and rows and all(isinstance(row, list) for row in rows):
                table_id = payload.get("id") or f"table-{len(tables) + 1}"
                caption = payload.get("caption")
                tables.append(TableArtifact(table_id=slugify(str(table_id)), caption=caption, rows=rows))
        for value in payload.values():
            _scan_docling_tables(value, tables)
    elif isinstance(payload, list):
        for item in payload:
            _scan_docling_tables(item, tables)


def extract_with_docling(pdf_bytes: bytes) -> ExtractionOutput | None:
    try:
        from docling.document_converter import DocumentConverter
    except Exception:
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as temp_file:
        temp_file.write(pdf_bytes)
        temp_file.flush()
        converter = DocumentConverter()
        result = converter.convert(temp_file.name)
        document = result.document
        markdown = document.export_to_markdown()
        payload = _docling_export_dict(document)
    tables: list[TableArtifact] = []
    _scan_docling_tables(payload, tables)
    return ExtractionOutput(
        source_type="pdf-docling",
        text_markdown=markdown,
        sections=_extract_sections(markdown),
        tables=tables,
        raw_artifacts=[RawArtifact(path="raw/paper.pdf", data=pdf_bytes, content_type="application/pdf")],
    )


def index_paper_artifacts(paper: PaperMetadata) -> ExtractionOutput:
    html = fetch_html(paper.arxiv_id)
    if html:
        source_bytes = fetch_source(paper.arxiv_id)
        markdown = _clean_html_to_markdown(html)
        raw_artifacts: list[RawArtifact] = []
        if source_bytes:
            raw_artifacts.append(
                RawArtifact(
                    path="raw/source.tar.gz",
                    data=source_bytes,
                    content_type="application/gzip",
                )
            )
        return ExtractionOutput(
            source_type="html",
            text_markdown=markdown,
            sections=_extract_sections(markdown),
            figures=_extract_figures_from_html(html),
            tables=_extract_tables_from_html(html),
            raw_artifacts=raw_artifacts,
        )

    source_bytes = fetch_source(paper.arxiv_id)
    if source_bytes:
        latex = _extract_latex_text(source_bytes)
        if latex:
            markdown = _latex_to_markdown(latex)
            return ExtractionOutput(
                source_type="latex",
                text_markdown=markdown,
                sections=_extract_sections(markdown),
                raw_artifacts=[
                    RawArtifact(path="raw/source.tar.gz", data=source_bytes, content_type="application/gzip"),
                    RawArtifact(path="raw/raw.tex", data=latex.encode("utf-8"), content_type="text/x-tex"),
                ],
            )

    pdf_bytes = fetch_pdf(paper.arxiv_id, paper.pdf_url)
    if pdf_bytes:
        docling_output = extract_with_docling(pdf_bytes)
        if docling_output:
            return docling_output
        fallback_markdown = (
            f"# {paper.title}\n\n"
            f"## Abstract\n\n{paper.abstract}\n\n"
            "_PDF fallback stored because structured extraction was unavailable._"
        )
        return ExtractionOutput(
            source_type="pdf",
            text_markdown=fallback_markdown,
            sections=_extract_sections(fallback_markdown),
            raw_artifacts=[RawArtifact(path="raw/paper.pdf", data=pdf_bytes, content_type="application/pdf")],
        )

    abstract_only = f"# {paper.title}\n\n## Abstract\n\n{paper.abstract}\n"
    return ExtractionOutput(
        source_type="abstract-only",
        text_markdown=abstract_only,
        sections=[SectionArtifact(title="Abstract", slug="abstract", markdown=paper.abstract)],
    )


def build_manifest(paper_id: str, paper: PaperMetadata, extraction: ExtractionOutput) -> dict:
    figure_count = len(extraction.figures)
    table_count = len(extraction.tables)
    section_slugs = [section.slug for section in extraction.sections]
    files = {
        "meta": "meta.json",
        "manifest": "manifest.json",
        "text": "text/full.md",
        "sections": [f"sections/{section.slug}.md" for section in extraction.sections],
        "figures_index": "figures/index.json" if figure_count else None,
        "tables_index": "tables/index.json" if table_count else None,
        "raw": [artifact.path for artifact in extraction.raw_artifacts],
    }
    return {
        "paper_id": paper_id,
        "arxiv_id": paper.arxiv_id,
        "source_type": extraction.source_type,
        "index_status": "indexed",
        "available_artifacts": files,
        "section_slugs": section_slugs,
        "figure_count": figure_count,
        "table_count": table_count,
        "last_indexed_at": paper.indexed_at.isoformat() if paper.indexed_at else None,
    }


def figure_index_payload(figures: list[FigureArtifact]) -> list[dict]:
    payload = []
    for figure in figures:
        payload.append(
            {
                "figure_id": figure.figure_id,
                "caption": shorten(figure.caption, width=200, placeholder="..."),
                "source_url": figure.source_url,
                "content_type": figure.content_type,
            }
        )
    return payload


def table_index_payload(tables: list[TableArtifact]) -> list[dict]:
    payload = []
    for table in tables:
        payload.append(
            {
                "table_id": table.table_id,
                "caption": shorten(table.caption or "", width=200, placeholder="..."),
                "row_count": len(table.rows),
            }
        )
    return payload


def table_json_bytes(table: TableArtifact) -> bytes:
    return json.dumps(table.json_payload(), indent=2).encode("utf-8")


def table_csv_bytes(table: TableArtifact) -> bytes:
    return csv_bytes(table.rows)
