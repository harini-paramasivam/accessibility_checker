# Test results (executed)

Environment: Python 3.12.3, Flask 3.1.3, pikepdf 10.5.1, pypdfium2 5.6.0, Tesseract 5.3.4 (eng only), poppler 24.02, qpdf 11.9.0, Chromium via Playwright, 1 CPU.
Command: `python -m unittest discover -s tests -v`

```
ok
ok
ok
ok
test_headers_csp_and_cors (test_api_security.Upload.test_headers_csp_and_cors) ... /home/claude/AccessFix-PDF/tests/test_api_security.py:68: ResourceWarning: unclosed file <_io.BufferedReader name='/home/claude/AccessFix-PDF/static/index.html'>
  r = self.c.get("/api/capabilities", headers={"Origin": "http://evil.example"}); self.assertNotIn("Access-Control-Allow-Origin", r.headers)
ResourceWarning: Enable tracemalloc to get the object allocation traceback
ok
ok
ok
ok
test_rejects_by_content_not_name (test_api_security.Upload.test_rejects_by_content_not_name) ... ok
test_too_large (test_api_security.Upload.test_too_large) ... ok
test_unknown_job_and_malformed_json (test_api_security.Upload.test_unknown_job_and_malformed_json) ... /usr/local/lib/python3.12/dist-packages/werkzeug/routing/rules.py:615: ResourceWarning: unclosed file <_io.FileIO name=4 mode='rb+' closefd=True>
  match = _part_re.match(rule, pos)
ResourceWarning: Enable tracemalloc to get the object allocation traceback
ok
/usr/lib/python3.12/unittest/case.py:589: ResourceWarning: unclosed file <_io.BufferedReader name='/tmp/afx-data-l7u0addb/docs/e88549e3837c4a1e85b64b7531df6e96/v0.pdf'>
  if method() is not None:
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib/python3.12/unittest/case.py:589: ResourceWarning: unclosed file <_io.BufferedReader name='/tmp/afx-data-l7u0addb/docs/e88549e3837c4a1e85b64b7531df6e96/cache/v0-2-300.png'>
  if method() is not None:
ResourceWarning: Enable tracemalloc to get the object allocation traceback
ok
test_pairs_meet_aa (test_contrast.Contrast.test_pairs_meet_aa) ... ok
ok
ok
ok
/home/claude/AccessFix-PDF/tests/test_remediate.py:21: ResourceWarning: unclosed file <_io.BufferedReader name='/tmp/afx-data-pch1k7o5/docs/b1624a66b3474294b054fa57408f42e9/v0.pdf'>
  self.assertEqual(self.c.get(f"/api/documents/{doc}/download?version=v0").data, defect("no_meta").read_bytes())     # original preserved
ResourceWarning: Enable tracemalloc to get the object allocation traceback
ok
ok
ok
ok
ok
ok
ok
ok
/home/claude/AccessFix-PDF/tests/test_scan_ocr.py:95: ResourceWarning: unclosed file <_io.BufferedReader name='/tmp/afx-data-c2q3bxrl/docs/0112c35f2107416f86ba5611db40abd3/v1.pdf'>
  f = WORK / "tag_out.pdf"; f.write_bytes(self.c.get(f"/api/documents/{doc}/download?version={j['result']['version']}").data)
ResourceWarning: Enable tracemalloc to get the object allocation traceback
ok
ok
/home/claude/AccessFix-PDF/tests/test_scan_ocr.py:73: ResourceWarning: unclosed file <_io.BufferedReader name='/tmp/afx-data-92bac9iy/docs/165bdfe16e424c9b94a9180798e7fec7/v1.pdf'>
  f = WORK / "mixed_out.pdf"; f.write_bytes(self.c.get(f"/api/documents/{doc}/download?version={j['result']['version']}").data)
ResourceWarning: Enable tracemalloc to get the object allocation traceback
ok
/home/claude/AccessFix-PDF/tests/test_scan_ocr.py:84: ResourceWarning: unclosed file <_io.BufferedReader name='/tmp/afx-data-d2trtdrn/docs/1e24ab21546447c684c27548f84ce864/v1.pdf'>
  f = WORK / "dup_out.pdf"; f.write_bytes(self.c.get(f"/api/documents/{doc}/download?version={j['result']['version']}").data)
ResourceWarning: Enable tracemalloc to get the object allocation traceback
ok
/home/claude/AccessFix-PDF/tests/test_scan_ocr.py:107: ResourceWarning: unclosed file <_io.BufferedReader name='/tmp/afx-data-2jgz1o01/docs/38a0c983cf0d43b29ffcf07fa6d4e709/v1.pdf'>
  f = WORK / "rot_out.pdf"; f.write_bytes(self.c.get(f"/api/documents/{doc}/download?version={j['result']['version']}").data)
ResourceWarning: Enable tracemalloc to get the object allocation traceback
ok
/home/claude/AccessFix-PDF/tests/test_scan_ocr.py:52: ResourceWarning: unclosed file <_io.BufferedReader name='/tmp/afx-data-ly3b7pbi/docs/6410601cde3645598d98f8f6e37ad35b/v1.pdf'>
  out = self.c.get(f"/api/documents/{doc}/download?version={v}").data
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/home/claude/AccessFix-PDF/tests/test_scan_ocr.py:67: ResourceWarning: unclosed file <_io.BufferedReader name='/tmp/afx-data-ly3b7pbi/docs/6410601cde3645598d98f8f6e37ad35b/v0.pdf'>
  orig = self.c.get(f"/api/documents/{doc}/download?version=v0").data
ResourceWarning: Enable tracemalloc to get the object allocation traceback
ok
/home/claude/AccessFix-PDF/tests/test_scan_ocr.py:142: ResourceWarning: unclosed file <_io.BufferedReader name='/tmp/afx-data-wqoy856x/docs/72998ef7d2704082aec6a8ed04639c5f/v1.pdf'>
  f = WORK / "two.pdf"; f.write_bytes(self.c.get(f"/api/documents/{doc}/download?version={j['result']['version']}").data)
ResourceWarning: Enable tracemalloc to get the object allocation traceback
ok
ok
test_existing_ocr_layer_is_recognised_not_candidate (test_scan_ocr.ScanDetection.test_existing_ocr_layer_is_recognised_not_candidate) ... ok
test_image_only_page_is_definitive (test_scan_ocr.ScanDetection.test_image_only_page_is_definitive) ... ok
test_mixed_document_is_classified_per_page (test_scan_ocr.ScanDetection.test_mixed_document_is_classified_per_page) ... ok
test_native_document_has_no_candidates (test_scan_ocr.ScanDetection.test_native_document_has_no_candidates) ... ok
test_rotated_scan_detected (test_scan_ocr.ScanDetection.test_rotated_scan_detected) ... ok
test_tagged_doc_with_scanned_page (test_scan_ocr.ScanDetection.test_tagged_doc_with_scanned_page) ... ok
test_alt_text_quality_is_flagged_as_heuristic_warning (test_structure.StructuralDefects.test_alt_text_quality_is_flagged_as_heuristic_warning) ... ok
test_defect_does_not_cause_unrelated_errors (test_structure.StructuralDefects.test_defect_does_not_cause_unrelated_errors) ... ok
test_each_defect_is_detected_from_real_objects (test_structure.StructuralDefects.test_each_defect_is_detected_from_real_objects) ... ok
test_findings_carry_evidence_and_locations (test_structure.StructuralDefects.test_findings_carry_evidence_and_locations) ... ok
test_list_and_table_rules_use_parent_child_not_text (test_structure.StructuralDefects.test_list_and_table_rules_use_parent_child_not_text) ... ok
test_malformed_vs_partial_status (test_structure.StructuralDefects.test_malformed_vs_partial_status) ... ok
test_pdf20_style_unusual_tags_are_not_blindly_errors (test_structure.StructuralDefects.test_pdf20_style_unusual_tags_are_not_blindly_errors) ... ok
test_good_tagged_has_real_tree_and_no_machine_errors (test_structure.TagStatusAndTree.test_good_tagged_has_real_tree_and_no_machine_errors) ... ok
test_tagged_status_agrees_with_poppler (test_structure.TagStatusAndTree.test_tagged_status_agrees_with_poppler) ... ok
test_untagged_differs_from_tagged (test_structure.TagStatusAndTree.test_untagged_differs_from_tagged) ... ok
test_corrupt_and_garbage (test_structure.UnsupportedDocuments.test_corrupt_and_garbage) ... ok
test_encrypted_blocks_accessibility (test_structure.UnsupportedDocuments.test_encrypted_blocks_accessibility) ... ok
test_encrypted_password_required (test_structure.UnsupportedDocuments.test_encrypted_password_required) ... ok
test_large_multipage_mixed_content_is_fast_and_page_referenced (test_structure.UnsupportedDocuments.test_large_multipage_mixed_content_is_fast_and_page_referenced) ... ok
test_page_limit (test_structure.UnsupportedDocuments.test_page_limit) ... ok
test_zero_pages (test_structure.UnsupportedDocuments.test_zero_pages) ... ok
test_finding_navigates_to_element (test_ui.UiFlows.test_finding_navigates_to_element) ... ok
test_fix_in_browser_creates_new_version_and_revalidates (test_ui.UiFlows.test_fix_in_browser_creates_new_version_and_revalidates) ... ok
test_invalid_upload_error_state (test_ui.UiFlows.test_invalid_upload_error_state) ... ok
test_keyboard_operation (test_ui.UiFlows.test_keyboard_operation) ... ok
test_ocr_flow_and_download (test_ui.UiFlows.test_ocr_flow_and_download) ... ok
test_responsive_layout (test_ui.UiFlows.test_responsive_layout) ... ok
test_tagged_vs_untagged_tree_in_browser (test_ui.UiFlows.test_tagged_vs_untagged_tree_in_browser) ... ok

----------------------------------------------------------------------
Ran 61 tests in 24.160s

OK
```

Not covered: PAC/veraPDF comparison (not available), non-English OCR, the legacy auto-tagger's tagging output (CDN libraries unavailable offline), browsers other than Chromium.
