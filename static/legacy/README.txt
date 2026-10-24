AccessFix PDF - PDF accessibility remediation (demo build)
==========================================================

How to run
----------
The app must be served over http(s) because it loads its font files and OCR model
from the fonts/ and tessdata/ folders. Opening index.html directly from disk will
not load those files.

  Option 1 (Node.js):   npx serve .        then open http://localhost:3000
  Option 2 (Python):    python -m http.server 8000   then open http://localhost:8000
  Option 3: upload the whole folder to any web host (Netlify, Vercel, S3, cPanel).

Internet access is needed on first load for the open-source libraries
(pdf.js, pdf-lib, fontkit, Tesseract.js) served from public CDNs.

What runs where
---------------
- Checking, tagging, font embedding, Unicode repair and OCR run in the browser.
  PDFs are not uploaded anywhere.
- The "Suggest with AI" buttons and AI alt text only work inside Claude, where the
  app is published as an artifact. In this standalone build they are hidden.

Scope and limits
----------------
- Automated checks cover machine-testable PDF/UA-1 (Matterhorn) and WCAG rules.
  The score is not a conformance claim. Validate output with PAC 2024 or veraPDF
  and a screen reader (NVDA/JAWS) before publishing a file as accessible.
- The PDF/UA identifier is only written when every machine check passes.
- Tables, formulas, headings and reading order are detected heuristically and
  listed under "Manual review" for a person to confirm.

Font licences
-------------
Liberation Sans/Serif/Mono: SIL Open Font License 1.1.
Carlito: SIL Open Font License 1.1. Caladea: Apache License 2.0.
Tesseract English model: Apache License 2.0.
