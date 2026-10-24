"""HTTP API and static hosting."""
from __future__ import annotations
import io
import logging
import re
import shutil
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException

from . import __version__, ocr as ocrmod
from .analyze import analyze
from .config import Config, ROOT, is_serverless
from .jobs import JobManager
from .pdfium_content import LOCK, extract_page_text_layout
from .remediate import RemediationError, apply_actions
from .report import build as build_report, to_html
from .store import NotFound, Store

log = logging.getLogger("accessfix")
STATIC = ROOT / "static"
if not STATIC.is_dir() and (Path.cwd() / "static").is_dir():
    STATIC = Path.cwd() / "static"
CSP_APP = "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
CSP_LEGACY = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; "
              "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' blob: https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; worker-src 'self' blob:; connect-src 'self' https://cdn.jsdelivr.net data: blob:; frame-ancestors 'none'")


def api_error(status, code, message, **extra):
    r = jsonify({"error": {"code": code, "message": message, **extra}})
    r.status_code = status
    return r


def setup_logging(cfg):
    root = logging.getLogger("accessfix")
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    h1 = logging.StreamHandler()
    h1.setFormatter(fmt)
    root.addHandler(h1)

    # In serverless runtimes (Vercel, AWS Lambda), stdout is captured by platform logs.
    # Rotating file handler is only attached when running on a writable persistent filesystem.
    if not is_serverless():
        try:
            (cfg.data_dir / "logs").mkdir(parents=True, exist_ok=True)
            h2 = RotatingFileHandler(cfg.data_dir / "logs" / "accessfix.log", maxBytes=2_000_000, backupCount=3)
            h2.setFormatter(fmt)
            root.addHandler(h2)
        except (OSError, PermissionError):
            pass


