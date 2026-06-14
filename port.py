"""Port a PDF to Obsidian (and later Notion) via direct extraction.

CLI:
    python port.py <pdf> --to obsidian [--out DIR] [--title NAME]

Also exposes convert() for the GUI (app.py) to reuse the same pipeline.
"""
from __future__ import annotations

import argparse
import os

from extract import extract_pdf
from render import render_page, dominant_body_size
from to_obsidian import write_vault


def convert(pdf: str, out: str, title: str | None = None,
            to: str = "obsidian", progress=None) -> dict:
    """Run the full PDF -> target conversion.

    progress: optional callback(str) for status messages (used by the GUI).
    Returns a summary dict: {pages, figures, body, note}.
    """
    def say(msg):
        if progress:
            progress(msg)
        else:
            print(msg)

    title = title or os.path.splitext(os.path.basename(pdf))[0]
    img_dir = os.path.join(out, "attachments")  # extract figures straight into the vault

    say(f"Extracting {os.path.basename(pdf)} …")
    pages = extract_pdf(pdf, img_dir)
    body = dominant_body_size(pages)

    say("Rendering Markdown …")
    pages_blocks = [render_page(els, body) for els in pages]
    n_imgs = sum(1 for bl in pages_blocks for b in bl if b.type == "image")
    n_tbl = sum(1 for bl in pages_blocks for b in bl if b.type == "table")
    say(f"{len(pages)} pages · {n_imgs} figures · {n_tbl} tables · body ~{body}pt")

    if to != "obsidian":
        raise NotImplementedError(f"target '{to}' is not supported yet")
    note = write_vault(pages_blocks, out, title)
    say(f"Wrote: {note}")

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
