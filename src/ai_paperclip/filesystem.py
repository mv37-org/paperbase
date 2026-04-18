"""Paper filesystem — each paper is a directory with structured, agent-readable files.

Layout at ~/.ai-paperclip/papers/<arxiv_id>/
    meta.json        — title, authors, doi, date, abstract, categories, urls
    content.lines    — full text (line-numbered: L<n>: <text>)
    sections/        — named section files (Introduction.lines, Methods.lines, ...)
    figures/         — extracted figure references
    raw.tex          — raw LaTeX source (if available)
"""

import json
import os
import re
import tarfile
import tempfile
import io
from pathlib import Path
from textwrap import fill

import requests

from . import PAPERS_DIR
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ai-paperclip/0.1.0 (research tool)"})


def paper_dir(arxiv_id: str) -> Path:
    safe_id = arxiv_id.replace("/", "_")
    return PAPERS_DIR / safe_id


def is_pulled(arxiv_id: str) -> bool:
    d = paper_dir(arxiv_id)
    return (d / "meta.json").exists()


def write_meta(arxiv_id: str, paper: dict):
    d = paper_dir(arxiv_id)
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "arxiv_id": paper.get("arxiv_id", arxiv_id),
        "title": paper.get("title", ""),
        "authors": paper.get("authors", ""),
        "abstract": paper.get("abstract", ""),
        "categories": paper.get("categories", ""),
        "primary_category": paper.get("primary_category", ""),
        "published": paper.get("published", ""),
        "updated": paper.get("updated", ""),
        "doi": paper.get("doi"),
        "comment": paper.get("comment"),
        "journal_ref": paper.get("journal_ref"),
        "pdf_url": paper.get("pdf_url", f"https://arxiv.org/pdf/{arxiv_id}.pdf"),
        "html_url": f"https://arxiv.org/html/{arxiv_id}",
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        "citation_count": paper.get("citation_count", 0),
        "influential_citation_count": paper.get("influential_citation_count", 0),
        "code_url": paper.get("code_url"),
    }
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    return d


def _fetch_html(arxiv_id: str) -> str | None:
    url = f"https://arxiv.org/html/{arxiv_id}"
    try:
        resp = SESSION.get(url, timeout=30)
        if resp.status_code == 200 and "<html" in resp.text[:500].lower():
            return resp.text
    except requests.RequestException:
        pass
    return None


def _fetch_source(arxiv_id: str) -> bytes | None:
    url = f"https://arxiv.org/e-print/{arxiv_id}"
    try:
        resp = SESSION.get(url, timeout=30, headers={"Accept": "application/x-eprint-tar"})
        if resp.status_code == 200:
            return resp.content
    except requests.RequestException:
        pass
    return None


