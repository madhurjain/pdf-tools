# pdf-tools

Two small scripts for working with PDFs such as tax forms on your own machine. Neither makes any
network calls.

- **[`redact_pdf.py`](#redact_pdfpy)** blacks out or replaces text, such as names, SSNs, EINs,
  addresses and account numbers, before you share a PDF.
- **[`extract_pdf.py`](#extract_pdfpy)** dumps a PDF's text, form-field values and page images,
  so you can see what's in it or check a redacted copy.

## Requirements

[uv](https://docs.astral.sh/uv/) (`brew install uv`, or see uv's install guide). Each script lists
its own dependencies at the top of the file, so the first `uv run` downloads Python,
[PyMuPDF](https://pymupdf.readthedocs.io/) and (for `redact_pdf.py`) [pypdf](https://pypdf.readthedocs.io/)
into uv's cache. After that the scripts work offline.

```sh
git clone <this repo> pdf-tools
cd pdf-tools
```

The examples below run from the `pdf-tools` folder. To run the scripts from anywhere, add aliases to
`~/.zshrc` (change the path to wherever you cloned the repo):

```sh
alias redact-pdf='uv run ~/pdf-tools/redact_pdf.py'
alias extract-pdf='uv run ~/pdf-tools/extract_pdf.py'
```

---

## redact_pdf.py

Removes or replaces the text you name in a PDF. The original text is **removed from the file**, not
just covered with a box, so it can't be copied out or recovered from the output. Fillable form fields
are flattened first so their values are changed too, and document metadata (author, title and so on)
is cleared. Your original PDF is never modified.

### Quick start

```sh
nano terms.txt                          # uncomment the sample rules you need, put in your values
uv run redact_pdf.py ~/taxes/1040.pdf
```

This writes `~/taxes/1040-redacted.pdf` next to `1040.pdf`. Open it and look it over before you
share it.

### Creating terms.txt

The script changes only what your rules name, in `terms.txt`, `--term`, `--regex` and `--box`. It has
no built-in patterns: it never removes addresses, SSNs or anything else on its own.

`terms.txt` sits next to `redact_pdf.py` and holds one rule per line. There are two kinds of rule:

| Line | Effect |
|---|---|
| `old text => new text` | Removes `old text` and writes `new text` in its place, at the same position, size and colour |
| `old text` | Removes `old text` and covers the spot with a black box |

Lines starting with `#` and blank lines are ignored. Spaces around `=>` don't matter.

The `terms.txt` in this repo has commented-out samples for names, SSNs, EINs, addresses, dates of
birth, phone numbers, email addresses and bank accounts. Remove the leading `# ` from the lines you
need and put in your own values. As shipped, it has no active rules, so the script asks for rules
when you run it.

Example of active rules:

```
# names
Jane Q Taxpayer => John Doe
Jane Taxpayer => John Doe

# SSN: also matches 123–45–6789, 123 45 6789 and 123456789
123-45-6789 => XXX-XX-XXXX

# employer EIN
12-3456789 => XX-XXXXXXX

# address
42 Elm Street => 1 Main Street
Springfield, IL 62704 => Anytown, ST 00000

# black these out
Acme Payroll Services
000123456789
```

#### Writing good rules

- **Dashes and spaces are flexible.** Matching ignores upper and lower case, and a dash or space in a
  term matches any kind of dash (hyphen, en dash, minus sign, non-breaking hyphen and so on), a space,
  or nothing. `123-45-6789` also matches `123–45–6789`, `123 45 6789` and `123456789`, and all of
  them get the same replacement. Other punctuation (commas, periods) must match exactly.
- **Numbers match whole.** A term that starts or ends with a digit doesn't match inside a longer
  number: `123456789` won't touch `91234567890`.
- **Words are matched anywhere, even inside longer words.** `Jane` also matches inside `Janet`, so
  use the full name or phrase where you can.
- **Longer terms are applied first.** You can list `Jane Q Taxpayer` and `Jane` together, and the full
  name is replaced as a whole.
- **A term split across two lines won't match.** Add each part as its own rule, for example the street
  and the city on separate lines.
- **Names in capitals.** Some forms print names in all capitals. A rule matches regardless of case, but
  your replacement is written exactly as you typed it, so type it in capitals if it should match.
- **If the same term appears twice** in the file, the last rule for it wins.

#### Keep terms.txt private

Once you fill it in, the file holds your real SSN, EIN and names in plain text. **Don't commit it.**
Either:

- keep your rules in a file outside the repo, next to your tax files, and pass it with
  `--terms-file ~/taxes/terms.txt`; or
- edit `terms.txt` in place and tell git to ignore your changes to it:
  `git update-index --skip-worktree terms.txt`. Undo that with `--no-skip-worktree` before you
  pull an updated copy of the file.

Delete your rules when you're done, and don't keep them in a synced folder.

### Running it

```
uv run redact_pdf.py INPUT.pdf [OUTPUT.pdf] [options]
```

- **`INPUT.pdf`**: the PDF to process. It isn't changed.
- **`OUTPUT.pdf`** (optional): where to write the result. The default is `INPUT-redacted.pdf` in the
  same folder as the input, or `INPUT-unlocked.pdf` if you only remove a password. An existing file with that name is overwritten.
- **Rules**: by default, the script reads `terms.txt` from the folder that `redact_pdf.py` is in
  (not the folder you run it from), and prints its full path. To use a different file, pass
  `--terms-file`. If there are no rules in it and none given in other ways, it asks for rules one at
  a time, with your typing hidden. Enter an empty line to finish.

Examples:

```sh
# rules from terms.txt next to the script, output 1040-redacted.pdf
uv run redact_pdf.py 1040.pdf

# choose the output name
uv run redact_pdf.py W2.pdf W2-for-accountant.pdf

# a different rules file
uv run redact_pdf.py 1099.pdf --terms-file ~/taxes/1099-terms.txt

# black out a signature in the lower part of page 2
uv run redact_pdf.py 1040.pdf --box 2:0.05,0.85,0.5,0.92
```

#### Options

| Option | What it does |
|---|---|
| `--terms-file FILE` | Read rules from `FILE` instead of the `terms.txt` next to the script |
| `--term 'old => new'` | Add a rule on the command line. You can repeat it. It ends up in your shell history, so prefer `terms.txt` for sensitive values |
| `--regex 'PATTERN => new'` | Add a rule that matches a [regular expression](https://docs.python.org/3/library/re.html) (case ignored). Leave out `=> new` to black out the match |
| `--box PAGE:x0,y0,x1,y1` | Black out an area: page number from 1, then coordinates from 0 to 1 measured from the top-left corner. `0.5` means halfway across or down. Use it for signatures, images and scanned pages. You can repeat it |
| `--white` | Fill black-out boxes with white instead of black |
| `--overflow N` | Let a replacement be up to `N` times the width of the text it replaces before it's shrunk to fit (default `1.0`) |
| `--min-size PT` | The smallest font size a replacement can be shrunk to (default `4`) |
| `--password PW` | Password for an encrypted PDF. If you leave it off, the script asks for it |
| `--remove-password` | Write the output without a password or permission restrictions (see [Passwords](#passwords)) |

### Passwords

If the input PDF has a password, the script asks for it, or you can pass `--password`. The output
**keeps the same password** unless you add `--remove-password`:

```sh
# redact and keep the password
uv run redact_pdf.py 1040.pdf

# redact and remove the password
uv run redact_pdf.py 1040.pdf --remove-password

# only remove the password: no active rules in terms.txt, writes 1040-unlocked.pdf
uv run redact_pdf.py 1040.pdf --remove-password
```

- **Unlock only:** this works when there are no rules at all. If `terms.txt` has active rules, they're
  still applied. To only unlock, comment them out or pass `--terms-file` with an empty file.
- **Permissions passwords:** `--remove-password` also removes a permissions password, which some PDFs
  use to block printing or copying.
- **You need the password:** the script can't remove a password you don't know.
- **The output also says what happened:** `password kept`, `password removed`, or
  `input had no password`.

### Reading the output

```
Using /Users/you/pdf-tools/terms.txt
'Jane Q Taxpayer' -> 'John Doe': 26 match(es)
'123-45-6789' -> 'XXX-XX-XXXX': 0 match(es)
'Taxpayer' -> 'Roe': 3 match(es), plus 26 inside a longer term, plus 16 hidden copies removed
NOTE: no match found for '123-45-6789' -> 'XXX-XX-XXXX' (check spelling / spacing).
OK: wrote 1040-redacted.pdf; none of the original text was found in it.
```

- Each rule is listed with its count, longest term first. **The output prints your terms**, so don't
  paste it anywhere you wouldn't paste `terms.txt`.
- **`plus N inside a longer term`**: those places were already handled by a longer rule (`Taxpayer`
  inside `Jane Q Taxpayer`), so only the longer rule's replacement is written there.
- **`hidden copies removed`**: some PDFs hold extra copies of text you can't see: on a hidden
  layer (such as a print-only one), clipped out of view, or placed off the page. They can still be
  copied out of the file, so the script removes them. Nothing is written in their place, because they
  were never visible.
- **`no match found`**: that rule didn't match anything. Usually the PDF writes it with different
  spacing or punctuation. This is only a note; it doesn't fail the run.
- **`OK`**: the script reopened the output and checked it with two PDF libraries, PyMuPDF and pypdf.
  None of the original text was found.
- **`WARNING: pages [...] have no text layer`**: those pages are scanned images, so the script can't
  find text on them. Cover what needs hiding with `--box`, and check those pages yourself.

#### When something is left over

If any original text can still be read from the output, the script lists how many instances are left,
on which pages, and why. It then prints `FAILED` and exits with code 1. The output file is still
written, but don't share it as is.

```
Still in the output:
  'Jane Q Taxpayer' -> 'John Doe': 2 left
    1 on page(s) 3: was marked for removal but the text is still there (font ABCDEF+Arial)
    1 on page(s) 7: in the file but not shown on the page (outside the page area or hidden); found by pypdf
FAILED: wrote 1040-redacted.pdf, but the text above is still in it. Don't share it as is.
```

| Reason | What it means | What to do |
|---|---|---|
| was marked for removal but the text is still there | The text was found and marked, but removing it didn't work. This is usually down to how the PDF draws that text (its font is named) | Black out the area with `--box`, or print the page to a new PDF first (see below) |
| formed by a replacement running into the text next to it | Your new text plus the text beside it spell an original term | Change the replacement, or add a rule for the surrounding text |
| not found when the original was searched | The text is there, but searching the original PDF didn't find it at that spot | Check the page; cover it with `--box` |
| a hidden copy (hidden layer, clipped, or off the page) that could not be removed | An invisible copy was found but removing it didn't work | Print the page to a new PDF first (see below) |
| in the file but stored in a way PyMuPDF can't read | Only pypdf could see this text, so the script couldn't remove it | Print the page to a new PDF first (see below) |

**Print to a new PDF:** in Preview, choose File > Export as PDF, or print with "Save as PDF". This
rebuilds the pages and drops hidden text. Then run the script on that copy. On a fillable form,
check that the field values survived the export.

### Limitations

- **Replacements are written in Helvetica**, so they may look slightly different from the
  document's own font.
- **Changed numbers aren't recalculated.** If you replace a figure, totals elsewhere stay as they were.
- **Scanned pages:** only `--box` works on them (see above).
- **Print-only and hidden fields are deleted.** Some forms have fields or notes that show only when
  printed, or never. Flattening would paint them onto the page, so they're deleted instead, and
  the run says how many. A printout of the output won't include them.
- **Tightly spaced lines:** only the middle of each matched line is removed, so lines above and
  below it aren't touched. If a match's letters overlap a neighbouring line's, check that spot by eye.
- **Text you didn't change can still move.** PyMuPDF's removal step had a bug that moved or dropped
  lines drawn with two particular PDF text commands. The script rewrites those commands into
  equivalent ones first, so the page looks the same and the bug is avoided. Other quirks may exist,
  and the check only looks for your terms, so it won't notice text that moved. Compare the pages by eye.
- **Letter-spaced text** (`S E C R E T`, where the PDF spreads out the letters) doesn't match a term.
  If the check reports it, cover it with `--box`.
- **Always look at the result before you share it.**

---

## extract_pdf.py

Pulls everything readable out of one or more PDFs: the text of each page, the values of any
fillable form fields, and a PNG image of each page. Use it to see what a PDF contains, to find the
exact spelling and spacing to put in `terms.txt`, or to check a redacted copy.

```
uv run extract_pdf.py FILE.pdf [FILE.pdf ...] [-o OUTDIR] [--dpi 110] [--no-images]
```

For each PDF it writes a folder `OUTDIR/<file name without .pdf>/` containing:

| File | Contents |
|---|---|
| `text.txt` | The text of every page, in reading order, with a `===== Page N =====` line before each page |
| `fields.txt` | Fillable form-field names and their values, by page. Written only if some fields are filled in |
| `page-001.png`, ... | An image of each page |

| Option | What it does |
|---|---|
| `-o`, `--outdir OUTDIR` | Where to write the folders (default `./extracted`) |
| `--dpi N` | Resolution of the page images (default `110`). Higher is sharper but larger |
| `--no-images` | Skip the page images and write only text and fields |

Examples:

```sh
# W-2 and 1099 into ./extracted/W2/ and ./extracted/1099/
uv run extract_pdf.py ~/taxes/W2.pdf ~/taxes/1099.pdf

# text only, into a folder of your choice
uv run extract_pdf.py ~/taxes/1040.pdf -o ~/taxes/extracted --no-images

# check a redacted copy: search its text for anything you meant to remove
uv run extract_pdf.py ~/taxes/1040-redacted.pdf --no-images
grep -i "taxpayer" extracted/1040-redacted/text.txt
```

An existing folder of the same name is written over. The output holds everything that was in the
PDF, so treat it as you would the PDF itself: delete it when you're done, and don't commit it
(`extracted/` is in `.gitignore`).
