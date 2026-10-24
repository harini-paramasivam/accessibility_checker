import hashlib, io, json, os, time, unittest
from common import *
from accessfix.store import safe_name


class Upload(ApiCase):
    def test_valid_upload_analysis_report_download_cycle(self):
        doc = self.upload_ok(good(), "My Report.pdf")
        m = self.c.get(f"/api/documents/{doc}").json
        self.assertEqual(m["name"], "My Report.pdf"); self.assertEqual(len(m["versions"]), 1)
        a = self.analysis(doc); self.assertEqual(a["tag_status"]["status"], "tagged")
        self.assertNotIn("nodes", a["structure"])                                   # slim endpoint
        st = self.c.get(f"/api/documents/{doc}/structure").json["structure"]; self.assertEqual(len(st["nodes"]), a["structure"]["elements"])
        r = self.c.get(f"/api/documents/{doc}/download"); self.assertEqual(r.data, good().read_bytes()); self.assertIn("attachment", r.headers["Content-Disposition"])
        rj = self.c.get(f"/api/documents/{doc}/report?format=json").json
        self.assertEqual(rj["file"]["sha256"], hashlib.sha256(good().read_bytes()).hexdigest()); self.assertEqual(rj["counts"], a["counts"])
        self.assertIn("not a PDF/UA or WCAG conformance certification", rj["disclaimer"])
        self.assertTrue(rj["checks_performed"]); self.assertTrue(any(f["severity"] == "not_run" for f in rj["findings"]))
        h = self.c.get(f"/api/documents/{doc}/report?format=html"); self.assertIn(b"Checks performed", h.data); self.assertIn(b"Not performed", h.data)
        img = self.c.get(f"/api/documents/{doc}/pages/2/image?w=300"); self.assertEqual(img.data[:4], b"\x89PNG")
        self.assertEqual(self.c.get(f"/api/documents/{doc}/pages/9/image").status_code, 404)

    def test_rejects_by_content_not_name(self):
        self.assertEqual(self.upload(__file__, "innocent.pdf").status_code, 415)
        r = self.c.post("/api/documents", data={"file": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 100), "x.pdf")}); self.assertEqual(r.status_code, 415)
        r = self.c.post("/api/documents", data={"file": (io.BytesIO(b""), "e.pdf")}); self.assertEqual(r.status_code, 400); self.assertEqual(r.json["error"]["code"], "empty_file")
        self.assertEqual(self.c.post("/api/documents", data={}).status_code, 400)
        self.assertEqual(len(os.listdir(self.cfg.data_dir / "docs")), 0)             # nothing stored for rejected files

    def test_pdf_signature_but_corrupt_gives_explicit_state(self):
        r = self.c.post("/api/documents", data={"file": (io.BytesIO(b"%PDF-1.7\n" + os.urandom(2000)), "bad.pdf")}); self.assertEqual(r.status_code, 201)
        self.wait(r.json["job"]["id"]); a = self.analysis(r.json["document"]["id"])
        self.assertEqual(a["state"], "unreadable"); self.assertIn("could not be parsed", a["message"])
        d = r.json["document"]["id"]
        self.assertEqual(self.c.post(f"/api/documents/{d}/ocr", json={"pages": "all"}).status_code, 422)

    def test_encrypted_and_empty_states_via_api(self):
        d = self.upload_ok(fixture("enc.pdf", fx.encrypted_pdf)); self.assertEqual(self.analysis(d)["state"], "encrypted_locked")
        p = WORK / "zero.pdf"
        if not p.exists():
            import pikepdf; pikepdf.new().save(str(p))
        self.assertEqual(self.analysis(self.upload_ok(p))["state"], "empty")

    def test_too_large(self):
        self.cfg.max_upload_mb = 1; self.app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 + 4096
        r = self.c.post("/api/documents", data={"file": (io.BytesIO(b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)), "big.pdf")})
        self.assertEqual(r.status_code, 413); self.assertEqual(r.json["error"]["code"], "too_large")

    def test_filename_sanitised(self):
        for evil in ("../../etc/passwd.pdf", "..\\..\\win.pdf", "<script>alert(1)</script>.pdf", "a\x00b\nc.pdf", ""):
            n = safe_name(evil); self.assertNotIn("/", n); self.assertNotIn("\\", n); self.assertNotIn("<", n); self.assertNotIn("\n", n); self.assertTrue(n.endswith(".pdf"))
        d = self.upload_ok(good(), "<img src=x onerror=alert(1)>.pdf")
        self.assertNotIn(b"<img", self.c.get(f"/api/documents/{d}/report?format=html").data)
        self.assertEqual(os.listdir(self.cfg.data_dir / "docs")[0], d)                   # storage path is server-generated

    def test_path_traversal_and_bad_ids(self):
        d = self.upload_ok(good())
        for bad in ("../../etc", "..%2F..%2Fetc", "v0/../../x", "VV", "v999", "x" * 40):
            self.assertIn(self.c.get(f"/api/documents/{d}/download?version={bad}").status_code, (404,), bad)
        for bad in ("..", "abc", "0" * 31, "0" * 32, "../" + d):
            self.assertEqual(self.c.get(f"/api/documents/{bad}/analysis").status_code, 404, bad)
        self.assertEqual(self.c.get("/static/../../etc/passwd").status_code, 404)
        self.assertEqual(self.c.get("/legacy/../../accessfix/config.py").status_code, 404)

    def test_headers_csp_and_cors(self):
        r = self.c.get("/"); self.assertIn("script-src 'self'", r.headers["Content-Security-Policy"]); self.assertNotIn("unsafe-inline", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        r = self.c.get("/api/capabilities", headers={"Origin": "http://evil.example"}); self.assertNotIn("Access-Control-Allow-Origin", r.headers)
        self.cfg.cors_origins = "http://localhost:3000"
        r = self.c.get("/api/capabilities", headers={"Origin": "http://localhost:3000"}); self.assertEqual(r.headers["Access-Control-Allow-Origin"], "http://localhost:3000")
        self.assertEqual(self.c.get("/api/capabilities").headers["Cache-Control"], "no-store")

    def test_delete_and_expiry(self):
        d = self.upload_ok(good()); self.assertTrue(self.c.delete(f"/api/documents/{d}").json["deleted"]); self.assertEqual(self.c.get(f"/api/documents/{d}").status_code, 404)
        d2 = self.upload_ok(good()); from accessfix.app import Store
        store = self.app.extensions["accessfix"]["store"]; m = store.meta(d2); m["touched"] = time.time() - 48 * 3600; store._write(store.dir(d2), m)
        self.assertEqual(store.cleanup(24), 1); self.assertEqual(self.c.get(f"/api/documents/{d2}").status_code, 404)

    def test_page_limit_via_api(self):
        self.cfg.max_pages = 1; d = self.upload_ok(untagged()); a = self.analysis(d)
        self.assertEqual(a["state"], "too_many_pages"); self.assertEqual(self.c.get(f"/api/documents/{d}/ocr-result").status_code, 404)

    def test_concurrent_uploads_are_isolated(self):
        import threading
        ids = []
        def go():
            ids.append(self.upload(untagged() if len(ids) % 2 else good()).json["document"]["id"])
        ts = [threading.Thread(target=go) for _ in range(6)]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(len(set(ids)), 6)
        for i in ids:
            time.sleep(0.1)
            for _ in range(100):
                if self.c.get(f"/api/documents/{i}/analysis").status_code == 200: break
                time.sleep(0.1)
            self.assertIn(self.analysis(i)["tag_status"]["status"], ("tagged", "untagged"))

    def test_unknown_job_and_malformed_json(self):
        self.assertEqual(self.c.get("/api/jobs/nope").status_code, 404)
        d = self.upload_ok(good()); self.assertEqual(self.c.post(f"/api/documents/{d}/remediate", data="{bad", content_type="application/json").status_code, 400)

if __name__ == "__main__":
    unittest.main()
