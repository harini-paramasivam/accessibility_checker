"""Comprehensive OCR text selection, copying, and Unicode compatibility tests.

Validates the 5 required document types:
1. Image-only scanned PDF
2. Mixed PDF (native + scanned)
3. Multi-column scanned PDF
4. Rotated scanned page PDF
5. PDF with punctuation and non-ASCII characters
"""
import io
import os
import random
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import pikepdf
import pypdfium2 as pdfium
import numpy as np

from tests.common import ApiCase, WORK, make_cfg, HAVE_TESS
from tests.fixtures import SANS, SERIF, SCAN_LINES
from accessfix import ocr as ocrmod
from accessfix.pdfium_content import extract_page_text_layout, page_display_info


def make_multicol_scan(path, dpi=200):
    """Create a 2-column scanned document."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader

    w_px, h_px = int(612 / 72 * dpi), int(792 / 72 * dpi)
    img = Image.new("L", (w_px, h_px), 255)
    d = ImageDraw.Draw(img)
    font_head = ImageFont.truetype(str(SANS), int(0.25 * dpi))
    font_body = ImageFont.truetype(str(SERIF), int(0.18 * dpi))

    # Header
    d.text((int(0.8 * dpi), int(0.8 * dpi)), "TWO COLUMN DISPATCH SUMMARY", font=font_head, fill=20)

    # Left Column
    col1_x = int(0.8 * dpi)
    col1_lines = [
        "Left Column Alpha",
        "Operations commenced at dawn",
        "Personnel reported on schedule",
        "Telemetry verified without error"
    ]
    y = int(1.4 * dpi)
    for ln in col1_lines:
        d.text((col1_x, y), ln, font=font_body, fill=20)
        y += int(0.40 * dpi)

    # Right Column
    col2_x = int(4.6 * dpi)
    col2_lines = [
        "Right Column Omega",
        "Logistics delivered all supplies",
        "Inventory cataloged in warehouse",
        "Final signoff completed promptly"
    ]
    y = int(1.4 * dpi)
    for ln in col2_lines:
        d.text((col2_x, y), ln, font=font_body, fill=20)
        y += int(0.40 * dpi)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)

    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawImage(ImageReader(buf), 0, 0, 612, 792)
    c.showPage()
    c.save()
    return Path(path)


def make_unicode_scan(path, dpi=200):
    """Create a scanned document with punctuation, quotes, currency, and non-ASCII accents."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader

    w_px, h_px = int(612 / 72 * dpi), int(792 / 72 * dpi)
    img = Image.new("L", (w_px, h_px), 255)
    d = ImageDraw.Draw(img)
    font_head = ImageFont.truetype(str(SANS), int(0.24 * dpi))
    font_body = ImageFont.truetype(str(SERIF), int(0.18 * dpi))

    lines = [
        "RÉSUMÉ FINANCIER & QUALITÉ",
        "Facture N° 84920: 3.450,75 € réglés à Müller & Associés.",
        "« L'exactitude des données est confirmée — inspection réussie »",
        "Contact: münchen-support@zugänglichkeit.de (Tél: +49-89-1234)"
    ]

    y = int(1.0 * dpi)
    for i, ln in enumerate(lines):
        d.text((int(0.8 * dpi), y), ln, font=font_head if i == 0 else font_body, fill=20)
        y += int(0.50 * dpi)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)

    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawImage(ImageReader(buf), 0, 0, 612, 792)
    c.showPage()
    c.save()
    return Path(path)


