"""Direct PDF extraction: pull text blocks and images in their original
reading-order positions. No LLM, no reflow guessing — just what's on the page.

Produces a list of ordered "elements" per page that downstream writers
(Obsidian markdown, Notion API) turn into output.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF


@dataclass
class Run:
    """A styled text span within a line."""
    text: str
    bold: bool = False
    italic: bool = False
    mono: bool = False     # monospace / code font (also the green inline-code spans)
    size: float = 0.0
    black: bool = False    # extra-heavy weight (e.g. Helvetica Black) — chapter/section labels


@dataclass
class Line:
    runs: list           # list[Run]
    bbox: tuple          # (x0, y0, x1, y1)

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def size(self) -> float:
        runs = [r for r in self.runs if r.text.strip()]
        return max((r.size for r in runs), default=0.0)

    @property
    def is_bold(self) -> bool:
        runs = [r for r in self.runs if r.text.strip()]
        if not runs:
            return False
        bold_chars = sum(len(r.text.strip()) for r in runs if r.bold or r.black)
        total = sum(len(r.text.strip()) for r in runs)
        return total > 0 and bold_chars / total > 0.6

    @property
    def is_black(self) -> bool:
        runs = [r for r in self.runs if r.text.strip()]
        return bool(runs) and all(r.black for r in runs)


@dataclass
class Element:
    kind: str          # "text" | "image" | "table"
    bbox: tuple        # (x0, y0, x1, y1) position on the page
    lines: list = field(default_factory=list)  # list[Line] for text elements
    image_path: str = ""  # for image elements (filled in once saved)
    rows: list = field(default_factory=list)   # list[list[str]] for table elements

    # convenience accessors used for sorting / heading heuristics
    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines).strip()

    @property
    def size(self) -> float:
        return max((l.size for l in self.lines), default=0.0)

    @property
    def is_bold(self) -> bool:
        return any(l.is_bold for l in self.lines)


def _span_run(span) -> Run:
    font = span["font"]
    fl = font.lower()
    flags = span["flags"]
    return Run(
        text=span["text"],
        bold=bool(flags & 16) or "bold" in fl or "blk" in fl or "black" in fl,
        italic=bool(flags & 2) or "obl" in fl or "ital" in fl,
        mono=bool(flags & 8) or "courier" in fl or "mono" in fl or "consol" in fl,
        size=round(span["size"], 1),
        black="blk" in fl or "black" in fl,
    )


def _block_lines(block) -> list:
    """Return structured Line objects (with styled Runs) for a text block."""
    lines = []
    for line in block.get("lines", []):
        runs = [_span_run(s) for s in line.get("spans", []) if s["text"]]
        if runs:
            lines.append(Line(runs=runs, bbox=tuple(line["bbox"])))
    return lines


def _rects_close(a, b, gap: float = 6.0) -> bool:
    """True if two bboxes overlap or sit within `gap` points of each other."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (bx0 > ax1 + gap or ax0 > bx1 + gap or by0 > ay1 + gap or ay0 > by1 + gap)


def _merge_image_rects(rects: list[tuple]) -> list[tuple]:
    """Cluster nearby/overlapping image rects into single figure rects.

    Tiled figures (one chart split into N image pieces) become one rect.
    """
    clusters: list[list[tuple]] = []
    for r in rects:
        placed = False
        for c in clusters:
            if any(_rects_close(r, m) for m in c):
                c.append(r)
                placed = True
                break
        if not placed:
            clusters.append([r])
    # Keep merging until stable (a rect can bridge two clusters).
    merged = []
    for c in clusters:
        xs0 = min(r[0] for r in c); ys0 = min(r[1] for r in c)
        xs1 = max(r[2] for r in c); ys1 = max(r[3] for r in c)
        merged.append((xs0, ys0, xs1, ys1))
    return merged


_PAGENUM_RE = re.compile(r"\b\d{1,3}\b")


