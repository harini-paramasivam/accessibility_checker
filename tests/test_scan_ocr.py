import json, shutil, unittest, time
from common import *
import pikepdf
from accessfix import ocr as ocrmod


def cls(a): return {p["page"]: p["class"] for p in a["pages"]}


class ScanDetection(unittest.TestCase):
    def test_image_only_page_is_definitive(self):
        a = run_analyze(scanned(1)); p = a["pages"][0]
        self.assertEqual(p["class"], "scan_no_text"); self.assertEqual(p["confidence"], "definitive")
        self.assertTrue(p["ocr_candidate"]); self.assertGreater(p["largest_image_coverage"], 0.95)
        self.assertEqual(a["scan"]["doc_class"], "image_only")
        self.assertIn("SCAN-NO-TEXT", codes(a, "error"))

    def test_mixed_document_is_classified_per_page(self):
        a = run_analyze(mixed())
        self.assertEqual(cls(a), {1: "native_text", 2: "scan_no_text", 3: "native_text"})   # one scan page must not condemn the rest
        self.assertEqual(a["scan"]["doc_class"], "mixed"); self.assertEqual(a["scan"]["candidate_pages"], [2])

    def test_native_document_has_no_candidates(self):
        a = run_analyze(untagged()); self.assertEqual(a["scan"]["candidate_pages"], []); self.assertEqual(a["scan"]["doc_class"], "native")

    def test_existing_ocr_layer_is_recognised_not_candidate(self):
        p = fixture("layer.pdf", fx.scanned_pdf, pages=1, invisible_text=["Quarterly revenue report invoice payable amount due March"])
        a = run_analyze(p); pg = a["pages"][0]
        self.assertEqual(pg["class"], "scan_ocr_layer"); self.assertFalse(pg["ocr_candidate"]); self.assertGreater(pg["invisible_chars"], 25)
        self.assertIn("OCR-LAYER-REVIEW", codes(a, "manual"))

    def test_rotated_scan_detected(self):
        p = fixture("rot.pdf", fx.scanned_pdf, pages=1, rotate=90)
        a = run_analyze(p); self.assertEqual(a["pages"][0]["class"], "scan_no_text"); self.assertEqual(a["pages"][0]["rotation"], 90)

    def test_tagged_doc_with_scanned_page(self):
        p = fixture("tagscan.pdf", fx.tagged_plus_scan); a = run_analyze(p)
        self.assertEqual(cls(a), {1: "native_text", 2: "native_text", 3: "native_text", 4: "scan_no_text"})
        self.assertEqual(a["tag_status"]["status"], "partially_tagged")     # scanned page has no tags: reported, not hidden


