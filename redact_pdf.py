# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.28", "pypdf[crypto]>=6"]
# ///
"""Redact or replace text in a PDF locally. Nothing leaves your machine.

The original text under each match is removed from the file, not just covered
up. A match is then either blacked out or, if you give a replacement, painted
over in white with the new text written in its place. Form fields and
annotations are flattened first so their values can be changed too, and
metadata is cleared. The output is re-opened and searched (with PyMuPDF and
pypdf) for every original term; anything left is counted, with the reason.

Terms file, one rule per line (# for comments, matching ignores case):

    Jane Q Taxpayer => John Doe      replace
    123-45-6789 => XXX-XX-XXXX       replace; any dash, a space or none matches,
                                     so 123–45–6789 and 123456789 match too
    Acme Payroll                     black out

Usage (uv installs PyMuPDF and pypdf on first run):

    uv run redact_pdf.py IN.pdf                    # reads terms.txt next to this script,
                                                   # writes IN-redacted.pdf next to IN.pdf
    uv run redact_pdf.py IN.pdf OUT.pdf --terms-file other.txt
    uv run redact_pdf.py IN.pdf --remove-password  # with no rules: writes IN-unlocked.pdf
    (with no terms.txt and no other rules, it prompts for them, input hidden)

To keep terms out of shell history, use --terms-file or the prompt, not --term.
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path

import pymupdf

SEP = "=>"
DEFAULT_TERMS = Path(__file__).resolve().parent / "terms.txt"  # read when --terms-file is not given
FONT = "helv"
# Include text that is clipped out of view or lies outside the page: it is invisible but can
# still be copied out of the file, so it has to be found and removed too.
NO_CLIP = ~(pymupdf.TEXT_CLIP | pymupdf.TEXT_MEDIABOX_CLIP)
SEARCH_FLAGS = pymupdf.TEXTFLAGS_SEARCH & NO_CLIP
TEXT_FLAGS = pymupdf.TEXTFLAGS_TEXT & NO_CLIP
DICT_FLAGS = pymupdf.TEXTFLAGS_DICT & NO_CLIP


def split_rule(line: str) -> tuple[str, str | None]:
    """'old => new' gives (old, new); a bare 'old' gives (old, None), meaning black out."""
    if SEP in line:
        old, new = line.split(SEP, 1)
        return old.strip(), new.strip()
    return line.strip(), None


def read_terms(args: argparse.Namespace) -> list[tuple[str, str | None]]:
    lines = list(args.term or [])
    terms_file = args.terms_file
    if terms_file is None and DEFAULT_TERMS.is_file():
        terms_file = DEFAULT_TERMS
        print(f"Using {DEFAULT_TERMS}")
    if terms_file:
        lines += [l for l in Path(terms_file).read_text().splitlines()
                  if l.strip() and not l.strip().startswith("#")]
    if not lines and not args.regex and not args.box and not args.remove_password:
        print(f"Enter rules, one per line: 'old {SEP} new' to replace, 'old' to black out.")
        print("Input is hidden. Empty line to finish.")
        while line := getpass.getpass("rule: ").strip():
            lines.append(line)
    rules = dict(split_rule(l) for l in lines)
    # Longer terms first so "123 Main St" is matched whole before "Main".
    return sorted(rules.items(), key=lambda r: len(r[0]), reverse=True)


def parse_box(spec: str) -> tuple[int, tuple[float, float, float, float]]:
    """'PAGE:x0,y0,x1,y1' with 1-based page and 0-1 coordinates from the top-left."""
    page, coords = spec.split(":")
    x0, y0, x1, y1 = (float(v) for v in coords.split(","))
    return int(page), (x0, y0, x1, y1)


def spans(page: pymupdf.Page) -> list[dict]:
    return [s for b in page.get_text("dict", flags=DICT_FLAGS)["blocks"] for l in b.get("lines", []) for s in l["spans"]]


def style_at(page_spans: list[dict], r: pymupdf.Rect) -> tuple[float, float, tuple]:
    """Font size, baseline y and colour of the original text in r."""
    mid = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
    for s in page_spans:
        if mid in pymupdf.Rect(s["bbox"]):
            return s["size"], s["origin"][1], pymupdf.sRGB_to_pdf(s["color"])
    return r.height * 0.75, r.y1 - r.height * 0.22, (0, 0, 0)


def band(r: pymupdf.Rect) -> pymupdf.Rect:
    """The middle 40% of a matched line's height. PDF readers remove every character whose box
    touches the removal area, and a full-height area reaches into tightly spaced lines above and
    below; this band covers the matched characters only."""
    return pymupdf.Rect(r.x0, r.y0 + r.height * 0.3, r.x1, r.y1 - r.height * 0.3)


def mark(page: pymupdf.Page, page_spans, rects, rule: int, new: str | None, args, state: dict) -> tuple[int, int]:
    """Mark rects for removal and queue replacement text to write once the old text is gone.

    A hit lying inside one already marked (a shorter term inside a longer one) is skipped,
    so two replacements are not written over each other. Returns (marked, skipped).
    """
    marked = skipped = 0
    for r in rects:
        if any(abs(r & m) > 0.8 * abs(r) for m, _ in state["marks"]):
            skipped += 1
            continue
        marked += 1
        state["marks"].append((r, rule))
        page.add_redact_annot(band(r), fill=False, cross_out=False)  # remove text, keep background
        if new is None:
            # Drawn after removal, a little shorter than the line so it doesn't cover its neighbours.
            state["blackouts"].append(pymupdf.Rect(r.x0, r.y0 + r.height * 0.1, r.x1, r.y1 - r.height * 0.1))
            continue
        size, baseline, color = style_at(page_spans, r)
        width = pymupdf.get_text_length(new, FONT, size)
        if new and width > r.width * args.overflow:
            size = max(size * r.width * args.overflow / width, args.min_size)
            width = pymupdf.get_text_length(new, FONT, size)
        state["inserts"].append((pymupdf.Point(r.x0, baseline), new, size, color))
        state["written"].append((pymupdf.Rect(r.x0, baseline - size, r.x0 + width, baseline + size * 0.3), new))
    return marked, skipped


def split_quote_ops(doc: pymupdf.Document) -> int:
    """Rewrite the ' and " text operators (move to the next line and show text) as the separate
    operators they stand for: T* then Tj, with Tw and Tc first for ". The page looks the same.
    MuPDF's redaction mishandles ' and ", moving or dropping lines drawn with them and lines
    after them, even when the line itself has nothing to remove. Returns streams rewritten."""
    from pypdf.generic import ContentStream, DecodedStreamObject

    xrefs = {x for page in doc for x in page.get_contents()}
    xrefs |= {x for x in range(1, doc.xref_length())
              if doc.xref_is_stream(x) and doc.xref_get_key(x, "Subtype")[1] == "/Form"}
    changed = 0
    for xref in sorted(xrefs):
        data = doc.xref_stream(xref)
        if not data or (b"'" not in data and b'"' not in data):
            continue
        try:
            raw = DecodedStreamObject()
            raw.set_data(data)
            content = ContentStream(raw, None)
            ops, found = [], False
            for operands, op in content.operations:
                if op == b"'":
                    ops += [([], b"T*"), (operands, b"Tj")]
                elif op == b'"':
                    aw, ac, text = operands
                    ops += [([aw], b"Tw"), ([ac], b"Tc"), ([], b"T*"), ([text], b"Tj")]
                else:
                    ops.append((operands, op))
                    continue
                found = True
            if found:
                content.operations = ops
                doc.update_stream(xref, content.get_data())
                changed += 1
        except Exception:
            continue  # leave a stream that can't be parsed as it is
    return changed


def drop_unseen(doc: pymupdf.Document) -> int:
    """Delete form fields and annotations a viewer doesn't show on screen (hidden, or print-only).
    Flattening would otherwise paint them onto the page, and they may hold the very details being
    removed. Returns how many were deleted."""
    unseen = pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_NO_VIEW
    count = 0
    for page in doc:
        # A widget's display flags are in its /F key (field_flags are something else).
        flags = lambda xref: int(f) if (f := doc.xref_get_key(xref, "F")[1]).isdigit() else 0
        for widget in [w for w in page.widgets() if flags(w.xref) & unseen]:
            page.delete_widget(widget)
            count += 1
        for annot in [a for a in page.annots() if a.flags & unseen]:
            page.delete_annot(annot)
            count += 1
    return count


def show_layers(doc: pymupdf.Document) -> None:
    """Switch every optional-content layer on while working, so text on a hidden layer (such
    as a print-only one) is found too. This doesn't change what the saved file shows."""
    for config in doc.layer_ui_configs():
        doc.set_layer_ui_config(config["number"], pymupdf.PDF_OC_ON)


