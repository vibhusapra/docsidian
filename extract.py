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
    link: str = ""         # destination URL if this span sits under a hyperlink


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


def _span_link(span, links: list) -> str:
    """URL of the hyperlink covering this span, if any (by bbox overlap)."""
    if not links:
        return ""
    bx0, by0, bx1, by1 = span["bbox"]
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    for (rx0, ry0, rx1, ry1), uri in links:
        if rx0 <= cx <= rx1 and ry0 <= cy <= ry1:
            return uri
    return ""


def _span_run(span, links: list = ()) -> Run:
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
        link=_span_link(span, links),
    )


def _block_lines(block, links: list = ()) -> list:
    """Return structured Line objects (with styled Runs) for a text block."""
    lines = []
    for line in block.get("lines", []):
        runs = [_span_run(s, links) for s in line.get("spans", []) if s["text"]]
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


def extract_page(page, doc, img_dir: str, page_num: int, prefix: str = "") -> list[Element]:
    """Return ordered elements for one page, saving images to img_dir.

    prefix: prepended to figure filenames so images from different documents
    stay unique when their attachments are merged into one Obsidian vault.
    """
    # TEXT_DEHYPHENATE off here (we handle wraps ourselves); expand ligatures
    # so "fi"/"fl" glyphs decode to real letters instead of garbage.
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_LIGATURES
    data = page.get_text("dict", flags=flags)
    # External hyperlinks on the page → (rect, uri) for span-level matching.
    links = [(tuple(l["from"]), l["uri"]) for l in page.get_links() if l.get("uri")]
    elements: list[Element] = []
    H = page.rect.height

    # Raster image rects (tiled fragments will be merged below).
    raw_img_rects = [tuple(b["bbox"]) for b in data["blocks"] if b["type"] == 1]
    text_rects = [tuple(b["bbox"]) for b in data["blocks"] if b["type"] == 0]

    # Detect tables; cell text is pulled out so it isn't also emitted as prose.
    # A "table" that is mostly empty cells is really a line drawing (e.g. a
    # diagram) — treat its region as a figure instead of a garbled table.
    table_rects: list[tuple] = []
    fig_region_rects: list[tuple] = list(raw_img_rects)
    try:
        for tab in page.find_tables().tables:
            if tab.row_count < 2 or tab.col_count < 2:
                continue
            rows = [[(c or "").strip() for c in row] for row in tab.extract()]
            cells = [c for row in rows for c in row]
            if not cells:
                continue
            empty_ratio = sum(1 for c in cells if not c) / len(cells)
            if empty_ratio > 0.55:
                fig_region_rects.append(tuple(tab.bbox))  # line-art, not data
            else:
                table_rects.append(tuple(tab.bbox))
                elements.append(Element("table", tuple(tab.bbox), rows=rows))
    except Exception:
        pass

    # Vector-drawn figures (common in papers): cluster substantial drawing ops
    # into regions, keep those that look like graphics rather than text/rules.
    fig_region_rects += _vector_figure_rects(page, text_rects)

    figure_rects = _merge_image_rects(fig_region_rects)

    for block in data["blocks"]:
        if block["type"] != 0:
            continue
        lines = []
        for ln in _block_lines(block, links):
            if _is_chrome(ln, H):
                continue  # drop running header / page number
            if any(_rect_contains(tr, ln.bbox) for tr in table_rects):
                continue  # belongs to a real table, already captured
            if any(_rect_contains(fr, ln.bbox) for fr in figure_rects):
                continue  # label baked into a figure image — don't duplicate
            lines.append(ln)
        if lines:
            elements.append(Element("text", tuple(block["bbox"]), lines=lines))

    # Render each merged figure region to a single PNG at 2x for clarity.
    for i, rect in enumerate(figure_rects, 1):
        clip = fitz.Rect(rect)
        pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2))
        fname = f"{prefix}page{page_num:03d}_fig{i}.png"
        fpath = os.path.join(img_dir, fname)
        pix.save(fpath)
        elements.append(Element("image", rect, image_path=fpath))

    return _reading_order(elements, page.rect.width)


def _rect_overlap_area(a, b) -> float:
    ox = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    oy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ox * oy


