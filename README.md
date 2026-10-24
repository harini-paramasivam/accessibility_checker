# AccessFix PDF

Local web app that (A) validates the **PDF tag structure** against the real PDF objects, and (B) detects **scanned pages** and runs **OCR** to add searchable text, then re-checks the result. Nothing is sent to third parties.

> Internal checker. Findings are **not** a PDF/UA or WCAG certification. OCR makes a PDF searchable; it does **not** make it tagged or accessible. Validate with PAC or veraPDF and test with a screen reader.

## 1. Requirements
* Python 3.10+ (tested on 3.12)
* **Tesseract OCR 5** with the language data you need (tested on 5.3.4)
* Optional for the full test suite: Playwright + Chromium, poppler-utils (`pdftotext`, `pdfinfo`)

### Install Tesseract (required for OCR)
| OS | Command |
|---|---|
| Ubuntu/Debian | `sudo apt install tesseract-ocr tesseract-ocr-eng` (more: `tesseract-ocr-tam`, `-hin`, `-deu` ...) |
| macOS | `brew install tesseract tesseract-lang` |
| Windows | Install the UB Mannheim build (https://github.com/UB-Mannheim/tesseract/wiki), tick extra languages, add to PATH or set `TESSERACT_CMD` |

Verify: `tesseract --version` and `tesseract --list-langs`. The app lists only languages that are actually installed. If Tesseract is missing the UI shows an error with these instructions and OCR is disabled (no fake output).

## 2. Setup and run
```bash
cd AccessFix-PDF
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                      # http://127.0.0.1:5050
```
Settings are environment variables (see `.env.example`): `ACCESSFIX_PORT`, `ACCESSFIX_MAX_UPLOAD_MB` (50), `ACCESSFIX_MAX_PAGES` (300), `ACCESSFIX_TTL_HOURS` (24), `ACCESSFIX_OCR_PAGE_TIMEOUT_S` (180), `ACCESSFIX_OCR_WORKERS`, `TESSERACT_CMD`, `TESSDATA_PREFIX`, `ACCESSFIX_CORS_ORIGINS` (empty = same-origin only).
`requirements-lock.txt` lists the exact versions the tests ran with. The built-in Flask server is single-process (job state is in memory); for several users put it behind one waitress/gunicorn worker with threads.

## 3. Using it
1. **Upload**: drop a PDF or use *Browse files*. The file is checked by content (`%PDF-` signature), size and page limits.
2. **Overview**: tag status (Tagged / Untagged / Partially tagged / Malformed), scan status and counts are shown separately.
3. **Findings**: filter by severity (error, warning, manual review, not performed); each has evidence, pages, standard references and a recommended fix. Fix controls (title, language, alt text, link/field descriptions, tab order, heading renumbering) always save a **new version**, then re-validate. Structural changes need a confirmation tick. The tool never writes alt text or tags by itself.
4. **Tag tree**: expand/collapse, search, filter by type, "only elements with findings", reading-order view per page, inspector (object id, marked content, alt, findings). Selecting an element highlights its area on the page preview. Arrow keys work.
5. **Scanned pages & OCR**: every page gets a class (native text, scanned, image + text, existing OCR layer ...) marked *definitive* or *heuristic*. Detected scans are pre-selected; choose pages, languages and resolution, then *Start*. Real progress, log, *Cancel* (no output written), *Retry failed pages*, per-page words and Tesseract confidence, text preview, post-OCR checks, **Download searchable PDF**.
6. **Before / after**: version table and resolved/new/unchanged findings. **Report (HTML/JSON)** and **Download PDF** are in the header.
7. **Untagged PDFs**: *Tagging workbench* (`/legacy/`, the original in-browser tool) offers heuristic auto-tagging for human review. *Send to AccessFix validator* re-checks its output with the structure engine.

## 4. What is checked (all internal, object-level)
Tag tree walk with RoleMap resolution (unmapped, circular, remapped types); `/P` links, duplicate/cyclic references, orphan elements; ParentTree vs. marked content in both directions (dangling, unreferenced, duplicate MCIDs, missing `/StructParents`); L/LI/Lbl/LBody and Table/THead/TBody/TFoot/TR/TH/TD parent-child rules; table regularity with ColSpan/RowSpan; TH presence/Scope; heading levels (skip, mixing, first level, empty); Figure/Formula alt text (missing, empty, filename-like); empty elements; links (Link tag, OBJR, `/Contents`, generic text); form fields (`/TU`, Form tag); annotations and tab order; title (Info + XMP), DisplayDocTitle, language; MarkInfo; fonts (embedding, Unicode mapping); untagged / double-marked content; extracted-text anomalies; bookmarks; encryption/accessibility permission; per-page scan classification.
Checks that **cannot** be done by a program are listed in every report as *Not performed*: colour contrast, correctness of reading order and alt text, language changes in text, complex table header association, full PDF/UA conformance.

## 5. OCR method and fidelity
Selected pages are rendered with pdfium, recognised by Tesseract (TSV word boxes and real confidences), and an **invisible text layer** (render mode 3, glyphless font with a ToUnicode map) is appended. Original content is wrapped in `q/Q`, not rewritten; pages are not rasterised or replaced. Existing tags, forms, links, bookmarks and native text stay. Optionally OCR words that overlap existing native text are skipped (no duplicates). Encrypted PDFs are refused with a message. After writing, the app verifies: page count/boxes/rotation, annotations, tag-tree equality, untouched pages' text, searchable text via independent extraction, and pixel-identical appearance.

## 6. Tests
```bash
pip install -r requirements-dev.txt     # reportlab, fonttools (+ playwright; then: playwright install chromium)
python -m unittest discover -s tests -v
```
See `TEST_RESULTS.md` for the executed run. UI tests are skipped automatically without Playwright; OCR tests without Tesseract.

## 7. Known limitations
* No independent PDF/UA validator (PAC, veraPDF) was available when this was built, so **no comparison against them was run**. The only independent cross-checks are poppler (`pdfinfo` Tagged flag, `pdftotext` text) and `qpdf` syntax checks. Compare results with PAC/veraPDF yourself; differences are expected, especially for Matterhorn conditions not implemented here.
* Standards references are orientation aids; some are at checkpoint level only. Verify against the published Matterhorn Protocol 1.1.
* The Python engine does **not** auto-tag untagged PDFs and does not rewrite a broken ParentTree. The browser Tagging workbench is heuristic; its tagging output was **not verified** in the build environment (it loads pdf.js, fontkit and Tesseract.js from CDNs, unavailable offline there; only its load/send wiring was exercised). Always re-validate its output.
* Reading order is checked by a geometric heuristic only.
* OCR tested with English only (only language installed). Non-BMP characters are skipped; vertical/right-to-left layouts and `UserUnit` are untested. OCR accuracy depends on scan quality; check low-confidence pages.
* Text inside Form XObjects is not read for the overlap filter. Large analyses stop at a time budget (reported as "not analysed").
* Job status is in memory (restart loses running jobs; stored documents remain until TTL). Uploaded files are deleted after `ACCESSFIX_TTL_HOURS`.
* UI tested in Chromium only.
* Legacy workbench needs internet (CDNs) as described in `static/legacy/README.txt`.