def _clean_html_to_text(html: str) -> str:
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL)
    html = re.sub(r"<header[^>]*>.*?</header>", "", html, flags=re.DOTALL)
    html = re.sub(r"<footer[^>]*>.*?</footer>", "", html, flags=re.DOTALL)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"<br\s*/?>", "\n", html)
    html = re.sub(r"</(p|div|li|h[1-6]|tr|blockquote)>", "\n\n", html)
    html = re.sub(r"<[^>]+>", "", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&lt;", "<", html)
    html = re.sub(r"&gt;", ">", html)
    html = re.sub(r"&#\d+;", "", html)
    html = re.sub(r"&\w+;", "", html)
    lines = [l.strip() for l in html.split("\n")]
    lines = [l for l in lines if l]
    merged = []
    for l in lines:
        if not merged or len(l) < 3:
            merged.append(l)
        elif len(merged[-1]) < 60 and not merged[-1].endswith((".", ":", "!", "?")):
            merged[-1] += " " + l
        else:
            merged.append(l)
    return "\n".join(merged)


def _extract_sections_from_text(text: str) -> dict[str, list[str]]:
    section_pattern = re.compile(
        r"^(?:\d+\.?\s+)?(Abstract|Introduction|Related Work|Background|"
        r"Methodology|Method|Methods|Approach|Model|Architecture|"
        r"Experiments?|Results?|Evaluation|Analysis|Discussion|"
        r"Conclusion|Conclusions|Limitations|Future Work|"
        r"Acknowledgments?|Acknowledgements?|References|Appendix|"
        r"Supplementary|Training|Datasets?|Implementation|Setup|"
        r"Ablation|Broader Impact|Ethics)\s*$",
        re.IGNORECASE | re.MULTILINE
    )

    sections = {}
    positions = [(m.start(), m.group().strip()) for m in section_pattern.finditer(text)]

    if not positions:
        return {"Full Text": text.split("\n")}

    if positions[0][0] > 100:
        preamble = text[:positions[0][0]].strip()
        if preamble:
            sections["Preamble"] = preamble.split("\n")

    for i, (pos, name) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        content = text[pos + len(name):end].strip()
        clean_name = re.sub(r"^\d+\.?\s*", "", name).strip().title()
        if clean_name.lower() == "references":
            content = content[:5000]
        sections[clean_name] = [l for l in content.split("\n") if l.strip()]

    return sections


def _extract_latex_text(source_bytes: bytes) -> str | None:
    try:
        if source_bytes[:2] == b'\x1f\x8b' or source_bytes[:4] == b'PK\x03\x04':
            bio = io.BytesIO(source_bytes)
            try:
                with tarfile.open(fileobj=bio, mode="r:gz") as tar:
                    tex_files = [m for m in tar.getmembers()
                                 if m.name.endswith(".tex") and not m.name.startswith(".__")]
                    if not tex_files:
                        return None
                    main = None
                    for tf in tex_files:
                        content = tar.extractfile(tf).read().decode("utf-8", errors="replace")
                        if "\\begin{document}" in content:
                            main = content
                            break
                    if not main and tex_files:
                        main = tar.extractfile(tex_files[0]).read().decode("utf-8", errors="replace")
                    return main
            except tarfile.TarError:
                try:
                    import gzip
                    decompressed = gzip.decompress(source_bytes)
                    if b"\\begin{document}" in decompressed or b"\\section" in decompressed:
                        return decompressed.decode("utf-8", errors="replace")
                except Exception:
                    pass
        else:
            text = source_bytes.decode("utf-8", errors="replace")
            if "\\begin{document}" in text or "\\section" in text:
                return text
    except Exception:
        pass
    return None


def _latex_to_plaintext(latex: str) -> str:
    text = re.sub(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", r"\nAbstract\n\1\n", latex, flags=re.DOTALL)
    text = re.sub(r"\\(?:sub)*section\*?\{([^}]*)\}", r"\n\1\n", text)
    text = re.sub(r"\\paragraph\*?\{([^}]*)\}", r"\n\1\n", text)
    text = re.sub(r"\\(?:text(?:bf|it|tt|rm|sf))\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:emph|textsc)\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:cite|citep|citet|ref|eqref|label)\{[^}]*\}", "", text)
    text = re.sub(r"\\(?:begin|end)\{(?:equation|align|figure|table|tabular|itemize|enumerate|proof|theorem|lemma|definition|corollary|remark|example|algorithm|algorithmic|lstlisting)\*?\}",
                  "\n", text)
    text = re.sub(r"\\item\s*", "\n- ", text)
    text = re.sub(r"\\\\", "\n", text)
    text = re.sub(r"\\\w+\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\\w+\b", "", text)
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"\$+([^$]+)\$+", r"\1", text)
    preamble = text.find("Abstract")
    if preamble == -1:
        doc_start = text.find("\\begin")
        if doc_start > 0:
            text = text[doc_start:]
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return "\n".join(lines)


def _extract_figure_refs(text: str) -> list[dict]:
    figures = []
    fig_pattern = re.compile(
        r"(?:Figure|Fig\.?)\s+(\d+)[.:]\s*(.*?)(?=\n\n|\nFigure|\nFig\.|\n[A-Z][a-z]+ \d|\Z)",
        re.IGNORECASE | re.DOTALL
    )
    for m in fig_pattern.finditer(text):
        caption = re.sub(r"\s+", " ", m.group(2)).strip()[:500]
        figures.append({
            "figure_number": int(m.group(1)),
            "caption": caption,
            "url": None,
        })
    return figures


def _extract_figures_from_html(html: str, arxiv_id: str) -> list[dict]:
    """Extract figure images and captions from arXiv HTML."""
    figures = []
    base_url = "https://arxiv.org/html/"

    fig_pattern = re.compile(
        r"<figure[^>]*>(.*?)</figure>", re.DOTALL | re.IGNORECASE
    )

    for fig_match in fig_pattern.finditer(html):
        fig_html = fig_match.group(1)

        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', fig_html)
        if not img_match:
            continue
        img_src = img_match.group(1)

        if not img_src.startswith("http"):
            if img_src.startswith("/"):
                img_src = f"https://arxiv.org{img_src}"
            else:
                img_src = f"{base_url}{img_src}"

        caption = ""
        cap_match = re.search(r"<figcaption[^>]*>(.*?)</figcaption>", fig_html, re.DOTALL)
        if cap_match:
            caption = re.sub(r"<[^>]+>", "", cap_match.group(1))
            caption = re.sub(r"\s+", " ", caption).strip()[:500]

        fig_num = len(figures) + 1
        num_match = re.search(r"(?:Figure|Fig\.?)\s*(\d+)", caption, re.IGNORECASE)
        if num_match:
            fig_num = int(num_match.group(1))

        figures.append({
            "figure_number": fig_num,
            "caption": caption,
            "url": img_src,
        })

    return figures


def pull_paper(arxiv_id: str, paper_meta: dict = None, force: bool = False) -> Path:
    d = paper_dir(arxiv_id)

    if is_pulled(arxiv_id) and not force:
        return d

    d.mkdir(parents=True, exist_ok=True)
    (d / "sections").mkdir(exist_ok=True)
    (d / "figures").mkdir(exist_ok=True)

    if paper_meta:
        write_meta(arxiv_id, paper_meta)

    full_text = None
    source_type = None

    html_figures = []
    html = _fetch_html(arxiv_id)
    if html:
        full_text = _clean_html_to_text(html)
        source_type = "html"
        html_figures = _extract_figures_from_html(html, arxiv_id)
    else:
        source = _fetch_source(arxiv_id)
        if source:
            latex = _extract_latex_text(source)
            if latex:
                (d / "raw.tex").write_text(latex)
                full_text = _latex_to_plaintext(latex)
                source_type = "latex"

    if not full_text and paper_meta:
        full_text = f"Title: {paper_meta.get('title', '')}\n\nAbstract\n{paper_meta.get('abstract', '')}\n\n[Full text not available — only abstract indexed]"
        source_type = "abstract-only"

    if full_text:
        lines = full_text.split("\n")
        numbered = [f"L{i+1}: {line}" for i, line in enumerate(lines)]
        (d / "content.lines").write_text("\n".join(numbered))

        sections = _extract_sections_from_text(full_text)
        for sec_name, sec_lines in sections.items():
            safe_name = re.sub(r"[^\w\s-]", "", sec_name).strip().replace(" ", "_")
            sec_path = d / "sections" / f"{safe_name}.lines"
            numbered_sec = [f"L{i+1}: {line}" for i, line in enumerate(sec_lines)]
            sec_path.write_text("\n".join(numbered_sec))

        figures = html_figures if html_figures else _extract_figure_refs(full_text)
        if figures:
            (d / "figures" / "index.json").write_text(json.dumps(figures, indent=2))

    manifest = {
        "arxiv_id": arxiv_id,
        "source_type": source_type,
        "has_full_text": source_type in ("html", "latex"),
        "sections": [f.stem for f in (d / "sections").iterdir()] if (d / "sections").exists() else [],
        "has_raw_tex": (d / "raw.tex").exists(),
    }
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2))

    return d