@unittest.skipUnless(HAVE_TESS, "tesseract not installed")
class OcrEngine(ApiCase):
    def run_ocr(self, doc, **kw):
        body = {"languages": "eng", "dpi": 200}; body.update(kw)
        r = self.ocr(doc, **body); self.assertEqual(r.status_code, 202, r.data)
        return self.wait(r.json["job"]["id"])

    def test_scanned_pdf_becomes_searchable(self):
        doc = self.upload_ok(scanned(2)); j = self.run_ocr(doc, pages="all")
        self.assertEqual(j["status"], "completed", j); v = j["result"]["version"]
        out = self.c.get(f"/api/documents/{doc}/download?version={v}").data
        f = WORK / "ocr_out.pdf"; f.write_bytes(out)
        before = pdftotext(scanned(2)); after = pdftotext(f)
        self.assertEqual(before.strip(), "")                                    # input really had no text
        for w in ("QUARTERLY", "Kowalski", "48213", "MEETING", "unanimously", "September"):
            self.assertIn(w, after, w)
        txt1 = pdftotext(f, 1, 1); self.assertIn("Invoice 48213 payable to Kowalski Brothers", txt1)   # phrase search works
        self.assertTrue(all(c["level"] != "fail" for c in j["result"]["checks"]), j["result"]["checks"])
        for p in j["result"]["per_page"]:
            self.assertEqual(p["status"], "ok"); self.assertGreater(p["words"], 8); self.assertGreater(p["mean_confidence"], 80)   # real tesseract confidences
        a = self.analysis(doc)                                                   # latest version
        self.assertEqual(a["tag_status"]["status"], "untagged")                  # searchable != tagged
        self.assertIn("OCR does not tag", j["result"]["accessibility_note"])
        self.assertIn("TAG-NONE", codes(a, "error")); self.assertEqual(cls(a), {1: "scan_ocr_layer", 2: "scan_ocr_layer"})
        self.assertEqual(self.analysis(doc, "v0")["pages"][0]["class"], "scan_no_text")      # original untouched
        orig = self.c.get(f"/api/documents/{doc}/download?version=v0").data
        self.assertEqual(orig, scanned(2).read_bytes())

    def test_mixed_pdf_native_text_and_other_pages_untouched(self):
        doc = self.upload_ok(mixed()); j = self.run_ocr(doc, pages="candidates")
        self.assertEqual(j["status"], "completed"); self.assertEqual([p["page"] for p in j["result"]["per_page"]], [2])
        f = WORK / "mixed_out.pdf"; f.write_bytes(self.c.get(f"/api/documents/{doc}/download?version={j['result']['version']}").data)
        for pg in (1, 3):
            self.assertEqual(pdftotext(mixed(), pg, pg), pdftotext(f, pg, pg))
        self.assertIn("Alpha Bravo Charlie", pdftotext(f, 1, 1)); self.assertIn("Delta Echo Foxtrot", pdftotext(f, 3, 3))
        self.assertIn("QUARTERLY REVENUE REPORT", pdftotext(f, 2, 2))
        with pikepdf.open(str(mixed())) as a, pikepdf.open(str(f)) as b:       # page content of page 1 and 3 identical objects
            for i in (0, 2):
                self.assertEqual(a.pages[i].obj.Contents.read_bytes(), b.pages[i].obj.Contents.read_bytes())

    def test_ocr_on_native_page_does_not_duplicate_text(self):
        doc = self.upload_ok(mixed()); j = self.run_ocr(doc, pages=[1])
        f = WORK / "dup_out.pdf"; f.write_bytes(self.c.get(f"/api/documents/{doc}/download?version={j['result']['version']}").data)
        t = pdftotext(f, 1, 1)
        self.assertEqual(t.count("Alpha"), 1); self.assertEqual(t.count("Ordinary running text"), 6)
        self.assertGreater(j["result"]["per_page"][0]["skipped_overlapping_native"], 5)

    def test_existing_tag_structure_survives_ocr(self):
        p = fixture("tagscan.pdf", fx.tagged_plus_scan); doc = self.upload_ok(p)
        before = self.analysis(doc)["structure"]
        j = self.run_ocr(doc, pages="candidates"); self.assertEqual(j["status"], "completed")
        after = self.analysis(doc)["structure"]
        self.assertEqual(before["types"], after["types"]); self.assertEqual(before["elements"], after["elements"])
        f = WORK / "tag_out.pdf"; f.write_bytes(self.c.get(f"/api/documents/{doc}/download?version={j['result']['version']}").data)
        with pikepdf.open(str(p)) as a, pikepdf.open(str(f)) as b:
            self.assertEqual(a.Root.StructTreeRoot.objgen, b.Root.StructTreeRoot.objgen)
            self.assertEqual(len(a.Root.StructTreeRoot.ParentTree.Nums), len(b.Root.StructTreeRoot.ParentTree.Nums))
        if HAVE_POPPLER: self.assertEqual(pdfinfo_tagged(f), "yes")
        self.assertTrue(any(c["name"] == "Existing tag structure preserved" and c["level"] == "pass" for c in j["result"]["checks"]))
        self.assertIn("Kowalski", pdftotext(f, 4, 4))
        self.assertIn("CONTENT-UNTAGGED", codes(self.analysis(doc), "error"))     # the new text layer is not in the tag tree: reported

    def test_rotated_page_text_is_searchable(self):
        p = fixture("rot.pdf", fx.scanned_pdf, pages=1, rotate=90); doc = self.upload_ok(p)
        j = self.run_ocr(doc, pages="all"); self.assertEqual(j["status"], "completed", j)
        f = WORK / "rot_out.pdf"; f.write_bytes(self.c.get(f"/api/documents/{doc}/download?version={j['result']['version']}").data)
        self.assertIn("Kowalski", pdftotext(f)); self.assertTrue(all(c["level"] != "fail" for c in j["result"]["checks"]))

    def test_cancel_writes_no_output(self):
        doc = self.upload_ok(fixture("scan6.pdf", fx.scanned_pdf, pages=6))
        r = self.ocr(doc, pages="all", languages="eng", dpi=300); jid = r.json["job"]["id"]
        self.c.post(f"/api/jobs/{jid}/cancel"); j = self.wait(jid)
        self.assertEqual(j["status"], "canceled")
        self.assertEqual(len(self.c.get(f"/api/documents/{doc}").json["versions"]), 1)

    def test_timeout_is_reported_per_page(self):
        self.cfg.ocr_page_timeout_s = 0.001
        doc = self.upload_ok(scanned(1)); r = self.ocr(doc, pages="all", languages="eng", dpi=200); j = self.wait(r.json["job"]["id"])
        self.assertEqual(j["status"], "failed"); self.assertIn("limit", json.dumps(j))

    def test_blank_or_unreadable_page_is_flagged_not_faked(self):
        p = WORK / "blank.pdf"; pdf = pikepdf.new(); pdf.add_blank_page(); pdf.save(str(p))
        doc = self.upload_ok(p); j = self.run_ocr(doc, pages=[1])
        pp = j["result"]["per_page"][0]; self.assertEqual(pp["words"], 0); self.assertTrue(any("no text" in f for f in pp["flags"]))
        self.assertIsNone(pp["mean_confidence"])                                  # no confidence invented

    def test_missing_language_and_bad_input(self):
        doc = self.upload_ok(scanned(1))
        r = self.ocr(doc, pages="all", languages="xyz"); self.assertEqual(r.status_code, 400); self.assertEqual(r.json["error"]["code"], "missing_language"); self.assertIn("eng", r.json["error"]["message"])
        self.assertEqual(self.ocr(doc, pages=[99], languages="eng").status_code, 400)
        self.assertEqual(self.ocr(doc, pages="all", languages="eng;rm -rf", dpi=200).status_code, 400)
        self.assertEqual(self.ocr(doc, pages="all", languages="eng", dpi=9999).status_code, 400)
        r = self.ocr(self.upload_ok(untagged()), pages="candidates", languages="eng"); self.assertEqual(r.status_code, 400)   # nothing to OCR

    def test_encrypted_document_cannot_be_ocrd(self):
        p = fixture("enc3.pdf", fx.encrypted_pdf, user="", owner="o"); doc = self.upload_ok(p)
        r = self.ocr(doc, pages="all", languages="eng"); self.assertEqual(r.status_code, 422); self.assertEqual(r.json["error"]["code"], "encrypted")

    def test_text_layer_search_phrases_through_two_independent_extractors(self):
        doc = self.upload_ok(scanned(1)); j = self.run_ocr(doc, pages="all")
        f = WORK / "two.pdf"; f.write_bytes(self.c.get(f"/api/documents/{doc}/download?version={j['result']['version']}").data)
        r = self.c.get(f"/api/documents/{doc}/pages/1/text?version={j['result']['version']}").json
        self.assertIn("Kowalski Brothers", r["text"].replace("\r\n", " ").replace("\n", " "))   # pdfium
        self.assertIn("Kowalski Brothers", pdftotext(f).replace("\n", " "))                    # poppler


class EngineUnavailable(ApiCase):
    cfg_kw = {"tesseract_cmd": "/nonexistent/tesseract"}

    def test_actionable_error_when_tesseract_missing(self):
        doc = self.upload_ok(scanned(1))
        self.assertFalse(self.c.get("/api/capabilities").json["ocr"]["available"])
        r = self.ocr(doc, pages="all", languages="eng"); self.assertEqual(r.status_code, 503)
        e = r.json["error"]; self.assertEqual(e["code"], "engine_unavailable"); self.assertIn("Install", e["message"]); self.assertIn("ubuntu/debian", e["install_help"])
        self.assertEqual(len(self.c.get(f"/api/documents/{doc}").json["versions"]), 1)          # no fake output

if __name__ == "__main__":
    unittest.main()
