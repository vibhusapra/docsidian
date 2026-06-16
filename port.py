"""Port a PDF to Obsidian (and later Notion) via direct extraction.

CLI:
    python port.py <pdf> --to obsidian [--out DIR] [--title NAME]

Also exposes convert() for the GUI (app.py) to reuse the same pipeline.
"""
from __future__ import annotations

import argparse
import os
import re

from extract import extract_pdf
from render import render_page, dominant_body_size
from to_obsidian import write_vault


def slugify(text: str) -> str:
    """A short, filename-safe slug for namespacing a document's figures."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "doc"


def folder_name(text: str) -> str:
    """A readable, filesystem-safe folder name for a document's attachments."""
    s = re.sub(r'[<>:"/\\|?*]+', " ", text)   # strip path-illegal characters
    s = re.sub(r"\s+", " ", s).strip()
    return s[:60] or "doc"


def convert(pdf: str, out: str, title: str | None = None,
            to: str = "obsidian", progress=None) -> dict:
    """Run the full PDF -> target conversion.

    progress: optional callback(message: str, fraction: float | None). Fraction
    is a 0..1 completion estimate; used by the web app's live progress bar.
    Returns a summary dict: {pages, figures, tables, body, note}.
    """
    def say(msg, frac=None):
        if progress:
            progress(msg, frac)
        else:
            print(msg)

    if to != "obsidian":
        raise NotImplementedError(f"target '{to}' is not supported yet")

    title = title or os.path.splitext(os.path.basename(pdf))[0]
    img_dir = os.path.join(out, "attachments")  # extract figures straight into the vault
    subdir = folder_name(title)  # each doc's figures get their own attachments subfolder

    say("Opening PDF …", 0.02)
    pages = extract_pdf(
        pdf, img_dir, subdir=subdir,
        progress=lambda i, n: say(f"Extracting page {i}/{n} …", 0.05 + 0.60 * i / n))

    body = dominant_body_size(pages)
    total = len(pages) or 1
    pages_blocks = []
    for idx, els in enumerate(pages, 1):
        pages_blocks.append(render_page(els, body))
        say(f"Rendering page {idx}/{total} …", 0.66 + 0.28 * idx / total)

    n_imgs = sum(1 for bl in pages_blocks for b in bl if b.type == "image")
    n_tbl = sum(1 for bl in pages_blocks for b in bl if b.type == "table")

    say("Writing vault …", 0.96)
    note = write_vault(pages_blocks, out, title)
    say(f"Done · {len(pages)} pages · {n_imgs} figures · {n_tbl} tables", 1.0)

    return {"pages": len(pages), "figures": n_imgs, "tables": n_tbl,
            "body": body, "note": note}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--to", choices=["obsidian"], default="obsidian")
    ap.add_argument("--out", default="out")
    ap.add_argument("--title", default=None)
    args = ap.parse_args()
    convert(args.pdf, args.out, args.title, args.to)


if __name__ == "__main__":
    main()
