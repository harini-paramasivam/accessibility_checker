import hashlib, unittest
from common import *


class Remediation(ApiCase):
    def fix(self, doc, actions, confirm=False, version=None):
        body = {"actions": actions, "confirm": confirm}
        if version: body["version"] = version
        return self.c.post(f"/api/documents/{doc}/remediate", json=body)

    def test_metadata_fixes_then_revalidation(self):
        doc = self.upload_ok(defect("no_meta")); before = self.analysis(doc)
        self.assertTrue({"META-TITLE", "META-LANG", "META-DDT", "NAV-TABS"} <= codes(before, "error"))
        r = self.fix(doc, [{"action": "set_title", "params": {"value": "Quarterly Review"}}, {"action": "set_lang", "params": {"value": "en-GB"}},
                           {"action": "set_display_doc_title"}, {"action": "set_tabs_structure"}])
        self.assertEqual(r.status_code, 201, r.json); v = r.json["version"]["id"]
        after = self.analysis(doc, v)
        self.assertFalse({"META-TITLE", "META-LANG", "META-DDT", "NAV-TABS"} & codes(after))
        self.assertEqual(after["file"]["title"], "Quarterly Review"); self.assertEqual(after["file"]["language"], "en-GB"); self.assertEqual(after["file"]["xmp_title"], "Quarterly Review")
        self.assertEqual(len(r.json["comparison"]["resolved"]), 4); self.assertEqual(r.json["comparison"]["new"], [])
        self.assertEqual(self.c.get(f"/api/documents/{doc}/download?version=v0").data, defect("no_meta").read_bytes())     # original preserved
        self.assertEqual(self.analysis(doc, "v0")["counts"], before["counts"])
        self.assertEqual(after["tag_status"]["status"], "tagged")                    # tag structure untouched

    def test_structural_change_needs_confirmation(self):
        doc = self.upload_ok(defect("heading_skip")); f = next(x for x in self.analysis(doc)["findings"] if x["code"] == "HEAD-SKIP")
        act = [{"action": f["fix"]["action"], "params": f["fix"]["params"]}]
        r = self.fix(doc, act); self.assertEqual(r.status_code, 422)                 # not confirmed -> nothing written
        self.assertEqual(len(self.c.get(f"/api/documents/{doc}").json["versions"]), 1)
        r = self.fix(doc, act, confirm=True); self.assertEqual(r.status_code, 201)
        a = self.analysis(doc, r.json["version"]["id"]); self.assertNotIn("HEAD-SKIP", codes(a))
        self.assertEqual([h["level"] for h in a["structure"]["headings"]], [1, 2])

    def test_alt_text_comes_from_the_user_only(self):
        doc = self.upload_ok(defect("figure_no_alt")); st = self.c.get(f"/api/documents/{doc}/structure").json["structure"]["nodes"]
        fig = next(n for n in st if n["t"] == "Figure" and n["alt"] is None)
        self.assertEqual(self.fix(doc, [{"action": "set_figure_alt", "params": {"items": [{"obj": fig["obj"], "text": "  "}]}}]).status_code, 422)   # blank refused
        r = self.fix(doc, [{"action": "set_figure_alt", "params": {"items": [{"obj": fig["obj"], "text": "Blue rectangle used as a placeholder graphic"}]}}])
        self.assertEqual(r.status_code, 201); a = self.analysis(doc, r.json["version"]["id"]); self.assertNotIn("FIG-NO-ALT", codes(a))

    def test_never_invents_structure(self):
        doc = self.upload_ok(untagged())
        r = self.fix(doc, [{"action": "set_mark_info"}]); self.assertEqual(r.status_code, 422); self.assertIn("no structure tree", r.json["error"]["message"])
        self.assertEqual(self.fix(doc, [{"action": "set_struct_type", "params": {"items": [{"obj": "1 0", "type": "H1"}]}}], confirm=True).status_code, 422)
        a = self.analysis(doc); self.assertEqual(a["tag_status"]["status"], "untagged"); self.assertEqual(len(self.c.get(f"/api/documents/{doc}").json["versions"]), 1)

    def test_validation_of_inputs(self):
        doc = self.upload_ok(defect("no_meta"))
        for act in ({"action": "set_lang", "params": {"value": "not a lang!"}}, {"action": "set_title", "params": {"value": ""}}, {"action": "nope"},
                    {"action": "set_figure_alt", "params": {"items": [{"obj": "9999 0", "text": "x"}]}}):
            self.assertEqual(self.fix(doc, [act]).status_code, 422, act)
        self.assertEqual(self.fix(doc, []).status_code, 400)

    def test_markinfo_and_xmp_sync(self):
        doc = self.upload_ok(defect("not_marked")); a = self.analysis(doc); self.assertIn("MARKINFO", codes(a, "error"))
        r = self.fix(doc, [{"action": "set_mark_info"}], confirm=True); self.assertEqual(r.status_code, 201)
        self.assertNotIn("MARKINFO", codes(self.analysis(doc, r.json["version"]["id"])))

    def test_clean_pt_orphans_fixes_malformed_integrity(self):
        doc = self.upload_ok(defect("pt_orphan"))
        a = self.analysis(doc)
        self.assertEqual(a["tag_status"]["status"], "malformed")
        self.assertIn("PT-ORPHAN", codes(a, "error"))
        r = self.fix(doc, [{"action": "clean_pt_orphans"}])
        self.assertEqual(r.status_code, 201)
        a_fixed = self.analysis(doc, r.json["version"]["id"])
        self.assertNotIn("PT-ORPHAN", codes(a_fixed))
        self.assertEqual(a_fixed["tag_status"]["status"], "tagged")

    def test_chained_versions_and_compare_endpoint(self):
        doc = self.upload_ok(defect("no_meta"))
        v1 = self.fix(doc, [{"action": "set_lang", "params": {"value": "en-US"}}]).json["version"]["id"]
        v2 = self.fix(doc, [{"action": "set_title", "params": {"value": "T"}}]).json["version"]["id"]
        self.assertEqual((v1, v2), ("v1", "v2")); c = self.c.get(f"/api/documents/{doc}/compare?from=v0&to=v2").json
        self.assertEqual({x["code"] for x in c["resolved"]}, {"META-LANG", "META-TITLE"})
        self.assertIn("Resolved", self.c.get(f"/api/documents/{doc}/report?format=html&version=v2").data.decode())

if __name__ == "__main__":
    unittest.main()