def enlarge(page: pymupdf.Page):
    """Make the page three times as wide and tall (and its visible area the whole of that), so
    text placed outside the page is found too. Returns a function that puts the page back."""
    media, crop = page.mediabox, page.cropbox
    page.set_mediabox(pymupdf.Rect(media.x0 - media.width, media.y0 - media.height,
                                   media.x1 + media.width, media.y1 + media.height))

    def restore():
        page.set_mediabox(media)
        page.set_cropbox(crop)
    return restore


# Characters PDFs use for a hyphen or dash: hyphen-minus, hyphen, non-breaking hyphen, figure dash,
# en dash, em dash, minus sign, small and full-width hyphen-minus, soft hyphen; plus the NUL and
# replacement characters that text extraction gives for a symbol the font has no mapping for.
DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2212\ufe63\uff0d\u00ad\u0000\ufffd"
SEPARATOR = f"[{DASHES}]|[^\\S\\n]"


def term_regex(term: str) -> re.Pattern:
    """A term as a pattern in which each run of dashes or spaces matches any run of dashes or
    spaces, or none, so '123-45-6789' also matches '123–45–6789', '123 45 6789' and '123456789'.
    A term starting or ending with a digit only matches a whole number, not part of a longer one."""
    parts = re.split(f"(?:{SEPARATOR})+", term.strip())
    body = f"(?:{SEPARATOR})*".join(re.escape(part) for part in parts)
    if term.strip()[:1].isdigit():
        body = r"(?<!\d)" + body
    if term.strip()[-1:].isdigit():
        body += r"(?!\d)"
    return re.compile(body, re.IGNORECASE)


