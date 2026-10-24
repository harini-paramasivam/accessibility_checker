"""Standalone OCR Smoke Test for AccessFix PDF.

Performs a real end-to-end OCR test:
1. Verifies Tesseract engine info and language packs.
2. Creates a realistic scanned document (image-only raster with text and noise).
3. Validates scanned-page classification.
4. Executes real Tesseract OCR to generate an invisible text layer.
5. Verifies output text extraction and appearance preservation.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Ensure workspace root is in sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accessfix.config import Config
from accessfix import ocr as ocrmod
from accessfix.analyze import analyze
from tests import fixtures as fx
from tests.common import pdftotext


import threading

class DummyJob:
    """Mock job tracking for progress and logs."""
    def __init__(self):
        self.id = "smoke-ocr-test"
        self.logs = []
        self.total = 0
        self.done = 0
        self.procs = set()
        self.cancel = threading.Event()

    def progress(self, done, total, msg=""):
        self.done, self.total = done, total
        print(f"  [Progress {done}/{total}] {msg}")

    def log(self, level, msg):
        self.logs.append((level, msg))
        print(f"  [{level.upper()}] {msg}")


def run_smoke_test():
    print("=" * 60)
    print(" ACCESSFIX PDF - OCR SMOKE TEST")
    print("=" * 60)

    cfg = Config()
    print(f"\n1. Checking Tesseract OCR Engine Configuration:")
    print(f"   - Configured cmd: {cfg.tesseract_cmd}")
    print(f"   - Configured tessdata: {cfg.tessdata_dir or '(default)'}")

    info = ocrmod.engine_info(cfg)
    print(f"   - Detected: {info['available']}")
    print(f"   - Version:  {info['version']}")
    print(f"   - Language: {info['languages']}")

    if not info["available"]:
        print(f"\n[FAIL] Tesseract engine is unavailable: {info.get('error')}")
        sys.exit(1)

    assert "eng" in info["languages"], f"English language pack missing: {info['languages']}"
    print("   -> Tesseract engine verified OK")

    work_dir = Path(tempfile.mkdtemp(prefix="afx-smoke-"))
    scanned_path = work_dir / "sample_scanned.pdf"
    ocr_out_path = work_dir / "sample_ocr_searchable.pdf"

    print(f"\n2. Creating Sample Scanned PDF Fixture:")
    fx.scanned_pdf(scanned_path, pages=1, dpi=200)
    print(f"   - Created {scanned_path.name} ({scanned_path.stat().st_size} bytes)")

    # Verify input truly contains no extractable text
    before_text = pdftotext(scanned_path)
    print(f"   - Native extractable text before OCR: '{before_text.strip()}' (expected empty)")
    assert before_text.strip() == "", "Input fixture unexpectedly contains native text"

    print(f"\n3. Testing Scanned-PDF Detection:")
    analysis_res = analyze(scanned_path, cfg=cfg)
    p0 = analysis_res["pages"][0]
    print(f"   - Page 1 class:      {p0['class']}")
    print(f"   - Confidence:        {p0['confidence']}")
    print(f"   - OCR Candidate:     {p0['ocr_candidate']}")
    print(f"   - Document class:    {analysis_res['scan']['doc_class']}")
    assert p0["class"] == "scan_no_text", f"Expected scan_no_text, got {p0['class']}"
    assert p0["ocr_candidate"] is True, "Expected page to be flagged as OCR candidate"
    print("   -> Scanned-PDF detection verified OK")

    print(f"\n4. Executing Tesseract OCR on Scanned Page:")
    job = DummyJob()
    t0 = time.time()
    summary, results = ocrmod.run_ocr(
        scanned_path,
        ocr_out_path,
        [0],
        "eng",
        200,
        3,
        cfg,
        job,
        skip_overlap=True,
    )
    elapsed = time.time() - t0
    print(f"   - OCR Execution completed in {elapsed:.2f}s")
    print(f"   - Pages OK:     {summary['pages_ok']}")

    pinfo = summary["per_page"][0]
    print(f"   - Page 1 words: {pinfo['words']}")
    print(f"   - Page 1 conf:  {pinfo['mean_confidence']:.1f}%")

    assert summary["pages_ok"] == 1, "OCR did not succeed on page 1"
    assert pinfo["words"] >= 10, f"Expected at least 10 words, got {pinfo['words']}"
    assert pinfo["mean_confidence"] > 80.0, f"Confidence too low: {pinfo['mean_confidence']}"
    print("   -> Real Tesseract text recognition verified OK")

    print(f"\n5. Verifying Searchable Text Layer and Appearance:")
    checks = ocrmod.verify_output(scanned_path, ocr_out_path, results, summary["per_page"])
    for c in checks:
        print(f"   - [{c['level'].upper()}] {c['name']}: {c['detail']}")
        assert c["level"] != "fail", f"Verification check failed: {c['name']} - {c['detail']}"

    # Extract text from output PDF
    after_text = pdftotext(ocr_out_path)
    print(f"\n6. Extracted Searchable Text:")
    for line in after_text.splitlines():
        if line.strip():
            print(f"     \"{line.strip()}\"")

    expected_phrases = ["QUARTERLY REVENUE REPORT", "Invoice 48213", "Kowalski Brothers"]
    for phrase in expected_phrases:
        assert phrase in after_text, f"Expected phrase '{phrase}' missing from searchable PDF text"
        print(f"   [FOUND] '{phrase}'")

    print("\n" + "=" * 60)
    print(" OCR SMOKE TEST PASSED: ALL ASSERTIONS VERIFIED!")
    print("=" * 60)


if __name__ == "__main__":
    run_smoke_test()
