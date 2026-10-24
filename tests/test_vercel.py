"""Tests for Vercel serverless environment compatibility."""
from __future__ import annotations
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from accessfix.app import create_app
from accessfix.config import Config, is_serverless, _default_data_dir


class VercelCompatibilityTests(unittest.TestCase):
    def test_is_serverless_detection(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_serverless())
        with patch.dict(os.environ, {"VERCEL": "1"}):
            self.assertTrue(is_serverless())
        with patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "func"}):
            self.assertTrue(is_serverless())
        with patch.dict(os.environ, {"LAMBDA_TASK_ROOT": "/var/task"}):
            self.assertTrue(is_serverless())

    def test_default_data_dir_uses_temp_on_serverless(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            data_dir = _default_data_dir()
            self.assertTrue(str(data_dir).startswith(tempfile.gettempdir()))

    def test_app_initialization_on_serverless(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            cfg = Config()
            self.assertTrue(str(cfg.data_dir).startswith(tempfile.gettempdir()))
            app = create_app(cfg)
            client = app.test_client()

            # Homepage loads
            resp = client.get("/")
            self.assertEqual(resp.status_code, 200)

            # Capabilities loads
            resp = client.get("/api/capabilities")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIn("version", data)
            self.assertIn("ocr", data)

    def test_serverless_upload_and_analysis_sync(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            cfg = Config()
            app = create_app(cfg)
            client = app.test_client()

            # Upload valid minimal PDF
            pdf_bytes = (
                b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
                b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
                b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
                b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
                b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n184\n%%EOF\n"
            )
            data = {"file": (io.BytesIO(pdf_bytes), "sample.pdf")}
            resp = client.post("/api/documents", data=data, content_type="multipart/form-data")
            self.assertEqual(resp.status_code, 201)
            res_json = resp.get_json()
            doc_id = res_json["document"]["id"]
            # Under serverless, job should have completed synchronously
            self.assertIn(res_json["job"]["status"], ("completed", "completed_with_errors"))

            # Check analysis endpoint directly
            resp2 = client.get(f"/api/documents/{doc_id}/analysis")
            self.assertEqual(resp2.status_code, 200)

    def test_vercel_rewritten_paths(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            cfg = Config()
            app = create_app(cfg)
            client = app.test_client()

            # When Vercel rewrites to /api/index.py or /api/index, middleware restores "/"
            resp1 = client.get("/api/index.py")
            self.assertEqual(resp1.status_code, 200)

            resp2 = client.get("/api/index")
            self.assertEqual(resp2.status_code, 200)

            # When X-Matched-Path header is sent by Vercel
            resp3 = client.get("/api/index", headers={"X-Matched-Path": "/api/capabilities"})
            self.assertEqual(resp3.status_code, 200)
            self.assertIn("version", resp3.get_json())

            # When __path query parameter is passed by vercel.json rewrite
            resp4 = client.get("/api/index?__path=/api/capabilities")
            self.assertEqual(resp4.status_code, 200)
            self.assertIn("version", resp4.get_json())

            # Favicon returns 204
            resp_fav = client.get("/favicon.ico")
            self.assertEqual(resp_fav.status_code, 204)

    def test_entrypoint_imports(self):
        import run
        self.assertIsNotNone(run.app)
        import api.index
        self.assertIsNotNone(api.index.app)


if __name__ == "__main__":
    unittest.main()