def pattern_of(rule: tuple) -> re.Pattern:
    old, _ = rule
    return old if isinstance(old, re.Pattern) else term_regex(old)


def hits_for(page: pymupdf.Page, rule: tuple, text: str, flags: int) -> list[pymupdf.Rect]:
    """Where on the page the rule matches. Each exact string the pattern finds in the text is
    searched for, and a hit is kept only if the pattern also matches the text around it, since
    PDF search knows nothing of word or number boundaries (so '123456789' would otherwise be
    found inside '91234567890')."""
    pattern = pattern_of(rule)
    hits = []
    for needle in {m.group(0) for m in pattern.finditer(text)}:
        for r in page.search_for(needle, flags=flags):
            pad = r.height
            around = page.get_text(clip=pymupdf.Rect(r.x0 - pad, r.y0 + r.height * 0.3,
                                                     r.x1 + pad, r.y1 - r.height * 0.3), flags=flags)
            if not pattern.search(around):
                continue
            if not any(abs(r & h) > 0.8 * abs(r) for h in hits):  # same spot found twice
                hits.append(r)
    return hits


def label(rule: tuple) -> str:
    old, new = rule
    old = f"regex {old.pattern!r}" if isinstance(old, re.Pattern) else repr(old)
    return f"{old} -> {new!r}" if new is not None else f"{old} (black out)"


def apply(page: pymupdf.Page) -> None:
    # Remove text, blank image pixels under the boxes, and remove vector art lying wholly
    # inside them (form borders crossing a box are kept).
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)


