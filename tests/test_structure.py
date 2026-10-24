import unittest, sys
from common import *
import pikepdf


class TagStatusAndTree(unittest.TestCase):
    def test_good_tagged_has_real_tree_and_no_machine_errors(self):
        a = run_analyze(good())
        self.assertEqual(a["state"], "ok")
        self.assertEqual(a["tag_status"]["status"], "tagged")
        self.assertEqual(codes(a, ("error", "warning")), set(), [f["title"] for f in a["findings"] if f["severity"] in ("error", "warning")])
        types = a["structure"]["types"]
        for t, n in {"Document": 1, "H1": 1, "H2": 3, "Table": 1, "TH": 3, "TD": 6, "L": 1, "LI": 2, "Lbl": 2, "LBody": 2, "Figure": 1, "Link": 1}.items():
            self.assertEqual(types.get(t), n, t)
        self.assertEqual(a["structure"]["elements"], sum(types.values()))
        fig = next(n for n in a["structure"]["nodes"] if n["t"] == "Figure")
        self.assertTrue(fig["alt"].startswith("Bar chart"))
        self.assertEqual(fig["pg"], 2)
        self.assertIsNotNone(fig["dbox"])           # located on the page for highlighting

    def test_untagged_differs_from_tagged(self):
        t, g = run_analyze(untagged()), run_analyze(good())
        self.assertEqual(t["tag_status"]["status"], "untagged")
        self.assertIn("TAG-NONE", codes(t, "error"))
        self.assertEqual(t["structure"]["nodes"], [])
        self.assertFalse(t["structure"]["present"])
        self.assertIn("NOTRUN-STRUCT", codes(t, "not_run"))     # no fake "pass" for checks that need tags
        self.assertNotEqual(t["tag_status"]["status"], g["tag_status"]["status"])
        self.assertNotEqual(codes(t, "error"), codes(g, "error"))

    @unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
    def test_tagged_status_agrees_with_poppler(self):
        self.assertEqual(pdfinfo_tagged(good()), "yes")
        self.assertEqual(pdfinfo_tagged(untagged()), "no")


class StructuralDefects(unittest.TestCase):
    CASES = {
        "heading_skip": {"HEAD-SKIP"}, "heading_mixed": {"HEAD-MIXED"}, "bad_list": {"LIST-STRUCTURE", "LIST-NO-LBODY"},
        "bad_table": {"TABLE-STRUCTURE", "TABLE-IRREGULAR", "PARENT-CHILD"}, "table_no_th": {"TABLE-NO-TH"}, "figure_no_alt": {"FIG-NO-ALT"},
        "unmapped_role": {"ROLE-UNMAPPED"}, "circular_role": {"ROLE-CIRCULAR"}, "link_no_obj": {"LINK-UNTAGGED", "LINK-NO-CONTENTS"},
        "no_parenttree": {"PT-MISSING"}, "parenttree_mismatch": {"PT-MISMATCH"}, "dangling_mcid": {"MCID-DANGLING"},
        "untagged_content": {"CONTENT-UNTAGGED"}, "p_mismatch": {"STRUCT-PARENT-LINK"}, "duplicate_ref": {"STRUCT-DUPLICATE"},
        "cycle": {"STRUCT-CYCLE"}, "no_meta": {"META-TITLE", "META-LANG", "META-DDT", "NAV-TABS"}, "not_marked": {"MARKINFO"}, "not_embedded": {"FONT-NOT-EMBEDDED"},
    }

    def test_each_defect_is_detected_from_real_objects(self):
        for d, expected in self.CASES.items():
            with self.subTest(defect=d):
                a = run_analyze(defect(d))
                self.assertEqual(a["state"], "ok")
                missing = expected - codes(a)
                self.assertFalse(missing, f"{d}: missing {missing}; got {sorted(codes(a, ('error', 'warning')))}")

    def test_defect_does_not_cause_unrelated_errors(self):
        a = run_analyze(defect("heading_skip"))
        self.assertEqual(codes(a, ("error", "warning")), {"HEAD-SKIP"})
        a = run_analyze(defect("figure_no_alt"))
        self.assertEqual(codes(a, ("error", "warning")), {"FIG-NO-ALT", "FIG-EMPTY-ALT", "FIG-WEAK-ALT"})

    def test_findings_carry_evidence_and_locations(self):
        a = run_analyze(defect("heading_skip"))
        f = next(x for x in a["findings"] if x["code"] == "HEAD-SKIP")
        self.assertTrue(any("H1 is followed by H3" in e for e in f["evidence"]))
        self.assertEqual(f["pages"], [1])
        self.assertTrue(f["nodes"])
        n = a["structure"]["nodes"][f["nodes"][0]]
        self.assertEqual(n["t"], "H3")
        self.assertTrue(n["issues"])                        # linked back to the tree node
        self.assertIsNotNone(f["fix"]); self.assertTrue(f["fix"]["needs_confirmation"])
        self.assertTrue(f["refs"])

    def test_malformed_vs_partial_status(self):
        self.assertEqual(run_analyze(defect("cycle"))["tag_status"]["status"], "malformed")
        self.assertEqual(run_analyze(defect("parenttree_mismatch"))["tag_status"]["status"], "malformed")
        self.assertEqual(run_analyze(defect("untagged_content"))["tag_status"]["status"], "partially_tagged")

    def test_list_and_table_rules_use_parent_child_not_text(self):
        a = run_analyze(defect("bad_list"))
        f = next(x for x in a["findings"] if x["code"] == "LIST-STRUCTURE")
        self.assertTrue(any("contains P" in e and "L" in e for e in f["evidence"]))
        a = run_analyze(defect("bad_table"))
        f = next(x for x in a["findings"] if x["code"] == "TABLE-IRREGULAR")
        self.assertIn("row widths", f["evidence"][0])

    def test_alt_text_quality_is_flagged_as_heuristic_warning(self):
        a = run_analyze(defect("figure_no_alt"))
        w = next(x for x in a["findings"] if x["code"] == "FIG-WEAK-ALT")
        self.assertEqual(w["severity"], "warning")
        self.assertIn("manual", {x["severity"] for x in a["findings"] if x["code"] == "FIG-REVIEW"})

    def test_pdf20_style_unusual_tags_are_not_blindly_errors(self):
        # "Part", "Sect", "Art", "Div" are valid standard types and must not be flagged
        from common import fx, WORK
        b = fx.TaggedBuilder(1); doc = b.elem("Document"); sect = b.elem("Sect", doc, 0); div = b.elem("Div", sect, 0)
        b.text(0, div, "P", "Nested standard grouping elements", 72, 700)
        p = b.finish(WORK / "nested.pdf")
        self.assertEqual(codes(run_analyze(p), ("error", "warning")), set())


