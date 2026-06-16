"""Write rendered blocks to an Obsidian vault: one Markdown file + images.

Images go in an `attachments/` folder next to the note and are linked with
Obsidian's `![[wikilink]]` embed syntax.
"""
from __future__ import annotations

import os
import shutil

from render import Block, clean_text


def _table_md(rows: list) -> str:
    """Render rows (list of list of cell strings) as a GitHub-style Markdown table."""
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    def fmt(r):
        cells = [clean_text((c or "").replace("\n", " ")).replace("|", "\\|").strip()
                 for c in r]
        cells += [""] * (ncol - len(cells))
        return "| " + " | ".join(cells) + " |"
    out = [fmt(rows[0]), "| " + " | ".join(["---"] * ncol) + " |"]
    out += [fmt(r) for r in rows[1:]]
    return "\n".join(out)


def _embed_path(image_path: str, attach: str) -> str:
    """Wikilink target for a figure: its path relative to the attachments dir,
    e.g. 'SIA Paper/page001_fig1.png'. Obsidian resolves this by suffix, so the
    figure is found wherever that attachments folder ends up in the vault."""
    rel = os.path.relpath(image_path, attach)
    if rel.startswith(".."):           # not under attachments — fall back to name
        rel = os.path.basename(image_path)
    return rel.replace(os.sep, "/")


def blocks_to_markdown(blocks: list[Block], attach: str) -> str:
    lines: list[str] = []
    prev_list = False
    for b in blocks:
        is_list = b.type in ("bullet", "toc")
        # keep consecutive list items tight (no blank line) so nesting renders
        if is_list and prev_list and lines and lines[-1] == "":
            lines.pop()

        if b.type == "heading":
            lines.append(("#" * max(b.level, 1)) + " " + b.text)
        elif b.type == "paragraph":
            lines.append(b.text)
        elif b.type == "bullet":
            lines.append("- " + b.text)
        elif b.type == "toc":
            lines.append(b.text)  # already a (possibly indented) list item
        elif b.type == "table":
            lines.append(_table_md(b.rows or []))
        elif b.type == "image":
            lines.append(f"![[{_embed_path(b.image_path, attach)}]]")
        lines.append("")  # blank line between blocks
        prev_list = is_list
    return "\n".join(lines).strip() + "\n"


def write_vault(pages_blocks: list[list[Block]], out_dir: str, title: str):
    """Write a single note (all pages concatenated). Figures already live under
    attachments/<document>/ from extraction; any that don't are copied in,
    preserving their subfolder."""
    attach = os.path.join(out_dir, "attachments")
    os.makedirs(attach, exist_ok=True)

    all_blocks: list[Block] = []
    for blocks in pages_blocks:
        for b in blocks:
            if b.type == "image" and b.image_path and _embed_path(
                    b.image_path, attach) == os.path.basename(b.image_path) \
                    and os.path.abspath(os.path.dirname(b.image_path)) != os.path.abspath(attach):
                # external image — copy into attachments root
                shutil.copy2(b.image_path, os.path.join(attach, os.path.basename(b.image_path)))
        all_blocks.extend(blocks)

    md = blocks_to_markdown(all_blocks, attach)
    note_path = os.path.join(out_dir, f"{title}.md")
    with open(note_path, "w") as f:
        f.write(f"# {title}\n\n")
        f.write(md)
    return note_path
