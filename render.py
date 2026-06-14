"""Turn ordered page Elements into clean Markdown blocks.

Reassembles wrapped lines into paragraphs, de-hyphenates line breaks,
detects bullet lists, and promotes large-font lines to headings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from extract import Element, Run

BULLET_RE = re.compile(r"^\s*[•·▪‣◦\-\*]\s+")

# Some PDFs have broken ToUnicode maps where ligature glyphs (fi, fl, ff...)
# decode to a stray char like "!". We only repair when the substitution turns
# a non-word into a real dictionary word, so genuine punctuation is never touched.
_LIGATURES = ["fi", "fl", "ff", "ffi", "ffl", "ft"]


def _load_words() -> set[str]:
    try:
        with open("/usr/share/dict/words") as f:
            return {w.strip().lower() for w in f if w.strip()}
    except OSError:
        return set()


_WORDS = _load_words()
# Characters seen standing in for a dropped ligature in broken fonts.
_LIG_STANDIN = re.compile(r"[!¡]")


def repair_ligatures(text: str) -> str:
    """Fix broken-ligature artifacts like 'than ! ve' -> 'than five'."""
    if not _WORDS or not _LIG_STANDIN.search(text):
        return text

    def fix_token(tok: str) -> str:
        # Strip the stray char (and any space it absorbed) and try to rebuild a word.
        core = _LIG_STANDIN.sub("", tok).replace(" ", "")
        if not core or core.lower() in _WORDS:
            return tok
        pos = _LIG_STANDIN.search(tok)
        if not pos:
            return tok
        left = tok[: pos.start()].rstrip()
        right = tok[pos.end():].lstrip()
        for lig in _LIGATURES:
            cand = (left + lig + right)
            if cand.lower().strip(".,;:)") in _WORDS:
                return cand
        return tok

    # Repair across the small window around each stray char (it may have eaten a space).
    out = []
    tokens = text.split(" ")
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens) and _LIG_STANDIN.fullmatch(tokens[i]):
            # pattern: "<prev> ! ve" -> rejoin prev? actually stray is its own token
            merged = fix_token(tokens[i] + " " + tokens[i + 1])
            out.append(merged)
            i += 2
        elif _LIG_STANDIN.search(tokens[i]):
            out.append(fix_token(tokens[i]))
            i += 1
        else:
            out.append(tokens[i])
            i += 1
    return _rejoin_split_ligatures(" ".join(out))


def _rejoin_split_ligatures(text: str) -> str:
    """Fix the other broken-ligature form: a stray space splits a word, e.g.
    'fi ve' -> 'five', 'ef fi cient' -> 'efficient'. Only merges when the
    left fragment is a known ligature and the join yields a dictionary word."""
    if not _WORDS:
        return text
    tokens = text.split(" ")
    out: list[str] = []
    i = 0
    while i < len(tokens):
        cur = tokens[i]
        if i + 1 < len(tokens) and cur.lower() in _LIGATURES:
            nxt = tokens[i + 1]
            joined = cur + nxt
            if joined.lower().strip(".,;:)’'\"") in _WORDS:
                # also try gluing onto the preceding fragment: 'ef fi cient'
                if out and (out[-1] + joined).lower().strip(".,;:)") in _WORDS:
                    out[-1] = out[-1] + joined
                else:
                    out.append(joined)
                i += 2
                continue
        out.append(cur)
        i += 1
    return " ".join(out)


@dataclass
class Block:
    type: str   # "heading" | "paragraph" | "bullet" | "image" | "table" | "toc"
    text: str = ""
    level: int = 0
    image_path: str = ""
    rows: list = None   # list[list[str]] for table blocks


_DOT_LEADER = re.compile(r"\s*\.{4,}\s*")


def clean_text(text: str) -> str:
    """Run all per-block text cleanups: ligature repair + dot-leader collapse."""
    text = repair_ligatures(text)
    text = _DOT_LEADER.sub(" ", text)  # TOC dotted leaders -> single space
    text = text.replace("\t", " ")     # stray tabs (e.g. TOC numbering) -> space
    text = re.sub(r"  +", " ", text)   # collapse runs of spaces
    return text


def _emph(s: str, mark: str) -> str:
    """Wrap non-space content in an emphasis marker, keeping outer spaces outside
    so Markdown like '** **' (which renders literally) is never produced."""
    if not s.strip():
        return s
    lead = s[: len(s) - len(s.lstrip())]
    trail = s[len(s.rstrip()):]
    return f"{lead}{mark}{s.strip()}{mark}{trail}"


def _same_style(a: Run, b: Run) -> bool:
    return (a.bold or a.black) == (b.bold or b.black) and a.italic == b.italic and a.mono == b.mono


def runs_to_md(runs: list[Run]) -> str:
    """Convert styled runs to inline Markdown (**bold**, *italic*, `code`)."""
    # Merge adjacent same-style runs so we don't emit '**a****b**'.
    merged: list[Run] = []
    for r in runs:
        if merged and _same_style(merged[-1], r):
            merged[-1] = Run(merged[-1].text + r.text, r.bold, r.italic, r.mono, r.size, r.black)
        else:
            merged.append(Run(r.text, r.bold, r.italic, r.mono, r.size, r.black))

    parts = []
    for r in merged:
        if r.mono:  # inline code — never reflow/clean its contents
            parts.append(_emph(r.text, "`"))
            continue
        t = clean_text(r.text)
        if (r.bold or r.black) and r.italic:
            parts.append(_emph(t, "***"))
        elif r.bold or r.black:
            parts.append(_emph(t, "**"))
        elif r.italic:
            parts.append(_emph(t, "*"))
        else:
            parts.append(t)
    return "".join(parts)


_TOC_ENTRY = re.compile(r"\.{3,}\s*\d{1,3}\s*$")
_TOC_SPLIT = re.compile(r"^(.*?)\.{2,}\s*(\d{1,3})\s*$")
_LEAD_NUM = re.compile(r"^(\d+(?:\.\d+)*)")


def format_toc_entry(raw: str, prefix: str = "") -> str:
    """Turn 'Scale and Specialization......26' into an indented list item."""
    m = _TOC_SPLIT.match(raw.strip())
    if not m:
        return f"- {raw.strip()}"
    title = m.group(1).strip()
    page = m.group(2)
    if prefix:
        title = f"{prefix} {title}"
    lead = _LEAD_NUM.match(title)
    depth = lead.group(1).count(".") if lead else 0
    indent = "  " * depth
    return f"{indent}- {clean_text(title)} … {page}"


def heading_level(size: float, is_black: bool, body_size: float) -> int:
    """Map a heading-candidate line's font size to a level, or 0 if not a heading.

    Tuned to the book's scale: ~36pt chapter titles, ~13pt 'Black' part/section
    labels, ~13.5pt sections, ~12.8pt subsections. 11pt bold lines are bullet
    lead-ins (handled as bullets), so the threshold sits above them.
    """
    if size >= body_size + 12:        # ~21pt+  → chapter title
        return 1
    if is_black and size >= body_size + 3:   # Black-weight part labels (Preface, TOC)
        return 1
    if size >= body_size + 4:         # ~13pt   → section
        return 2
    if size >= body_size + 2.5:       # ~11.8pt → subsection
        return 3
    return 0


def render_page(elements: list[Element], body_size: float) -> list[Block]:
    """Convert one page's elements into Markdown blocks."""
    blocks: list[Block] = []
    # paragraph buffer holds (raw_text, markdown) so we can de-hyphenate on raw
    para: list[tuple] = []

    # Left text margin = the MOST COMMON left edge (the body column), not the
    # minimum — headers/page numbers/figures can sit further left and would
    # otherwise poison the margin and make every body line look "indented".
    x0_counts: dict[int, int] = {}
    for e in elements:
        if e.kind == "text":
            for ln in e.lines:
                x0_counts[round(ln.x0)] = x0_counts.get(round(ln.x0), 0) + 1
    margin = float(max(x0_counts, key=x0_counts.get)) if x0_counts else 0.0
    indent_threshold = margin + 6.0  # past this = indented (e.g. bullet wrap)

    # geometry of the last body line we appended — used to spot paragraph breaks
    last = {"bottom": None, "size": 0.0}

    def flush_para():
        if para:
            text = ""
            for raw, md in para:
                if not text:
                    text = md
                elif text.rstrip().endswith("-"):
                    text = text.rstrip()[:-1] + md.lstrip()
                else:
                    text = text + " " + md.lstrip()
            blocks.append(Block("paragraph", text=text.strip()))
            para.clear()
        last["bottom"] = None

    def para_break_before(line) -> bool:
        """True if there's a vertical gap (or column jump) signalling a new paragraph."""
        if last["bottom"] is None:
            return False
        gap = line.bbox[1] - last["bottom"]
        sz = last["size"] or line.size or 10.0
        return gap > 0.6 * sz or gap < -2.0  # extra leading, or moved up = new column

    for el in elements:
        if el.kind == "image":
            flush_para()
            blocks.append(Block("image", image_path=el.image_path))
            continue
        if el.kind == "table":
            flush_para()
            blocks.append(Block("table", rows=el.rows))
            continue

        for line in el.lines:
            raw = line.text.strip()
            if not raw:
                continue
            md = runs_to_md(line.runs).strip()
            indented = line.x0 > indent_threshold
            lvl = heading_level(line.size, line.is_black, body_size)

            if _TOC_ENTRY.search(raw):
                # Table-of-contents entry: emit one list item per line. A bare
                # section number (e.g. '1.1') may sit just before it — fold it in.
                prefix = ""
                if para and _NUM_ONLY.match(para[-1][0]):
                    prefix = para.pop()[0]
                flush_para()
                blocks.append(Block("toc", text=format_toc_entry(raw, prefix)))
            elif BULLET_RE.match(raw):
                flush_para()
                content_md = BULLET_RE.sub("", md).lstrip()
                blocks.append(Block("bullet", text=content_md))
            elif blocks and blocks[-1].type == "bullet" and not para \
                    and indented and not line.is_bold:
                # indented line right after a bullet = its wrapped continuation
                prev = blocks[-1]
                if prev.text.rstrip().endswith("-"):
                    prev.text = prev.text.rstrip()[:-1] + md
                else:
                    prev.text = prev.text + " " + md
            elif lvl and len(raw) < 90:
                flush_para()
                blocks.append(Block("heading", text=clean_text(raw), level=lvl))
            else:
                if para_break_before(line):
                    flush_para()
                para.append((raw, md))
                last["bottom"] = line.bbox[3]
                last["size"] = line.size

    flush_para()
    return fix_headings(blocks)