def redact(args: argparse.Namespace) -> int:
    terms = read_terms(args)
    patterns = [(re.compile(old, re.IGNORECASE), new) for old, new in map(split_rule, args.regex or [])]
    boxes = [parse_box(b) for b in args.box or []]
    rules = terms + patterns
    if not (rules or boxes or args.remove_password):
        print("Nothing to redact.", file=sys.stderr)
        return 2
    if args.output is None:
        src = Path(args.input)
        suffix = "redacted" if rules or boxes else "unlocked"
        args.output = str(src.with_name(f"{src.stem}-{suffix}.pdf"))
    if Path(args.input).resolve() == Path(args.output).resolve():
        print("Output must be a different file from input.", file=sys.stderr)
        return 2

    doc = pymupdf.open(args.input)
    # Also true for PDFs with only an owner password (restricting printing, copying and so on).
    encrypted, password = doc.is_encrypted or bool((doc.metadata or {}).get("encryption")), None
    if doc.needs_pass:
        password = args.password or getpass.getpass("PDF password: ")
        if not doc.authenticate(password):
            print("Wrong password.", file=sys.stderr)
            return 2

    # Turn form-field values and annotations into ordinary page content, so they
    # are found by search and removed by redaction like any other text.
    split_quote_ops(doc)
    unseen = drop_unseen(doc)
    doc.bake(annots=True, widgets=True)

    marked = [0] * len(rules)
    nested = [0] * len(rules)
    hidden = [0] * len(rules)
    no_text_pages = []
    pages = {}  # page number -> {"marks", "inserts", "written"}, used again by verify

    # Pass 1, the text you can see: black it out, or remove it and queue its replacement.
    for page in doc:
        w, h = page.rect.width, page.rect.height
        for n, (x0, y0, x1, y1) in boxes:
            if n == page.number + 1:
                page.add_redact_annot(pymupdf.Rect(x0 * w, y0 * h, x1 * w, y1 * h),
                                      fill=(1, 1, 1) if args.white else (0, 0, 0))
        text = page.get_text()
        if not text.strip():
            no_text_pages.append(page.number + 1)
        page_spans = spans(page)
        state = pages[page.number] = {"marks": [], "inserts": [], "written": [], "blackouts": []}
        for i, rule in enumerate(rules):
            m, s = mark(page, page_spans, hits_for(page, rule, text, pymupdf.TEXTFLAGS_SEARCH),
                        i, rule[1], args, state)
            marked[i] += m
            nested[i] += s
        apply(page)

    # Pass 2, copies you can't see: on hidden layers, clipped out of view, or outside the page.
    # Remove them without writing anything in their place.
    show_layers(doc)
    for page in doc:
        restore = enlarge(page)
        text = page.get_text(flags=TEXT_FLAGS)
        for i, rule in enumerate(rules):
            for r in hits_for(page, rule, text, SEARCH_FLAGS):
                page.add_redact_annot(band(r), fill=False, cross_out=False)
                hidden[i] += 1
        if page.first_annot:
            apply(page)
        restore()

    # Write the replacements and black-out boxes last, so pass 2 can't mistake one for a hidden copy.
    for page in doc:
        for r in pages[page.number]["blackouts"]:
            page.draw_rect(r, color=None, fill=(1, 1, 1) if args.white else (0, 0, 0))
        for origin, new, size, color in pages[page.number]["inserts"]:
            page.insert_text(origin, new, fontname=FONT, fontsize=size, color=color)

    doc.set_metadata({})
    doc.del_xml_metadata()
    # Keep the original password and permissions unless asked to remove them.
    keep = encrypted and not args.remove_password
    doc.save(args.output, garbage=4, deflate=True, clean=True,
             encryption=pymupdf.PDF_ENCRYPT_KEEP if keep else pymupdf.PDF_ENCRYPT_NONE)
    doc.close()
    if encrypted:
        print("password removed" if args.remove_password else "password kept (use --remove-password to remove it)")
    elif args.remove_password:
        print("input had no password")

    for i, rule in enumerate(rules):
        extra = f", plus {nested[i]} inside a longer term" if nested[i] else ""
        extra += f", plus {hidden[i]} hidden cop{'y' if hidden[i] == 1 else 'ies'} removed" if hidden[i] else ""
        print(f"{label(rule)}: {marked[i]} match(es){extra}")
    if boxes:
        print(f"manual boxes: {len(boxes)}")
    if unseen:
        print(f"removed {unseen} hidden or print-only form field(s)/annotation(s) "
              "(not shown on screen; they would have appeared after flattening)")
    if no_text_pages:
        print(f"WARNING: pages {no_text_pages} have no text layer (scanned?); text on them "
              "cannot be found by search. Cover it with --box, or check those pages by eye.")
    for i, rule in enumerate(rules):
        if marked[i] + nested[i] + hidden[i] == 0:
            print(f"NOTE: no match found for {label(rule)} (check spelling / spacing).")
    return verify(args.output, rules, pages, password)


