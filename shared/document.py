from __future__ import annotations

import re
from pathlib import Path
from typing import Generator


Chunk = tuple[str, str, int]  # (doc_path, chunk_text, chunk_index)

_SUPPORTED = {".txt", ".md", ".rst", ".csv", ".json", ".pdf", ".html", ".htm"}

# Sections shorter than this are merged into their neighbour rather than
# becoming standalone chunks. Keeps tiny cover-page items and signature
# blocks from becoming isolated, low-signal chunks.
_MIN_SECTION_CHARS = 200

# iXBRL inline wrapper tags: these wrap individual data values mid-sentence.
# We unwrap them (keep the text content, discard the tag) rather than removing.
_XBRL_UNWRAP = frozenset({"ix:nonfraction", "ix:nonnumeric", "ix:continuation"})


def load_documents(path: str | Path) -> Generator[Chunk, None, None]:
    """Yield (doc_path, chunk_text, chunk_index) for every chunk in path.

    path may be a single file or a directory; directories are walked recursively.
    Supported formats: .txt .md .rst .csv .json .pdf .html .htm
    """
    root = Path(path)
    files = sorted(root.rglob("*")) if root.is_dir() else [root]
    for file in files:
        if file.suffix.lower() not in _SUPPORTED:
            continue
        yield from _chunk_file(file)


def _chunk_file(file: Path) -> Generator[Chunk, None, None]:
    suffix = file.suffix.lower()
    if suffix == ".pdf":
        sections = _split_paragraphs(_extract_pdf(file))
    elif suffix in {".html", ".htm"}:
        sections = _extract_html_sections(file)
    else:
        sections = _split_paragraphs(
            file.read_text(encoding="utf-8", errors="replace")
        )
    for idx, section in enumerate(sections):
        yield str(file), section, idx


# ── PDF ───────────────────────────────────────────────────────────────────────

def _extract_pdf(file: Path) -> str:
    import pdfplumber

    pages = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text.strip())
    return "\n\n".join(pages)


# ── HTML / iXBRL ──────────────────────────────────────────────────────────────

def _extract_html_sections(file: Path) -> list[str]:
    """Parse an HTML or iXBRL document into a list of semantic section strings.

    ## iXBRL documents (SEC 10-K / 10-Q filings)

    SEC filings embed XBRL financial data inline in the HTML using two kinds
    of tags that must be handled differently:

    ### Metadata blocks — decompose entirely
    `<ix:header>`, `<ix:hidden>`, and all remaining namespace-prefixed tags
    (`xbrli:*`, `xbrldi:*`, `i:*`, `link:*`, `us-gaap:*`, etc.) contain only
    XBRL schema context, identifiers, and URIs.  They carry no readable text
    and produce garbage output if passed to get_text().

    ### Inline value wrappers — unwrap, keep text
    `<ix:nonfraction>`, `<ix:nonnumeric>`, and `<ix:continuation>` wrap
    individual numeric or text values that appear mid-sentence in the filing.
    Removing them would delete the data values; instead we unwrap them so the
    text content is preserved in the surrounding flow.

    ## Section boundaries
    SEC iXBRL filings use `<hr>` tags as section dividers rather than heading
    tags.  Splitting on `<hr>` yields 50–70 semantically coherent sections per
    document (one per financial statement, note, MD&A subsection, risk factor
    block, etc.) rather than hundreds of paragraph-level fragments mixed with
    XBRL noise.

    ## Table rendering
    Financial tables are rendered as pipe-delimited rows ("Revenue | $143B |
    $148B") instead of letting get_text() concatenate all cells without any
    delimiter, which makes the data unreadable.

    ## Cleanup
    - The "Table of Contents" navigation link that appears at the top of every
      section in the filing is stripped as an artifact.
    - Sections shorter than _MIN_SECTION_CHARS are merged into the next section
      so that short cover-page items and signature blocks do not become isolated
      low-signal chunks.
    """
    from bs4 import BeautifulSoup, NavigableString

    raw = file.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "html.parser")

    # Step 1a: decompose ix:header and ix:hidden first — they are the top-level
    # containers for all XBRL metadata and their children include xbrli:context,
    # xbrli:unit, link:schemaref, etc.  Removing them also removes their children
    # in one pass, which is more efficient than finding every descendant.
    for tag in soup.find_all(["ix:header", "ix:hidden"]):
        tag.decompose()

    # Step 1b: decompose any remaining namespace-prefixed tags that are not
    # inline wrappers.  These are residual XBRL elements (e.g. xbrldi:*
    # dimension members, i:* short-form contexts used by some filers) that sit
    # outside the ix:header block.
    for tag in soup.find_all(True):
        name = tag.name or ""
        if ":" in name and name not in _XBRL_UNWRAP:
            tag.decompose()

    # Step 2: unwrap inline XBRL value tags.  .unwrap() replaces the tag with
    # its children in the tree, leaving the numeric/text value in place.
    for tag in soup.find_all(list(_XBRL_UNWRAP)):
        tag.unwrap()

    # Remove standard noise tags that never contribute readable content.
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()

    # Step 3: split on <hr> tags to get semantic sections.
    body = soup.find("body") or soup
    raw_sections: list[list] = []
    current: list = []
    for elem in body.children:
        if hasattr(elem, "name") and elem.name == "hr":
            if current:
                raw_sections.append(current[:])
            current = []
        else:
            current.append(elem)
    if current:
        raw_sections.append(current)

    # Step 4: render each section to a plain-text string.
    rendered: list[str] = []
    for elems in raw_sections:
        parts: list[str] = []
        for elem in elems:
            _collect_text(elem, parts)
        text = "\n".join(parts).strip()
        # Step 5: strip the "Table of Contents" navigation artifact that the
        # filing template injects at the top of every section.
        text = re.sub(r"^Table\s+of\s+Contents\s*\n?", "", text, flags=re.IGNORECASE).strip()
        if text:
            rendered.append(text)

    # Step 6: merge undersized sections into the following section.
    merged: list[str] = []
    pending = ""
    for section in rendered:
        if pending:
            section = pending + "\n\n" + section
            pending = ""
        if len(section) < _MIN_SECTION_CHARS:
            pending = section
        else:
            merged.append(section)
    if pending:
        if merged:
            merged[-1] += "\n\n" + pending
        else:
            merged.append(pending)

    return merged


def _collect_text(node, parts: list[str]) -> None:
    """Recursively collect text from a BS4 node, rendering tables structurally."""
    from bs4 import NavigableString

    if isinstance(node, NavigableString):
        t = str(node).strip()
        if t:
            parts.append(t)
    elif hasattr(node, "name"):
        if node.name == "table":
            rendered = _render_table(node)
            if rendered:
                parts.append(rendered)
        elif node.name == "br":
            parts.append("")
        else:
            for child in node.children:
                _collect_text(child, parts)


def _render_table(table) -> str:
    """Render an HTML table as pipe-delimited text rows."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [
            td.get_text(separator=" ", strip=True)
            for td in tr.find_all(["td", "th"])
        ]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


# ── Plain text ────────────────────────────────────────────────────────────────

def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines; discard empty chunks."""
    paragraphs = re.split(r"\n{2,}", text.strip())
    return [p.strip() for p in paragraphs if p.strip()]