_NUM_ONLY = re.compile(r"^\d+(\.\d+)*$")
_NUM_PREFIX = re.compile(r"^(\d+(?:\.\d+)*)\s+")
_CHAPTER_LABEL = re.compile(r"^CHAPTER\s+(\d+)\s*$", re.IGNORECASE)


def fix_headings(blocks: list[Block]) -> list[Block]:
    """Merge split section numbers into titles and refine heading levels.

    Books often render '1.1' and 'Scale and Specialization' as two adjacent
    heading blocks; rejoin them. When a heading is numbered, numbering depth
    is the authoritative level (1 -> h1, 1.1 -> h2, 1.1.1 -> h3); otherwise the
    size-derived level from render_page stands.
    """
    out: list[Block] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.type == "heading" and _NUM_ONLY.match(b.text.strip()):
            num = b.text.strip()
            if i + 1 < len(blocks) and blocks[i + 1].type == "heading":
                title = blocks[i + 1].text.strip()
                merged = Block("heading", text=f"{num} {title}")
                merged.level = min(num.count(".") + 1, 6)
                out.append(merged)
                i += 2
                continue
            b.level = min(num.count(".") + 1, 6)
            out.append(b)
            i += 1
            continue
        if b.type == "heading":
            m = _NUM_PREFIX.match(b.text.strip())
            if m:
                b.level = min(m.group(1).count(".") + 1, 6)
            elif not b.level:
                b.level = 2
            # Fold an orphaned 'CHAPTER N' label (rendered just above the title)
            # into the heading: 'Chapter 0: Inference'.
            cm = out and out[-1].type == "paragraph" and _CHAPTER_LABEL.match(out[-1].text.strip())
            if cm and b.level == 1:
                num = cm.group(1)
                out.pop()
                b.text = f"Chapter {num}: {b.text}"
        out.append(b)
        i += 1
    return out


def dominant_body_size(pages: list[list[Element]]) -> float:
    """Most common text size (by char count) across the doc = body text size."""
    counts: dict[float, int] = {}
    for els in pages:
        for e in els:
            if e.kind != "text":
                continue
            for ln in e.lines:
                for r in ln.runs:
                    n = len(r.text.strip())
                    if n:
                        counts[r.size] = counts.get(r.size, 0) + n
    return max(counts, key=counts.get) if counts else 10.0