def create_app(cfg: Config | None = None) -> Flask:
    cfg = cfg or Config()
    setup_logging(cfg)
    app = Flask(__name__, static_folder=None)
    app.url_map.strict_slashes = False
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_upload_bytes + 4096

    class VercelPathFixMiddleware:
        """Corrects PATH_INFO when Vercel rewrites requests to serverless entrypoints."""
        def __init__(self, wsgi_app):
            self.wsgi_app = wsgi_app

        def __call__(self, environ, start_response):
            orig_path = (
                environ.get("HTTP_X_MATCHED_PATH")
                or environ.get("HTTP_X_VERCEL_MATCHED_PATH")
                or environ.get("HTTP_X_FORWARDED_URI")
                or environ.get("RAW_URI")
                or environ.get("REQUEST_URI")
            )
            if orig_path:
                clean_path = orig_path.split("?")[0]
                if clean_path in ("/api/index", "/api/index.py"):
                    environ["PATH_INFO"] = "/"
                elif clean_path:
                    environ["PATH_INFO"] = clean_path
            else:
                path = environ.get("PATH_INFO", "")
                if path in ("/api/index.py", "/api/index"):
                    environ["PATH_INFO"] = "/"
            return self.wsgi_app(environ, start_response)

    app.wsgi_app = VercelPathFixMiddleware(app.wsgi_app)
    store = Store(cfg.data_dir)
    jobs = JobManager(cfg.max_concurrent_jobs)
    app.extensions["accessfix"] = {"cfg": cfg, "store": store, "jobs": jobs}

    def _cleaner():
        while True:
            time.sleep(1800)
            try:
                n = store.cleanup(cfg.ttl_hours)
                if n:
                    log.info("Removed %d expired document(s)", n)
            except Exception:
                log.exception("cleanup failed")

    if not app.config.get("TESTING") and not is_serverless():
        threading.Thread(target=_cleaner, daemon=True, name="cleanup").start()

    # ------------------------------------------------------------------ headers / errors
    @app.after_request
    def headers(resp):
        p = request.path
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if p.startswith("/legacy"):
            resp.headers["Content-Security-Policy"] = CSP_LEGACY
        else:
            resp.headers["Content-Security-Policy"] = CSP_APP
        if p.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
            origin = request.headers.get("Origin")
            allowed = [o.strip() for o in cfg.cors_origins.split(",") if o.strip()]
            if origin and origin in allowed:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Vary"] = "Origin"
                resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
                resp.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
        return resp

    @app.errorhandler(HTTPException)
    def http_err(e):
        if request.path.startswith("/api/"):
            code = {413: "too_large", 404: "not_found", 405: "method_not_allowed"}.get(e.code, "http_error")
            msg = (f"The upload is larger than the {cfg.max_upload_mb} MB limit." if e.code == 413 else e.description)
            return api_error(e.code, code, msg)
        return e

    @app.errorhandler(NotFound)
    def nf(e):
        return api_error(404, "not_found", str(e))

    @app.errorhandler(Exception)
    def boom(e):
        log.exception("Unhandled error on %s", request.path)
        if request.path.startswith("/api/"):
            return api_error(500, "internal_error", f"Unexpected server error ({type(e).__name__}). See the server log.")
        raise e

    # ------------------------------------------------------------------ helpers
    def ver(doc_id):
        v = request.args.get("version") or (request.get_json(silent=True) or {}).get("version")
        return store.version(doc_id, v)

    def run_analysis(doc_id, vid, job=None):
        prog = (lambda d, t, m: job.progress(d, t, m)) if job else None
        res = analyze(store.path(doc_id, vid), cfg, prog, store.meta(doc_id)["name"])
        store.save_json(doc_id, f"analysis-{vid}", res)
        store.update_version(doc_id, vid, state=res["state"], tag_status=(res.get("tag_status") or {}).get("status"), counts=res.get("counts"))
        return res

    def analysis_of(doc_id, vid):
        a = store.load_json(doc_id, f"analysis-{vid}")
        if a is None:
            raise NotFound("Analysis for this version is not available yet")
        return a

    def slim(a):
        a = dict(a)
        if a.get("structure"):
            a["structure"] = {k: v for k, v in a["structure"].items() if k != "nodes"}
        return a

    def doc_json(doc_id):
        m = store.meta(doc_id)
        return {k: m[k] for k in ("id", "name", "created", "versions")}

    # ------------------------------------------------------------------ static
    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:p>")
    def static_files(p):
        return send_from_directory(STATIC, p)

    @app.get("/legacy/")
    def legacy_index():
        return send_from_directory(STATIC / "legacy", "index.html")

    @app.get("/legacy/<path:p>")
    def legacy_files(p):
        return send_from_directory(STATIC / "legacy", p)

    # ------------------------------------------------------------------ capabilities
    @app.get("/api/capabilities")
    def capabilities():
        eng = ocrmod.engine_info(cfg)
        return jsonify({"version": __version__, "ocr": eng, "poppler_pdftotext": bool(shutil.which("pdftotext")),
                        "limits": {"max_upload_mb": cfg.max_upload_mb, "max_pages": cfg.max_pages, "ocr_page_timeout_s": cfg.ocr_page_timeout_s,
                                   "ocr_workers": cfg.ocr_workers, "ttl_hours": cfg.ttl_hours},
                        "libraries": {"pikepdf": pikepdf.__version__, "pypdfium2": getattr(pdfium, "PYPDFIUM_INFO", "?") and str(pdfium.PYPDFIUM_INFO)},
                        "independent_validators": {"pac": False, "verapdf": bool(shutil.which("verapdf"))}})

    # ------------------------------------------------------------------ documents
    @app.post("/api/documents")
    def upload():
        f = request.files.get("file")
        if f is None:
            return api_error(400, "no_file", "Choose a PDF file to upload (form field 'file').")
        data = f.read(cfg.max_upload_bytes + 1)
        if not data:
            return api_error(400, "empty_file", "The uploaded file is empty.")
        if len(data) > cfg.max_upload_bytes:
            return api_error(413, "too_large", f"The file is larger than the {cfg.max_upload_mb} MB limit.")
        if b"%PDF-" not in data[:1024]:
            return api_error(415, "not_pdf", "This file does not look like a PDF (no %PDF- signature). The check uses the file content, not its name.")
        meta = store.create(f.filename or "document.pdf", data)
        doc_id = meta["id"]
        log.info("Uploaded %s (%d bytes) as %s", meta["name"], len(data), doc_id)

        def work(job):
            job.log("info", "Analysing document structure and pages")
            res = run_analysis(doc_id, "v0", job)
            if res["state"] != "ok":
                job.log("warn", res.get("message", ""))
            return {"version": "v0", "state": res["state"]}

        job = jobs.submit("analyze", doc_id, work, sync=is_serverless())
        r = jsonify({"document": doc_json(doc_id), "job": job.to_dict()})
        r.status_code = 201
        return r

    @app.get("/api/documents/<doc_id>")
    def get_doc(doc_id):
        store.touch(doc_id)
        return jsonify(doc_json(doc_id))

    @app.delete("/api/documents/<doc_id>")
    def del_doc(doc_id):
        store.delete(doc_id)
        return jsonify({"deleted": True})

    @app.get("/api/documents/<doc_id>/analysis")
    def get_analysis(doc_id):
        v = ver(doc_id)
        return jsonify({"version": v, "analysis": slim(analysis_of(doc_id, v["id"]))})

    @app.get("/api/documents/<doc_id>/structure")
    def get_structure(doc_id):
        v = ver(doc_id)
        a = analysis_of(doc_id, v["id"])
        return jsonify({"version": v["id"], "structure": a.get("structure"), "tag_status": a.get("tag_status")})

    @app.get("/api/documents/<doc_id>/pages/<int:pno>/image")
    def page_image(doc_id, pno):
        v = ver(doc_id)
        path = store.path(doc_id, v["id"])
        try:
            width = max(80, min(1400, int(request.args.get("w", 240))))
        except ValueError:
            width = 240
        cache = store.cache_dir(doc_id) / f"{v['id']}-{pno}-{width}.png"
        if not cache.is_file():
            with LOCK:
                doc = pdfium.PdfDocument(str(path))
                try:
                    if pno < 1 or pno > len(doc):
                        abort(404, "Page does not exist")
                    page = doc[pno - 1]
                    wpt = page.get_size()[0]
                    img = page.render(scale=width / wpt).to_pil().convert("RGB")
                    page.close()
                except HTTPException:
                    raise
                except Exception as e:
                    return api_error(422, "render_failed", f"Page {pno} could not be rendered: {e}")
                finally:
                    doc.close()
            buf = io.BytesIO()
            img.save(buf, "PNG", optimize=False)
            cache.write_bytes(buf.getvalue())
        return send_file(cache, mimetype="image/png", max_age=0)

    @app.get("/api/documents/<doc_id>/pages/<int:pno>/text")
    def page_text(doc_id, pno):
        v = ver(doc_id)
        path = store.path(doc_id, v["id"])
        ocr_words = None
        ocr_data = store.load_json(doc_id, f"ocr-{v['id']}")
        if ocr_data and "per_page" in ocr_data:
            for pinfo in ocr_data["per_page"]:
                if pinfo.get("page") == pno and "words_data" in pinfo:
                    ocr_words = pinfo.get("words_data")
                    break
        with LOCK:
            doc = pdfium.PdfDocument(str(path))
            try:
                if pno < 1 or pno > len(doc):
                    abort(404, "Page does not exist")
                page = doc[pno - 1]
                layout = extract_page_text_layout(page, ocr_words)
                page.close()
            finally:
                doc.close()
        return jsonify({
            "page": pno,
            "version": v["id"],
            "text": layout["text"][:100000],
            "truncated": len(layout["text"]) > 100000,
            "words": layout["words"],
            "lines": layout["lines"],
            "display_width": layout["display_width"],
            "display_height": layout["display_height"],
            "rotation": layout["rotation"],
        })

    @app.get("/api/documents/<doc_id>/download")
    def download(doc_id):
        v = ver(doc_id)
        m = store.meta(doc_id)
        stem = m["name"][:-4]
        suffix = {"original": "", "ocr": "-searchable", "fixed": "-fixed"}.get(v["kind"], "")
        return send_file(store.path(doc_id, v["id"]), mimetype="application/pdf", as_attachment=True, download_name=f"{stem}{suffix}.pdf", max_age=0)

    # ------------------------------------------------------------------ jobs
    @app.get("/api/jobs/<job_id>")
    def get_job(job_id):
        j = jobs.get(job_id)
        if not j:
            return api_error(404, "not_found", "Unknown job")
        return jsonify(j.to_dict())

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel_job(job_id):
        j = jobs.get(job_id)
        if not j:
            return api_error(404, "not_found", "Unknown job")
        if j.kind != "ocr":
            return api_error(409, "not_cancelable", "Only OCR jobs can be canceled.")
        j.request_cancel()
        j.log("warn", "Cancel requested")
        return jsonify(j.to_dict())

    # ------------------------------------------------------------------ OCR
    @app.post("/api/documents/<doc_id>/ocr")
    def start_ocr(doc_id):
        body = request.get_json(silent=True) or {}
        v = ver(doc_id)
        a = analysis_of(doc_id, v["id"])
        if a["state"] != "ok":
            return api_error(422, "unsupported_document", a.get("message") or "This document cannot be processed.")
        if a["file"].get("encrypted"):
            return api_error(422, "encrypted", "Encrypted PDFs cannot be modified. Remove the encryption in the source application first.")
        n_pages = a["file"]["pages"]
        sel = body.get("pages", "candidates")
        if sel == "all":
            pages = list(range(1, n_pages + 1))
        elif sel == "candidates":
            pages = list((a.get("scan") or {}).get("candidate_pages", []))
        elif isinstance(sel, list) and all(isinstance(x, int) for x in sel):
            pages = sorted(set(sel))
        else:
            return api_error(400, "bad_pages", "pages must be 'all', 'candidates' or a list of page numbers")
        if not pages:
            return api_error(400, "no_pages", "No pages to OCR. The detector found no scanned pages; select pages manually if you want to run OCR anyway.")
        if any(p < 1 or p > n_pages for p in pages):
            return api_error(400, "bad_pages", f"Page numbers must be between 1 and {n_pages}.")
        langs = str(body.get("languages") or "eng")
        dpi = body.get("dpi", 300)
        psm = body.get("psm", 3)
        if dpi not in (150, 200, 300, 400):
            return api_error(400, "bad_dpi", "dpi must be 150, 200, 300 or 400")
        if psm not in (1, 3, 4, 6, 11, 12):
            return api_error(400, "bad_psm", "Unsupported page segmentation mode")
        eng = ocrmod.engine_info(cfg)
        if not eng["available"]:
            return api_error(503, "engine_unavailable", eng["error"], install_help=eng["install_help"])
        if not ocrmod.LANG_RE.match(langs) or [l for l in langs.split("+") if l not in eng["languages"]]:
            return api_error(400, "missing_language", f"Language data not installed for '{langs}'. Installed: {', '.join(eng['languages'])}", installed=eng["languages"])
        skip = bool(body.get("skip_overlap", True))
        src, parent = store.path(doc_id, v["id"]), v["id"]

        def work(job):
            tmp = store.dir(doc_id) / f"tmp-{job.id}.pdf"
            try:
                summary, results = ocrmod.run_ocr(src, tmp, [p - 1 for p in pages], langs, dpi, psm, cfg, job, skip)
                job.log("info", "Verifying the output file with independent text extraction")
                checks = ocrmod.verify_output(src, tmp, results, summary["per_page"])
                summary["checks"] = checks
                label = f"OCR: {summary['pages_ok']} page(s), {langs}"
                newv = store.commit_file(doc_id, tmp, label, "ocr", parent, {"has_ocr": True})
            finally:
                if tmp.exists():
                    tmp.unlink()
            store.save_json(doc_id, f"ocr-{newv['id']}", summary)
            job.log("info", "Re-checking the new file for accessibility")
            res = run_analysis(doc_id, newv["id"], job)
            failed = [c for c in checks if c["level"] == "fail"]
            for c in failed:
                job.log("error", f"Verification failed: {c['name']} - {c['detail']}")
            return {"version": newv["id"], "has_errors": bool(summary["pages_failed"] or failed), "summary": {k: v for k, v in summary.items() if k != "per_page"},
                    "per_page": summary["per_page"], "checks": checks, "tag_status": res["tag_status"], "counts": res["counts"],
                    "scan": res.get("scan"), "accessibility_note": "The file now has searchable text. Tagging status: "
                    f"{res['tag_status']['status'].replace('_', ' ')}. OCR does not tag a document, so this result does not establish accessibility."}

        job = jobs.submit("ocr", doc_id, work)
        log.info("OCR job %s: doc %s pages %s langs %s dpi %s", job.id[:8], doc_id[:8], pages[:10], langs, dpi)
        r = jsonify({"job": job.to_dict()})
        r.status_code = 202
        return r

    @app.get("/api/documents/<doc_id>/ocr-result")
    def ocr_result(doc_id):
        v = ver(doc_id)
        s = store.load_json(doc_id, f"ocr-{v['id']}")
        if s is None:
            raise NotFound("This version was not produced by OCR")
        return jsonify(s)

    # ------------------------------------------------------------------ remediation
    @app.post("/api/documents/<doc_id>/remediate")
    def remediate(doc_id):
        body = request.get_json(silent=True) or {}
        actions = body.get("actions")
        if not isinstance(actions, list) or not actions or len(actions) > 50:
            return api_error(400, "bad_actions", "Send 1-50 actions.")
        v = ver(doc_id)
        before = analysis_of(doc_id, v["id"])
        if before["state"] != "ok":
            return api_error(422, "unsupported_document", before.get("message") or "Cannot modify this document.")
        tmp = store.dir(doc_id) / f"tmp-{int(time.time() * 1000)}.pdf"
        try:
            applied, skipped = apply_actions(store.path(doc_id, v["id"]), tmp, actions, bool(body.get("confirm")))
            names = ", ".join(sorted({x["action"].replace("set_", "").replace("_", " ") for x in applied}))
            newv = store.commit_file(doc_id, tmp, f"Fixed: {names}", "fixed", v["id"], {"remediation": {"applied": applied, "skipped": skipped}})
        except RemediationError as e:
            return api_error(422, "remediation_failed", str(e))
        finally:
            if tmp.exists():
                tmp.unlink()
        after = run_analysis(doc_id, newv["id"])
        cmp = compare(before, after, v["id"], newv["id"])
        store.save_json(doc_id, f"compare-{newv['id']}", cmp)
        return jsonify({"version": newv, "applied": applied, "skipped": skipped, "comparison": cmp, "analysis": slim(after)}), 201

    def _sig(a):
        out = {}
        for f in a.get("findings", []):
            if f["severity"] in ("error", "warning"):
                out[f["code"]] = (f["severity"], f["count"], f["title"])
        return out

    def compare(a, b, vfrom, vto):
        sa, sb = _sig(a), _sig(b)
        return {"from": vfrom, "to": vto,
                "resolved": [{"code": c, "title": sa[c][2]} for c in sa if c not in sb],
                "new": [{"code": c, "title": sb[c][2]} for c in sb if c not in sa],
                "unchanged": [{"code": c, "title": sb[c][2], "before": sa[c][1], "after": sb[c][1]} for c in sb if c in sa],
                "counts_before": a.get("counts"), "counts_after": b.get("counts"),
                "tag_status_before": (a.get("tag_status") or {}).get("status"), "tag_status_after": (b.get("tag_status") or {}).get("status")}

    @app.get("/api/documents/<doc_id>/compare")
    def compare_route(doc_id):
        f, t = request.args.get("from", "v0"), request.args.get("to", "latest")
        vf, vt = store.version(doc_id, f), store.version(doc_id, t)
        return jsonify(compare(analysis_of(doc_id, vf["id"]), analysis_of(doc_id, vt["id"]), vf["id"], vt["id"]))

    # ------------------------------------------------------------------ report
    @app.get("/api/documents/<doc_id>/report")
    def report(doc_id):
        v = ver(doc_id)
        m = store.meta(doc_id)
        a = analysis_of(doc_id, v["id"])
        ocr_s = store.load_json(doc_id, f"ocr-{v['id']}")
        cmp = None
        if v.get("parent"):
            try:
                cmp = compare(analysis_of(doc_id, v["parent"]), a, v["parent"], v["id"])
            except NotFound:
                pass
        rep = build_report(m, v, a, m["versions"], ocr_s, v.get("remediation"), cmp)
        fmt = request.args.get("format", "json")
        stem = m["name"][:-4]
        if fmt == "html":
            return Response(to_html(rep), mimetype="text/html", headers={"Content-Disposition": f'attachment; filename="{re.sub(r"[^A-Za-z0-9._-]", "_", stem)}-accessibility-report.html"'})
        if fmt == "json":
            r = jsonify(rep)
            r.headers["Content-Disposition"] = f'attachment; filename="{re.sub(r"[^A-Za-z0-9._-]", "_", stem)}-accessibility-report.json"'
            return r
        return api_error(400, "bad_format", "format must be json or html")

    return app
