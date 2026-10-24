"""JSON and HTML report generation from real analysis results."""
from __future__ import annotations
import html
import time
from . import __version__

SEV_LABEL = {"error": "Error", "warning": "Warning", "manual": "Manual review", "not_run": "Not performed"}
DISCLAIMER = ("This report comes from AccessFix PDF's internal checks. It is not a PDF/UA or WCAG conformance certification. "
              "Validate with an independent tool (PAC, veraPDF) and test with a screen reader before declaring a document accessible. "
              "OCR makes text searchable; it does not tag a document.")


CLASS_LABEL = {
    "structural_error": "PDF Structural Error",
    "accessibility_failure": "Accessibility Failure",
    "semantic_issue": "Potential Semantic Issue",
    "manual_review": "Manual Check Required",
    "not_run": "Not Performed",
}


def build(meta, version, analysis, history=None, ocr=None, remediation=None, comparison=None):
    a = dict(analysis)
    st = a.get("structure")
    if st:
        st = {k: v for k, v in st.items() if k != "nodes"}
    scan = a.get("scan") or {}
    rep = {"report_version": 2, "generated": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "tool": a.get("tool"), "disclaimer": DISCLAIMER,
           "file": {**a.get("file", {}), "name": meta["name"], "version": version["id"], "version_label": version["label"], "sha256": version["sha256"]},
           "state": a.get("state"), "message": a.get("message"), "tag_status": a.get("tag_status"), "counts": a.get("counts"),
           "classification_counts": a.get("classification_counts"),
           "scan": {"summary": scan, "pages": [{k: p.get(k) for k in ("page", "class", "label", "confidence", "ocr_candidate", "visible_chars", "invisible_chars",
                                                                         "image_coverage", "largest_image_coverage", "reasons")} for p in a.get("pages", [])]},
           "checks_performed": a.get("checked"), "findings": a.get("findings", []), "structure": st,
           "version_history": history or [], "ocr": ocr, "remediation": remediation, "comparison": comparison}
    return rep