class UnsupportedDocuments(unittest.TestCase):
    def test_encrypted_password_required(self):
        p = fixture("enc.pdf", fx.encrypted_pdf)
        a = run_analyze(p)
        self.assertEqual(a["state"], "encrypted_locked"); self.assertIn("password", a["message"])

    def test_encrypted_blocks_accessibility(self):
        p = fixture("enc2.pdf", fx.encrypted_pdf, user="", owner="o", allow_access=False)
        a = run_analyze(p)
        self.assertEqual(a["state"], "ok"); self.assertTrue(a["file"]["encrypted"])
        self.assertIn("SEC-ACCESSIBILITY", codes(a, "error"))

    def test_corrupt_and_garbage(self):
        bad = WORK / "trunc.pdf"; bad.write_bytes(good().read_bytes()[:700])
        self.assertIn(run_analyze(bad)["state"], ("unreadable", "empty"))
        junk = WORK / "junk.pdf"; junk.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 20)
        self.assertEqual(run_analyze(junk)["state"], "unreadable")

    def test_zero_pages(self):
        p = WORK / "zero.pdf"; pdf = pikepdf.new(); pdf.save(str(p))
        self.assertEqual(run_analyze(p)["state"], "empty")

    def test_page_limit(self):
        a = run_analyze(untagged(), make_cfg(max_pages=1))
        self.assertEqual(a["state"], "too_many_pages")

    def test_large_multipage_mixed_content_is_fast_and_page_referenced(self):
        import time
        b = fx.TaggedBuilder(60); doc = b.elem("Document")
        for i in range(60):
            b.text(i, doc, "H1" if i == 0 else "P", f"Page {i + 1} heading text", 72, 700)
        p = b.finish(WORK / "sixty.pdf")
        t = time.time(); a = run_analyze(p)
        self.assertEqual(a["file"]["pages"], 60); self.assertEqual(len(a["pages"]), 60)
        self.assertLess(time.time() - t, 30)
        self.assertEqual({n["pg"] for n in a["structure"]["nodes"] if n["t"] != "Document"}, set(range(1, 61)))


if __name__ == "__main__":
    unittest.main()
