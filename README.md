---
title: Docsidian
emoji: 📄
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# Docsidian

Convert a PDF into a ready-to-open **Obsidian** vault — Markdown plus extracted
figures — using direct text/image extraction (no LLM, no cloud).

It preserves the structure that usually gets lost: heading hierarchy, **bold** /
*italic* / `inline code`, bullet lists, tables, and figures placed where they
appear. Running headers, page numbers, and dotted table-of-contents leaders are
cleaned up automatically.

## How it works

```
PDF ──> extract ──> render ──> Obsidian vault
        (PyMuPDF)   (Markdown)  (.md + attachments/)
```

| File | Role |
|------|------|
| `extract.py` | Pull styled text runs, images (tiled figures merged), and tables from each page; strip page chrome. |
| `render.py` | Turn page elements into Markdown blocks: headings, paragraphs, bullets, tables, inline formatting, ligature repair. |
| `to_obsidian.py` | Write the Markdown note + copy figures into `attachments/`, linked with `![[wikilinks]]`. |
| `port.py` | Shared `convert()` pipeline + a CLI. |
| `webapp.py` | Small Flask UI: upload a PDF, download the vault as a zip. |

## Use

With [uv](https://docs.astral.sh/uv/) — no setup needed, it resolves deps from
the lockfile on first run.

### Web app (recommended)

```bash
uv run webapp.py
```

Open <http://127.0.0.1:5001>, choose a PDF, click **Convert**, and a
`<title>.zip` downloads. Unzip it and open the folder as a vault in Obsidian.

### Command line

```bash
uv run port.py paper.pdf --out my_vault --title "Paper Title"
```

Produces `my_vault/<title>.md` and `my_vault/attachments/`.

### Without uv (pip + venv)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python webapp.py
```

## Limitations

- **Math/equations** extract as plain text, so complex notation is imperfect —
  this is inherent to direct PDF extraction without OCR/an LLM.
- **Color** is not carried into Markdown; colored monospace spans become
  `inline code`.
- **Notion** export is not implemented yet (the pipeline is target-agnostic, so
  it's a straightforward addition).