def to_html(rep):
    e = lambda x: html.escape(str(x if x is not None else ""))
    f = rep["file"]
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Accessibility report - {e(f.get('name'))}</title><style>"
           "body{font:16px/1.5 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;color:#14201f}h1,h2{line-height:1.2}"
           "table{border-collapse:collapse;width:100%}th,td{border:1px solid #9aa7a5;padding:.4rem .6rem;text-align:left;vertical-align:top}"
           "th{background:#eef2f1}.error{color:#a31509;font-weight:700}.warning{color:#7a4b00;font-weight:700}.manual{color:#2b3e9e;font-weight:700}.not_run{color:#4a5654;font-weight:700}"
           ".badge{display:inline-block;padding:.15rem .45rem;border-radius:4px;font-size:.85rem;font-weight:600;margin-right:.5rem}"
           ".b-struct{background:#fee2e2;color:#991b1b}.b-access{background:#fed7aa;color:#9a3412}.b-semantic{background:#fef3c7;color:#92400e}.b-man{background:#e0e7ff;color:#3730a3}"
           "code{background:#eef2f1;padding:0 .2rem}.note{border-left:4px solid #0b5d57;padding:.5rem 1rem;background:#f1f6f5}</style></head><body>"]
    out.append(f"<h1>Accessibility report</h1><p class='note'>{e(rep['disclaimer'])}</p>")
    out.append("<h2>Document</h2><table><tbody>")
    for k, label in (("name", "File"), ("version_label", "Version checked"), ("pages", "Pages"), ("size", "Size (bytes)"), ("pdf_version", "PDF version"), ("title", "Title"),
                     ("language", "Language"), ("encrypted", "Encrypted"), ("sha256", "SHA-256")):
        out.append(f"<tr><th scope='row'>{label}</th><td>{e(f.get(k))}</td></tr>")
    out.append(f"<tr><th scope='row'>Generated</th><td>{e(rep['generated'])} by {e((rep.get('tool') or {}).get('name'))} {e((rep.get('tool') or {}).get('version'))}</td></tr></tbody></table>")
    if rep.get("state") not in (None, "ok"):
        out.append(f"<h2>Result</h2><p class='error'>{e(rep.get('message'))}</p></body></html>")
        return "".join(out)
    ts = rep["tag_status"] or {}
    out.append(f"<h2>Tagging status</h2><p><strong>{e(ts.get('status', '')).replace('_', ' ')}</strong></p><ul>" + "".join(f"<li>{e(r)}</li>" for r in ts.get("reasons", [])) + "</ul>")
    c = rep["counts"] or {}
    cc = rep.get("classification_counts") or {}
    out.append(f"<h2>Summary</h2><p>{c.get('error', 0)} errors, {c.get('warning', 0)} warnings, {c.get('manual', 0)} manual-review items, {c.get('not_run', 0)} checks not performed.</p>")
    if cc:
        out.append(f"<p><span class='badge b-struct'>Structural errors: {cc.get('structural_error', 0)}</span> "
                   f"<span class='badge b-access'>Accessibility failures: {cc.get('accessibility_failure', 0)}</span> "
                   f"<span class='badge b-semantic'>Semantic issues: {cc.get('semantic_issue', 0)}</span> "
                   f"<span class='badge b-man'>Manual review: {cc.get('manual_review', 0)}</span></p>")
    sc = rep["scan"]["summary"] or {}
    out.append("<h2>Scanned pages and OCR</h2>")
    out.append(f"<p>Document class (heuristic): <strong>{e(sc.get('doc_class', '')).replace('_', ' ')}</strong>. OCR candidate pages: {e(sc.get('candidate_pages'))}. Existing OCR layer on pages: {e(sc.get('ocr_layer_pages'))}.</p>")
    out.append("<table><thead><tr><th>Page</th><th>Class</th><th>Confidence of classification</th><th>Evidence</th></tr></thead><tbody>" +
               "".join(f"<tr><td>{p['page']}</td><td>{e(p['label'])}</td><td>{e(p['confidence'])}</td><td>{e(' '.join(p.get('reasons') or []))}</td></tr>" for p in rep["scan"]["pages"][:500]) + "</tbody></table>")
    o = rep.get("ocr")
    if o:
        out.append(f"<h3>OCR run</h3><p>{e(o.get('engine'))}, languages {e(o.get('languages'))}, {e(o.get('dpi'))} dpi. Pages OK: {e(o.get('pages_ok'))} of {e(o.get('pages_requested'))}; failed: {e(o.get('pages_failed'))}.</p>")
        out.append("<table><thead><tr><th>Page</th><th>Status</th><th>Words</th><th>Mean confidence</th><th>Flags</th></tr></thead><tbody>" +
                   "".join(f"<tr><td>{p['page']}</td><td>{e(p['status'])}</td><td>{e(p.get('words'))}</td><td>{e(p.get('mean_confidence'))}</td><td>{e('; '.join(p.get('flags') or [p.get('error', '')]))}</td></tr>" for p in o.get("per_page", [])) + "</tbody></table>")
        if o.get("checks"):
            out.append("<h3>Post-OCR verification</h3><table><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>" +
                       "".join(f"<tr><td>{e(k['name'])}</td><td>{e(k['level'])}</td><td>{e(k['detail'])}</td></tr>" for k in o["checks"]) + "</tbody></table>")
    out.append("<h2>Findings</h2>")
    for sev in ("error", "warning", "manual", "not_run"):
        items = [x for x in rep["findings"] if x["severity"] == sev]
        if not items:
            continue
        out.append(f"<h3>{SEV_LABEL[sev]} ({len(items)})</h3>")
        for x in items:
            cl = x.get("classification", "")
            cl_badge = f"<span class='badge {'b-struct' if cl == 'structural_error' else 'b-access' if cl == 'accessibility_failure' else 'b-semantic' if cl == 'semantic_issue' else 'b-man'}'>{CLASS_LABEL.get(cl, cl)}</span>" if cl else ""
            out.append(f"<h4><span class='{sev}'>{SEV_LABEL[sev]}</span>: {e(x['title'])}</h4>")
            if cl_badge:
                out.append(f"<p>{cl_badge} <small>Verification: {e(x.get('verification', ''))}</small></p>")
            if x.get("pages"):
                out.append(f"<p>Pages: {e(', '.join(map(str, x['pages'][:40])))}</p>")
            if x.get("obj_refs"):
                out.append(f"<p>PDF Objects: {e(', '.join(map(str, x['obj_refs'][:15])))}</p>")
            if x.get("detail"):
                out.append(f"<p>{e(x['detail'])}</p>")
            if x.get("impact"):
                out.append(f"<p><strong>Assistive Technology Impact:</strong> {e(x['impact'])}</p>")
            if x.get("evidence"):
                out.append("<p>Evidence:</p><ul>" + "".join(f"<li>{e(v)}</li>" for v in x["evidence"][:15]) + "</ul>")
            if x.get("recommendation"):
                out.append(f"<p><strong>Recommended action:</strong> {e(x['recommendation'])}</p>")
            if x.get("refs"):
                out.append("<p>References: " + "; ".join(e(f"{r['std']} {r['id']}") for r in x["refs"]) + f". Source: {e(x.get('source'))}.</p>")
    out.append("<h2>Checks performed</h2><ul>" + "".join(f"<li>{e(c)}</li>" for c in rep.get("checks_performed") or []) + "</ul>")
    cmp = rep.get("comparison")
    if cmp:
        out.append(f"<h2>Before / after</h2><p>Compared {e(cmp['from'])} with {e(cmp['to'])}.</p><ul><li>Resolved: {e(cmp['resolved'])}</li><li>New: {e(cmp['new'])}</li><li>Unchanged: {e(cmp['unchanged'])}</li></ul>")
    out.append("<h2>Version history</h2><ul>" + "".join(f"<li>{e(v['id'])}: {e(v['label'])} ({e(v['kind'])}, {e(v['size'])} bytes)</li>" for v in rep["version_history"]) + "</ul>")
    out.append("</body></html>")
    return "".join(out)
