#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.28"]
# ///
"""Extract text, form-field values, and page images from PDFs using PyMuPDF.

Usage (uv installs PyMuPDF on first run):
    uv run extract_pdf.py file1.pdf [file2.pdf ...] [-o OUTDIR] [--dpi 110] [--no-images]

For each PDF, writes into OUTDIR/<pdf-stem>/:
    text.txt     - page-by-page text (with page markers)
    fields.txt   - fillable form-field (widget) names and values, if any
    page-NNN.png - rendered image of each page (unless --no-images)
"""
import argparse
from pathlib import Path

import pymupdf


def extract(pdf_path: Path, outdir: Path, dpi: int, images: bool) -> None:
    doc = pymupdf.open(pdf_path)
    dest = outdir / pdf_path.stem
    dest.mkdir(parents=True, exist_ok=True)

    text_parts, field_parts = [], []
    for i, page in enumerate(doc, start=1):
        text_parts.append(f"===== Page {i} =====\n{page.get_text('text', sort=True)}")

        widgets = [w for w in page.widgets() if w.field_value not in (None, "", "Off")]
        if widgets:
            field_parts.append(f"===== Page {i} =====")
            field_parts += [f"{w.field_name}: {w.field_value}" for w in widgets]

        if images:
            page.get_pixmap(dpi=dpi).save(dest / f"page-{i:03d}.png")

    (dest / "text.txt").write_text("\n".join(text_parts))
    if field_parts:
        (dest / "fields.txt").write_text("\n".join(field_parts))
    print(f"{pdf_path.name}: {doc.page_count} pages -> {dest}"
          f"{' (has form fields)' if field_parts else ''}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="+", type=Path)
    ap.add_argument("-o", "--outdir", type=Path, default=Path("extracted"))
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()
    for pdf in args.pdfs:
        extract(pdf, args.outdir, args.dpi, not args.no_images)


if __name__ == "__main__":
    main()