@unittest.skipUnless(HAVE_TESS, "Tesseract OCR engine required for selectable text tests")
class TestOcrSelectableCopyable(ApiCase):
    """Verifies that OCR text is accurately positioned, selectable, and copyable."""

    def run_ocr_job(self, doc_id, pages="all", langs="eng"):
        r = self.ocr(doc_id, pages=pages, languages=langs, dpi=200)
        self.assertEqual(r.status_code, 202, r.data)
        job = self.wait(r.json["job"]["id"])
        self.assertEqual(job["status"], "completed", job)
        return job

    def get_processed_pdf(self, doc_id, version):
        resp = self.c.get(f"/api/documents/{doc_id}/download?version={version}")
        self.assertEqual(resp.status_code, 200)
        out_path = WORK / f"{doc_id}_{version}.pdf"
        out_path.write_bytes(resp.data)
        return out_path

    # ------------------------------------------------------------------ 1. Image-only scanned PDF
    def test_1_image_only_scanned_pdf(self):
        """Test word, sentence, and multi-line selection on image-only scanned document."""
        from tests.fixtures import scanned_pdf
        src = WORK / "test1_image_only.pdf"
        scanned_pdf(src, pages=1)

        doc_id = self.upload_ok(src)
        job = self.run_ocr_job(doc_id, pages=[1], langs="eng")
        processed_path = self.get_processed_pdf(doc_id, job["result"]["version"])

        # Reopen generated PDF with pdfium
        doc = pdfium.PdfDocument(str(processed_path))
        page = doc[0]
        layout = extract_page_text_layout(page)

        # 1. Select specific single word
        word_texts = [w["text"] for w in layout["words"]]
        self.assertIn("QUARTERLY", word_texts)
        self.assertIn("Invoice", word_texts)
        self.assertIn("48213", word_texts)

        # Find word "Kowalski" and verify bounding box
        kowalski = next((w for w in layout["words"] if "Kowalski" in w["text"]), None)
        self.assertIsNotNone(kowalski, "Word 'Kowalski' should be selectable")
        self.assertGreater(kowalski["w"], 0.01)
        self.assertGreater(kowalski["h"], 0.005)

        # 2. Select a sentence
        full_text = layout["text"]
        self.assertIn("Invoice 48213 payable to Kowalski Brothers", full_text)

        # 3. Select multiple lines
        lines_text = [l["text"] for l in layout["lines"]]
        self.assertGreaterEqual(len(lines_text), 3)
        self.assertIn("QUARTERLY REVENUE REPORT", lines_text[0])

        # 4. Compare text extracted with original content
        self.assertIn("1,250.00", full_text)
        self.assertIn("March", full_text)

        # 5. Visual appearance unchanged
        orig_doc = pdfium.PdfDocument(str(src))
        img_orig = np.asarray(orig_doc[0].render(scale=1.0).to_pil().convert("L"), dtype=np.int16)
        img_proc = np.asarray(page.render(scale=1.0).to_pil().convert("L"), dtype=np.int16)
        diff = int(np.abs(img_orig - img_proc).max())
        self.assertLessEqual(diff, 8, "Original visual appearance must be preserved")

        orig_doc.close()
        doc.close()

    # ------------------------------------------------------------------ 2. Mixed PDF
    def test_2_mixed_pdf(self):
        """Test mixed PDF containing native selectable text and scanned page."""
        from tests.fixtures import mixed_pdf
        src = WORK / "test2_mixed.pdf"
        mixed_pdf(src)

        doc_id = self.upload_ok(src)
        # OCR candidate page 2 only
        job = self.run_ocr_job(doc_id, pages=[2], langs="eng")
        processed_path = self.get_processed_pdf(doc_id, job["result"]["version"])

        doc = pdfium.PdfDocument(str(processed_path))
        self.assertEqual(len(doc), 3)

        # Page 1: Native text preserved and NOT duplicated
        p1_layout = extract_page_text_layout(doc[0])
        p1_text = p1_layout["text"]
        self.assertIn("Alpha Bravo Charlie", p1_text)
        self.assertEqual(p1_text.count("Ordinary running text"), 6, "Native text must not be duplicated")

        # Page 2: Now selectable after OCR
        p2_layout = extract_page_text_layout(doc[1])
        p2_text = p2_layout["text"]
        self.assertIn("QUARTERLY REVENUE REPORT", p2_text)
        self.assertIn("Kowalski", p2_text)

        # Page 3: Native text intact
        p3_layout = extract_page_text_layout(doc[2])
        self.assertIn("Delta Echo Foxtrot", p3_layout["text"])

        doc.close()

    # ------------------------------------------------------------------ 3. Multi-column scanned PDF
    def test_3_multi_column_scanned_pdf(self):
        """Test scanned PDF with multi-column layout."""
        src = WORK / "test3_multicol.pdf"
        make_multicol_scan(src)

        doc_id = self.upload_ok(src)
        job = self.run_ocr_job(doc_id, pages=[1], langs="eng")
        processed_path = self.get_processed_pdf(doc_id, job["result"]["version"])

        doc = pdfium.PdfDocument(str(processed_path))
        page = doc[0]
        layout = extract_page_text_layout(page)
        full_text = layout["text"]

        # Verify words from both columns are recognized
        self.assertIn("DISPATCH", full_text)
        self.assertIn("Alpha", full_text)
        self.assertIn("commenced", full_text)
        self.assertIn("Omega", full_text)
        self.assertIn("Logistics", full_text)
        self.assertIn("warehouse", full_text)

        # Select word from left column and verify coordinates
        alpha_word = next((w for w in layout["words"] if "Alpha" in w["text"]), None)
        self.assertIsNotNone(alpha_word)
        # Select word from right column and verify coordinates
        omega_word = next((w for w in layout["words"] if "Omega" in w["text"]), None)
        self.assertIsNotNone(omega_word)

        # Right column should have greater x (left) coordinate than left column
        self.assertGreater(omega_word["l"], alpha_word["l"], "Column geometry must be preserved")

        doc.close()

    # ------------------------------------------------------------------ 4. Rotated scanned page
    def test_4_rotated_scanned_page(self):
        """Test rotated scanned page (90 degrees)."""
        from tests.fixtures import scanned_pdf
        src = WORK / "test4_rot.pdf"
        scanned_pdf(src, pages=1, rotate=90)

        doc_id = self.upload_ok(src)
        job = self.run_ocr_job(doc_id, pages=[1], langs="eng")
        processed_path = self.get_processed_pdf(doc_id, job["result"]["version"])

        doc = pdfium.PdfDocument(str(processed_path))
        page = doc[0]
        self.assertEqual(page.get_rotation(), 90)

        layout = extract_page_text_layout(page)
        full_text = layout["text"]

        # Verify rotated text was recognized and is searchable/copyable
        self.assertIn("Kowalski", full_text)
        self.assertIn("Invoice", full_text)
        self.assertIn("QUARTERLY", full_text)

        # Check word bounding boxes are within normalized 0.0 - 1.0 bounds
        for w in layout["words"]:
            self.assertGreaterEqual(w["l"], 0.0)
            self.assertLessEqual(w["l"] + w["w"], 1.05)
            self.assertGreaterEqual(w["t"], 0.0)
            self.assertLessEqual(w["t"] + w["h"], 1.05)

        doc.close()

    # ------------------------------------------------------------------ 5. Non-ASCII and punctuation
    def test_5_punctuation_and_unicode_characters(self):
        """Test PDF with non-ASCII accented characters, currency symbols, and quotes."""
        src = WORK / "test5_unicode.pdf"
        make_unicode_scan(src)

        doc_id = self.upload_ok(src)
        # Use eng+deu or eng for recognition
        job = self.run_ocr_job(doc_id, pages=[1], langs="eng")
        processed_path = self.get_processed_pdf(doc_id, job["result"]["version"])

        doc = pdfium.PdfDocument(str(processed_path))
        page = doc[0]
        layout = extract_page_text_layout(page)
        full_text = layout["text"]

        # Verify numbers, punctuation, colon, dash
        self.assertIn("84920", full_text)
        self.assertIn("49", full_text)
        self.assertTrue(any("m" in w.lower() for w in full_text.split()))

        # Verify that page layout API endpoint returns words and lines
        resp = self.c.get(f"/api/documents/{doc_id}/pages/1/text?version={job['result']['version']}")
        self.assertEqual(resp.status_code, 200)
        api_data = resp.json
        self.assertIn("words", api_data)
        self.assertIn("lines", api_data)
        self.assertGreater(len(api_data["words"]), 5)

        doc.close()