def verify(path: str, rules: list, pages: dict, password: str | None) -> int:
    """Count what is left of each rule in the output, and say why for each leftover."""
    doc = pymupdf.open(path)
    if doc.needs_pass and not doc.authenticate(password or ""):
        print("FAILED: could not reopen the output to check it.", file=sys.stderr)
        return 1
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        if reader.is_encrypted:
            reader.decrypt(password or "")
    except Exception as e:  # the second check is extra; don't fail the run over it
        print(f"NOTE: second check (pypdf) skipped: {e}")
        reader = None

    leftovers = {}  # (rule index, reason) -> list of page numbers
    def add(i, reason, page_no, n=1):
        leftovers.setdefault((i, reason), []).extend([page_no] * n)

    # Visible text, in the same coordinates as pass 1, so leftovers can be matched to what was done.
    patterns = [pattern_of(rule) for rule in rules]
    seen = {}  # (page, rule) -> instances accounted for
    for page in doc:
        state = pages.get(page.number, {"marks": [], "written": []})
        text = page.get_text()
        page_spans = spans(page)
        for i, rule in enumerate(rules):
            count = 0
            for r in hits_for(page, rule, text, pymupdf.TEXTFLAGS_SEARCH):
                if any(abs(r & w) > 0.5 * abs(r) and patterns[i].search(n) for w, n in state["written"] if n):
                    continue  # part of a replacement that itself matches the rule
                count += 1
                if any(abs(r & m) > 0.5 * abs(r) for m, _ in state["marks"]):
                    mid = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
                    font = next((s["font"] for s in page_spans if mid in pymupdf.Rect(s["bbox"])), "?")
                    add(i, f"was marked for removal but the text is still there (font {font})", page.number + 1)
                elif any(abs(r & w) > 0 for w, _ in state["written"]):
                    add(i, "formed by a replacement running into the text next to it", page.number + 1)
                else:
                    add(i, "not found when the original was searched", page.number + 1)
            seen[page.number, i] = count

    # Everything PyMuPDF can see, hidden or not; then pypdf, which reads the file differently.
    show_layers(doc)
    for page in doc:
        state = pages.get(page.number, {"marks": [], "written": []})
        restore = enlarge(page)
        text = page.get_text(flags=TEXT_FLAGS)
        restore()
        pypdf_text = None
        if reader is not None:
            try:
                pypdf_text = reader.pages[page.number].extract_text() or ""
            except Exception:
                pass
        for i, pattern in enumerate(patterns):
            # Matches that are just the replacements written in are expected.
            expected = sum(len(pattern.findall(n)) for _, n in state["written"] if n)
            accounted = seen.get((page.number, i), 0)
            extra = len(pattern.findall(text)) - expected - accounted
            if extra > 0:
                add(i, "a hidden copy (hidden layer, clipped, or off the page) that could not be removed",
                    page.number + 1, extra)
                accounted += extra
            if pypdf_text is not None:
                extra = len(pattern.findall(pypdf_text)) - expected - accounted
                if extra > 0:
                    add(i, "in the file but stored in a way PyMuPDF can't read, so it couldn't "
                           "be removed; found by pypdf", page.number + 1, extra)

    if not leftovers:
        if rules:
            print(f"OK: wrote {path}; none of the original text was found in it.")
        else:
            print(f"OK: wrote {path}")
        return 0

    print("\nStill in the output:")
    for i, rule in enumerate(rules):
        items = [(reason, pg) for (j, reason), pg in leftovers.items() if j == i]
        if not items:
            continue
        print(f"  {label(rule)}: {sum(len(pg) for _, pg in items)} left")
        for reason, pg in items:
            where = ", ".join(str(n) for n in sorted(set(pg)))
            print(f"    {len(pg)} on page(s) {where}: {reason}")
    sys.stdout.flush()
    print(f"FAILED: wrote {path}, but the text above is still in it. Don't share it as is.", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output", nargs="?", help="default: INPUT-redacted.pdf (or INPUT-unlocked.pdf) next to the input")
    ap.add_argument("--term", action="append", help=f"'old {SEP} new' or 'old' (repeatable; lands in shell history)")
    ap.add_argument("--terms-file", help=f"file with one rule per line (default: {DEFAULT_TERMS.name} next to this script, if it exists)")
    ap.add_argument("--regex", action="append", help=f"'PATTERN {SEP} new' or 'PATTERN' (repeatable)")
    ap.add_argument("--box", action="append", metavar="PAGE:x0,y0,x1,y1",
                    help="black out an area, 1-based page, 0-1 coordinates from top-left (repeatable)")
    ap.add_argument("--white", action="store_true", help="black-out boxes in white instead of black")
    ap.add_argument("--overflow", type=float, default=1.0,
                    help="replacement may be this many times the original's width before it is shrunk (default 1.0)")
    ap.add_argument("--min-size", type=float, default=4, help="smallest font size for replacement text (default 4)")
    ap.add_argument("--password", help="password for an encrypted PDF (prompted if needed)")
    ap.add_argument("--remove-password", action="store_true",
                    help="write the output without a password or permission restrictions; "
                         "works without any rules to just unlock a PDF")
    args = ap.parse_args()
    return redact(args)


if __name__ == "__main__":
    sys.exit(main())
