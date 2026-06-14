# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Docsidian converts a PDF into an Obsidian vault (one Markdown note + extracted
figures) using **direct PyMuPDF extraction — no LLM, no OCR, no cloud calls**.
That constraint is intentional: improvements must come from better parsing
heuristics, not by handing pages to a model.

## Commands

Dependencies are managed with `uv` (lockfile committed). A `.venv/` from
`python3 -m venv` also exists locally.

```bash
# Web UI (primary entry point) — http://127.0.0.1:5001
uv run webapp.py

# CLI — writes <out>/<title>.md + <out>/attachments/
uv run port.py <file.pdf> --out my_vault --title "My Title"

# Without uv
.venv/bin/python webapp.py
```

There is **no test suite, linter, or build step.** Verification is empirical:
run the pipeline against the sample PDFs and grep the output (see below).

## Architecture

Three-stage pipeline, each stage in its own module. `port.convert()` is the
single shared entry point that both the CLI and the web app call.

```
PDF ──> extract.py ──> render.py ──> to_obsidian.py
        Element[]/page  Block[]/page  <title>.md + attachments/
```

1. **extract.py** — `extract_pdf()` returns a list (per page) of `Element`s.
   - `Element.kind` is `"text"`, `"image"`, or `"table"`.
   - Text elements hold `Line`s of styled `Run`s (bold/italic/mono/black + size),
     so formatting survives to the render stage. This per-span fidelity is the
     whole reason text isn't flattened early.
   - Images: raw embedded image fragments are **merged by bounding-box proximity**
     (`_merge_image_rects`) and re-rendered as one clipped PNG — many PDFs tile a
     single figure into several image XObjects.
   - Tables come from `page.find_tables()`; single-row detections are rejected
     (they're almost always diagram ruling lines). Text inside a table's rect is
     dropped so it isn't also emitted as garbled prose.
   - Page chrome is removed two ways: `_is_chrome` (margin-band page numbers) and
     `_strip_repeated_chrome` (lines that recur across many pages, e.g. a running
     title with no page number).

2. **render.py** — `render_page()` turns `Element`s into Markdown `Block`s
   (heading / paragraph / bullet / table / toc / image), then `fix_headings()`
   post-processes. Key logic:
   - `runs_to_md()` emits inline `**bold**` / `*italic*` / `` `code` ``; lone
     symbols (∗, µ, †) are left unstyled to avoid `*∗*` clutter.
   - `heading_level()` maps **font size relative to body size** to h1/h2/h3.
     `fix_headings()` then overrides with section-numbering depth when present,
     merges split number+title headings, folds `CHAPTER N` labels into titles,
     and promotes fully-bold run-in subsection labels (`**1.1. …**`) to headings.
   - `clean_text()` is the shared text fixer: ligature repair (broken `fi`/`fl`
     ToUnicode, validated against `/usr/share/dict/words`), diacritic recombining
     (`G¨odel` → `Gödel`), dotted-leader and whitespace collapse.
   - Paragraphs are split on vertical gaps / column jumps (`para_break_before`),
     not just at headings.

3. **to_obsidian.py** — concatenates all pages' blocks into one note, writes
   figures into `attachments/`, and links them with `![[wikilink]]` embeds.

`webapp.py` is a single-file Flask app: upload a PDF, it runs `convert()` into a
temp dir, zips the vault, and streams it back. `render.yaml` deploys it on Render
via gunicorn.

## Working on the heuristics

The parsing is tuned against two reference PDFs in the repo root (gitignored):
the *Inference Engineering* book (259 pp, clean layout) and the *SIA* research
paper (15 pp, dense/academic). **The hard rule when changing any heuristic: do
not regress the book while fixing the paper, and vice-versa.** Standard check:

```bash
uv run port.py "Inference Engineering.pdf" --out /tmp/book --title book
# expect: 17 h1, 50 h2, 91 h3, 20 tables, 145 TOC items
grep -cE '^# '  /tmp/book/book.md     # h1
grep -cE '^\| --- \|' /tmp/book/book.md   # tables
```

Many thresholds (heading size offsets, margin-band fractions, repeated-line
counts) are empirically chosen constants — when adjusting one, re-run both PDFs
and eyeball the Markdown plus a couple of rendered figures.

## Known limitations

- Math/equations extract as plain positioned glyphs (no LaTeX reconstruction).
- Tables whose columns are separated only by alignment (no ruling lines) can get
  merged into one cell — PyMuPDF can't split them and a length-based reject would
  also kill legitimate long-celled tables.
- Color isn't represented in Markdown; colored monospace becomes inline code.
- Only Obsidian output exists; the pipeline is target-agnostic so a Notion writer
  would be a new module consuming the same `Block`s.