def list_paper_files(arxiv_id: str) -> list[str]:
    d = paper_dir(arxiv_id)
    if not d.exists():
        return []
    files = []
    for f in sorted(d.rglob("*")):
        if f.is_file():
            files.append(str(f.relative_to(d)))
    return files


def read_file(arxiv_id: str, filepath: str) -> str:
    d = paper_dir(arxiv_id)
    target = d / filepath
    if not target.exists():
        raise FileNotFoundError(f"File not found: /papers/{arxiv_id}/{filepath}")
    if not target.resolve().is_relative_to(d.resolve()):
        raise PermissionError("Path traversal not allowed")
    return target.read_text()


def head_file(arxiv_id: str, filepath: str, n: int = 40) -> str:
    content = read_file(arxiv_id, filepath)
    lines = content.split("\n")
    return "\n".join(lines[:n])


def grep_file(arxiv_id: str, filepath: str, pattern: str) -> list[str]:
    content = read_file(arxiv_id, filepath)
    regex = re.compile(pattern, re.IGNORECASE)
    return [line for line in content.split("\n") if regex.search(line)]


def search_across_papers(pattern: str, field: str = "content.lines", max_papers: int = 0) -> dict[str, list[str]]:
    regex = re.compile(pattern, re.IGNORECASE)
    results = {}
    papers_dir = PAPERS_DIR
    if not papers_dir.exists():
        return results

    count = 0
    for paper_d in sorted(papers_dir.iterdir()):
        if max_papers and count >= max_papers:
            break
        target = paper_d / field
        if target.exists():
            content = target.read_text()
            matches = [line for line in content.split("\n") if regex.search(line)]
            if matches:
                results[paper_d.name] = matches
                count += 1

    return results