def _is_chrome(line: Line, page_height: float) -> bool:
    """Detect running headers / page numbers in the top or bottom margin band."""
    y0 = line.bbox[1]
    y1 = line.bbox[3]
    txt = line.text.strip()
    if len(txt) > 60:
        return False
    top = y0 / page_height < 0.085
    bottom = y1 / page_height > 0.93
    if top:                      # top band: running header (page num + chapter name)
        return bool(_PAGENUM_RE.search(txt)) or len(txt) < 30
    if bottom:                   # bottom band: only strip if it's a page-number line
        return bool(_PAGENUM_RE.fullmatch(txt)) or (len(txt) < 25 and _PAGENUM_RE.search(txt))
    return False


def _rect_contains(outer, inner_bbox, pad: float = 2.0) -> bool:
    """True if inner_bbox's vertical center sits within outer rect (table region)."""
    cx = (inner_bbox[0] + inner_bbox[2]) / 2
    cy = (inner_bbox[1] + inner_bbox[3]) / 2
    return (outer[0] - pad <= cx <= outer[2] + pad) and (outer[1] - pad <= cy <= outer[3] + pad)


def extract_page(page, doc, img_dir: str, page_num: int) -> list[Element]:
    """Return ordered elements for one page, saving images to img_dir."""
    # TEXT_DEHYPHENATE off here (we handle wraps ourselves); expand ligatures
    # so "fi"/"fl" glyphs decode to real letters instead of garbage.
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_LIGATURES
    data = page.get_text("dict", flags=flags)
    elements: list[Element] = []
    H = page.rect.height

    # Collect raw image rects, then merge tiled fragments into whole figures.
    raw_img_rects = [tuple(b["bbox"]) for b in data["blocks"] if b["type"] == 1]
    figure_rects = _merge_image_rects(raw_img_rects)

    # Detect tables first; their cell text is pulled out so it isn't also
    # emitted as garbled free-flow paragraphs.
    table_rects: list[tuple] = []
    try:
        found = page.find_tables()
        for tab in found.tables:
            rows = tab.extract()
            rows = [[(c or "").strip() for c in row] for row in rows]
            if any(any(c for c in row) for row in rows):
                table_rects.append(tuple(tab.bbox))
                elements.append(Element("table", tuple(tab.bbox), rows=rows))
    except Exception:
        pass

    for block in data["blocks"]:
        if block["type"] != 0:
            continue
        lines = []
        for ln in _block_lines(block):
            if _is_chrome(ln, H):
                continue  # drop running header / page number
            if any(_rect_contains(tr, ln.bbox) for tr in table_rects):
                continue  # belongs to a table, already captured
            lines.append(ln)
        if lines:
            elements.append(Element("text", tuple(block["bbox"]), lines=lines))

    # Render each merged figure region to a single PNG at 2x for clarity.
    for i, rect in enumerate(figure_rects, 1):
        clip = fitz.Rect(rect)
        pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2))
        fname = f"page{page_num:03d}_fig{i}.png"
        fpath = os.path.join(img_dir, fname)
        pix.save(fpath)
        elements.append(Element("image", rect, image_path=fpath))

    # Sort top-to-bottom, then left-to-right, so reading order is preserved
    # even when PyMuPDF returns blocks out of order.
    elements.sort(key=lambda e: (round(e.bbox[1] / 3), e.bbox[0]))
    return elements


def extract_pdf(pdf_path: str, img_dir: str) -> list[list[Element]]:
    """Extract every page. Returns a list (per page) of element lists."""
    os.makedirs(img_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        pages.append(extract_page(page, doc, img_dir, i + 1))
    doc.close()
    return pages


if __name__ == "__main__":
    import sys

    pdf = sys.argv[1] if len(sys.argv) > 1 else "sample_page.pdf"
    out_imgs = "out/_images"
    pages = extract_pdf(pdf, out_imgs)
    for pno, els in enumerate(pages, 1):
        print(f"=== PAGE {pno} ({len(els)} elements) ===")
        for e in els:
            if e.kind == "text":
                tag = "BOLD" if e.is_bold else "text"
                preview = e.text.replace("\n", " ")[:80]
                print(f"  [{tag} sz={e.size}] {preview}")
            else:
                print(f"  [IMAGE] {e.image_path}")