def _vector_figure_rects(page, text_rects: list[tuple]) -> list[tuple]:
    """Find vector-drawn figures (diagrams/plots) by clustering drawing ops.

    Gated to avoid capturing rules, table borders, or text columns: a region
    qualifies only if it is a sizable 2-D block built from several drawing ops
    and is not mostly covered by text (which would make it a paragraph column).
    """
    try:
        draws = page.get_drawings()
    except Exception:
        return []
    pw, ph = page.rect.width, page.rect.height
    page_area = pw * ph
    # Keep only drawing ops with real 2-D extent (skip thin lines / underlines).
    rects = [(d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1)
             for d in draws
             if (d["rect"].x1 - d["rect"].x0) > 8 and (d["rect"].y1 - d["rect"].y0) > 8]
    if len(rects) < 3:
        return []

    out = []
    for c in _merge_image_rects(rects):
        w, h = c[2] - c[0], c[3] - c[1]
        area = w * h
        if w < 80 or h < 80 or area < 0.03 * page_area or area > 0.75 * page_area:
            continue
        members = sum(1 for r in rects if _rect_overlap_area(c, r) > 0)
        if members < 4:
            continue
        # Reject regions that are mostly text (i.e. a body column, not a figure).
        text_cover = sum(_rect_overlap_area(c, t) for t in text_rects)
        if text_cover > 0.45 * area:
            continue
        out.append(c)
    return out


def _reading_order(elements: list[Element], page_width: float) -> list[Element]:
    """Order elements as a human reads them, handling multi-column layouts.

    Single column → simple top-to-bottom. Two columns (common in papers) →
    full-width elements (titles, wide figures/tables) act as dividers; between
    them the left column is read fully, then the right column. This avoids the
    line-by-line column interleaving a naive y-sort produces.
    """
    if not elements:
        return elements
    mid = page_width / 2

    def width(e):
        return e.bbox[2] - e.bbox[0]

    def center(e):
        return (e.bbox[0] + e.bbox[2]) / 2

    narrow = [e for e in elements if width(e) < 0.55 * page_width]
    left = [e for e in narrow if center(e) < mid]
    right = [e for e in narrow if center(e) >= mid]
    two_col = len(left) >= 3 and len(right) >= 3

    if not two_col:
        return sorted(elements, key=lambda e: (round(e.bbox[1] / 3), e.bbox[0]))

    # Walk top-to-bottom; a full-width element flushes the current column pair.
    ordered: list[Element] = []
    segment: list[Element] = []

    def flush():
        l = sorted((e for e in segment if center(e) < mid), key=lambda e: e.bbox[1])
        r = sorted((e for e in segment if center(e) >= mid), key=lambda e: e.bbox[1])
        ordered.extend(l)
        ordered.extend(r)
        segment.clear()

    for e in sorted(elements, key=lambda e: e.bbox[1]):
        if width(e) > 0.55 * page_width:  # spans both columns → divider
            flush()
            ordered.append(e)
        else:
            segment.append(e)
    flush()
    return ordered


def _norm_chrome(text: str) -> str:
    """Normalize a line for repeated-header matching (drop digits & case)."""
    return re.sub(r"\d+", "", text).lower().strip()


def _strip_repeated_chrome(pages: list[list["Element"]], heights: list[float]) -> None:
    """Remove running headers/footers: short lines in the top/bottom margin band
    whose text repeats across many pages (page-number variations ignored)."""
    counts: dict[str, int] = {}
    for els, H in zip(pages, heights):
        seen = set()
        for e in els:
            if e.kind != "text":
                continue
            for ln in e.lines:
                frac = ln.bbox[1] / H if H else 0.5
                if (frac < 0.12 or frac > 0.88) and len(ln.text.strip()) < 90:
                    key = _norm_chrome(ln.text)
                    if key and key not in seen:
                        counts[key] = counts.get(key, 0) + 1
                        seen.add(key)
    if not pages:
        return
    threshold = max(3, int(0.2 * len(pages)))
    repeated = {k for k, n in counts.items() if n >= threshold}
    if not repeated:
        return
    for els, H in zip(pages, heights):
        for e in els:
            if e.kind != "text":
                continue
            e.lines = [ln for ln in e.lines
                       if not ((ln.bbox[1] / H < 0.12 or ln.bbox[1] / H > 0.88)
                               and _norm_chrome(ln.text) in repeated)]
        # drop now-empty text elements
        els[:] = [e for e in els if e.kind != "text" or e.lines]


def extract_pdf(pdf_path: str, img_dir: str, progress=None, prefix: str = "") -> list[list[Element]]:
    """Extract every page. Returns a list (per page) of element lists.

    progress: optional callback(page_index, page_count) called per page.
    prefix: namespaces saved figure filenames (for vault merging).
    """
    os.makedirs(img_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    n = doc.page_count
    pages = []
    heights = []
    for i, page in enumerate(doc):
        heights.append(page.rect.height)
        pages.append(extract_page(page, doc, img_dir, i + 1, prefix=prefix))
        if progress:
            progress(i + 1, n)
    doc.close()
    _strip_repeated_chrome(pages, heights)
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
