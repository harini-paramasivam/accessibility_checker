"""Local OCR with Tesseract. Adds an invisible, selectable text layer to chosen pages.

Nothing is rasterised and replaced: the original page content is kept byte for byte
(wrapped in q/Q) and a text layer in render mode 3 is appended. Existing tags, forms,
links, annotations, bookmarks and native text stay in the file.
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pikepdf
import pypdfium2 as pdfium
from pikepdf import Array, Dictionary, Name, Stream

from .pdfium_content import LOCK, page_display_info, page_objects, display_to_user, T_TEXT

ASSET_FONT = Path(__file__).parent / "assets" / "GlyphLessFont.ttf"
LANG_RE = re.compile(r"^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$")


class OcrError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


# ----------------------------------------------------------------------------- engine
def engine_info(cfg) -> dict:
    cmd = cfg.tesseract_cmd
    exe = cmd if os.path.isfile(cmd) else shutil.which(cmd)
    if not exe and (not cmd or cmd == "tesseract"):
        from .config import _find_tesseract
        candidate = _find_tesseract()
        if os.path.isfile(candidate):
            exe = candidate
    info = {"available": False, "command": exe or cfg.tesseract_cmd, "version": None, "languages": [], "error": None, "install_help": INSTALL_HELP}
    if not exe:
        info["error"] = f"Tesseract executable '{cfg.tesseract_cmd}' was not found. Install Tesseract OCR (see install_help) or set TESSERACT_CMD."
        return info
    env = _env(cfg)
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15, env=env)
        info["version"] = (v.stdout or v.stderr).splitlines()[0].strip()
        l = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=15, env=env)
        if l.returncode != 0:
            info["error"] = (l.stderr or l.stdout).strip()[:300]
            return info
        langs = [x.strip() for x in l.stdout.splitlines()[1:] if x.strip()]
        info["languages"] = sorted(x for x in langs if x not in ("osd",))
        info["available"] = bool(info["languages"])
        if not info["languages"]:
            info["error"] = "Tesseract is installed but no language data (*.traineddata) was found. Install e.g. tesseract-ocr-eng or set TESSDATA_PREFIX."
        info["executable"] = exe
    except subprocess.TimeoutExpired:
        info["error"] = "Tesseract did not respond to --version."
    except Exception as e:
        info["error"] = f"Tesseract could not be started: {e}"
    return info


INSTALL_HELP = {
    "ubuntu/debian": "sudo apt install tesseract-ocr tesseract-ocr-eng   (more languages: tesseract-ocr-tam, tesseract-ocr-hin, ...)",
    "macos": "brew install tesseract tesseract-lang",
    "windows": "Install the UB Mannheim build from https://github.com/UB-Mannheim/tesseract/wiki, tick the extra languages, add it to PATH (or set TESSERACT_CMD).",
    "verify": "tesseract --version && tesseract --list-langs",
}


def _env(cfg):
    env = dict(os.environ)
    env["OMP_THREAD_LIMIT"] = "1"
    if cfg.tessdata_dir:
        env["TESSDATA_PREFIX"] = cfg.tessdata_dir
    return env


# ----------------------------------------------------------------------------- tesseract run
def _run_tesseract(exe, png, langs, psm, dpi, cfg, timeout, procs, cancel):
    cmd = [exe, str(png), "stdout", "-l", langs, "--psm", str(psm), "--dpi", str(int(dpi)), "tsv"]
    if cancel.is_set():
        raise OcrError("canceled", "Canceled")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(cfg))
    procs.add(p)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise OcrError("timeout", f"Tesseract exceeded the {timeout:g}s per-page limit")
    finally:
        procs.discard(p)
    if cancel.is_set():
        raise OcrError("canceled", "Canceled")
    if p.returncode != 0:
        msg = err.decode("utf-8", "replace").strip()
        if "Failed loading language" in msg or "Error opening data file" in msg:
            raise OcrError("missing_language", f"Language data for '{langs}' could not be loaded: {msg[:200]}")
        raise OcrError("tesseract_failed", f"Tesseract exited with code {p.returncode}: {msg[:300]}")
    return out.decode("utf-8", "replace")


def parse_tsv(tsv):
    words = []
    for ln in tsv.splitlines()[1:]:
        c = ln.split("\t")
        if len(c) < 12 or c[0] != "5":
            continue
        txt = c[11].strip()
        if not txt:
            continue
        try:
            conf = float(c[10])
            words.append({"text": txt, "l": int(c[6]), "t": int(c[7]), "w": int(c[8]), "h": int(c[9]), "conf": conf,
                          "line": (int(c[2]), int(c[3]), int(c[4]))})
        except ValueError:
            continue
    return words


# ----------------------------------------------------------------------------- one page
def _render(pdf_path, idx, dpi, max_pixels):
    with LOCK:
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            page = doc[idx]
            info = page_display_info(page)
            scale = dpi / 72.0
            px = info["width"] * scale * info["height"] * scale
            if px > max_pixels:
                scale *= (max_pixels / px) ** 0.5
            img = page.render(scale=scale).to_pil().convert("L")
            native = [o.bbox for o in page_objects(page) if o.kind == T_TEXT and o.text.strip()]
            page.close()
        finally:
            doc.close()
    return img, scale, info, native


def ocr_page(pdf_path, idx, langs, dpi, psm, cfg, workdir, procs, cancel, skip_overlap=True):
    """Return dict with words (pixel coordinates), geometry and stats. Raises OcrError."""
    t0 = time.time()
    try:
        img, scale, info, native = _render(pdf_path, idx, dpi, cfg.ocr_max_pixels)
    except Exception as e:
        raise OcrError("render_failed", f"Page {idx + 1} could not be rendered: {e}")
    png = Path(workdir) / f"p{idx + 1}.png"
    img.save(png, dpi=(dpi, dpi))
    exe = shutil.which(cfg.tesseract_cmd) or cfg.tesseract_cmd
    tsv = _run_tesseract(exe, png, langs, psm, scale * 72, cfg, cfg.ocr_page_timeout_s, procs, cancel)
    try:
        png.unlink()
    except OSError:
        pass
    words = parse_tsv(tsv)
    skipped = 0
    if skip_overlap and native and words:
        kept = []
        for w in words:
            cu, cv = (w["l"] + w["w"] / 2) / scale, (w["t"] + w["h"] / 2) / scale
            ux, uy = display_to_user(info, cu, cv)
            if any(b[0] - 1 <= ux <= b[2] + 1 and b[1] - 1 <= uy <= b[3] + 1 for b in native):
                skipped += 1
            else:
                kept.append(w)
        words = kept
    confs = [w["conf"] for w in words if w["conf"] >= 0]
    mean = round(sum(confs) / len(confs), 1) if confs else None
    low = sum(1 for c in confs if c < 60)
    flags = []
    if not words:
        flags.append("no text recognised (blank page, picture only, or wrong language)")
    if mean is not None and mean < 70:
        flags.append(f"low mean confidence ({mean})")
    if confs and low / len(confs) > 0.3:
        flags.append(f"{low} of {len(confs)} words below confidence 60")
    return {"index": idx, "words": words, "scale": scale, "info": info, "width_px": img.width, "height_px": img.height,
            "mean_conf": mean, "low_conf_words": low, "word_count": len(words), "skipped_overlap": skipped, "flags": flags,
            "seconds": round(time.time() - t0, 2), "text": _text_of(words)}


def _text_of(words):
    lines, cur, key = [], [], None
    for w in words:
        if key is not None and w["line"] != key:
            lines.append(" ".join(cur))
            cur = []
        key = w["line"]
        cur.append(w["text"])
    if cur:
        lines.append(" ".join(cur))
    return "\n".join(lines)


# ----------------------------------------------------------------------------- text layer
def _glyphless_font(pdf):
    ttf = ASSET_FONT.read_bytes()
    ff = pdf.make_stream(ttf)
    ff.Length1 = len(ttf)
    fd = pdf.make_indirect(Dictionary(Type=Name.FontDescriptor, FontName=Name("/GlyphLessFont"), Flags=5, FontBBox=Array([0, -1, 1000, 1000]),
                                      ItalicAngle=0, Ascent=1000, Descent=-1, CapHeight=1000, StemV=80, FontFile2=ff))
    c2g = pdf.make_stream(b"\x00\x01" * 65536)       # every CID -> glyph 1 (blank); pikepdf compresses on save
    cid = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.CIDFontType2, BaseFont=Name("/GlyphLessFont"),
                                       CIDSystemInfo=Dictionary(Registry=pikepdf.String("Adobe"), Ordering=pikepdf.String("Identity"), Supplement=0),
                                       FontDescriptor=fd, DW=500, CIDToGIDMap=c2g))
    tou = pdf.make_stream(b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def "
                          b"/CMapName /Adobe-Identity-UCS def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange "
                          b"1 beginbfrange <0000> <FFFF> <0000> endbfrange endcmap CMapName currentdict /CMap defineresource pop end end")
    return pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type0, BaseFont=Name("/GlyphLessFont"), Encoding=Name("/Identity-H"),
                                        DescendantFonts=Array([cid]), ToUnicode=tou))


def _hex16(s):
    out = []
    for ch in s:
        cp = ord(ch)
        if cp < 32 or cp > 0xFFFF or 0xD800 <= cp <= 0xDFFF:
            continue
        out.append(f"{cp:04X}")
    return "".join(out)


def text_layer_ops(res, fontname="AFXOCR"):
    info, scale = res["info"], res["scale"]
    ops, n = [], 0
    for w in res["words"]:
        text = _hex16(w["text"])
        if not text:
            continue
        nchar = len(text) // 4
        u0, v0 = w["l"] / scale, (w["t"] + w["h"]) / scale        # bottom-left of the word box (display pt from top-left)
        wpt, hpt = w["w"] / scale, w["h"] / scale
        if wpt < 0.5 or hpt < 1:
            continue
        fs = max(1.0, hpt * 0.85)
        base = v0 - hpt * 0.18
        px, py = display_to_user(info, u0, base)
        rx, ry = display_to_user(info, u0 + 1, base)
        ux, uy = display_to_user(info, u0, base - 1)
        a, b, c, d = rx - px, ry - py, ux - px, uy - py
        tz = max(10.0, min(1000.0, wpt / (nchar * 0.5 * fs) * 100.0))
        ops.append(f"BT 3 Tr /{fontname} {fs:.2f} Tf {tz:.1f} Tz {a:.4f} {b:.4f} {c:.4f} {d:.4f} {px:.2f} {py:.2f} Tm <{text}0020> Tj ET")
        n += 1
    return "\n".join(ops), n


def apply_layers(src, dst, results):
    """results: {page_index: ocr_page result}. Writes dst; returns {index: n_words_written}."""
    written = {}
    with pikepdf.open(str(src)) as pdf:
        font = _glyphless_font(pdf)
        for idx, res in sorted(results.items()):
            ops, n = text_layer_ops(res)
            written[idx] = n
            if not n:
                continue
            page = pdf.pages[idx]
            page.add_resource(font, Name.Font, Name("/AFXOCR"), replace_existing=True)
            page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
            page.contents_add(pdf.make_stream(("Q\nq\n" + ops + "\nQ\n").encode("latin-1")))
        pdf.save(str(dst), object_stream_mode=pikepdf.ObjectStreamMode.preserve)
    return written


# ----------------------------------------------------------------------------- orchestration
def run_ocr(src, dst, pages, langs, dpi, psm, cfg, ctl, skip_overlap=True):
    """pages: 0-based indices. ctl: object with .cancel (Event), .procs (set), .progress(done,total,msg), .log(level,msg).
    Returns dict(summary, per_page). Raises OcrError for job-level failures."""
    eng = engine_info(cfg)
    if not eng["available"]:
        raise OcrError("engine_unavailable", eng["error"] or "OCR engine unavailable")
    if not LANG_RE.match(langs or ""):
        raise OcrError("bad_language", "Language must look like 'eng' or 'eng+deu'")
    missing = [l for l in langs.split("+") if l not in eng["languages"]]
    if missing:
        raise OcrError("missing_language", f"Language data not installed: {', '.join(missing)}. Installed: {', '.join(eng['languages'])}")
    with pikepdf.open(str(src)) as pdf:
        if pdf.is_encrypted:
            raise OcrError("encrypted", "Encrypted PDFs cannot be modified. Remove the encryption in the source application first.")
        total_pages = len(pdf.pages)
    bad = [p for p in pages if p < 0 or p >= total_pages]
    if bad or not pages:
        raise OcrError("bad_pages", "No valid pages selected" if not pages else f"Pages out of range: {[b + 1 for b in bad][:5]}")
    work = Path(tempfile.mkdtemp(prefix="afx-ocr-"))
    results, per_page = {}, {}
    done = 0
    t0 = time.time()
    ctl.progress(0, len(pages), "Starting OCR")
    try:
        with ThreadPoolExecutor(max_workers=max(1, cfg.ocr_workers)) as ex:
            futs = {ex.submit(ocr_page, src, p, langs, dpi, psm, cfg, work, ctl.procs, ctl.cancel, skip_overlap): p for p in pages}
            for fut in as_completed(futs):
                p = futs[fut]
                try:
                    r = fut.result()
                    results[p] = r
                    per_page[p + 1] = {"page": p + 1, "status": "ok", "words": r["word_count"], "mean_confidence": r["mean_conf"], "low_confidence_words": r["low_conf_words"],
                                       "skipped_overlapping_native": r["skipped_overlap"], "flags": r["flags"], "seconds": r["seconds"], "text": r["text"][:6000],
                                       "words_data": [{"text": w["text"], "conf": w.get("conf", 0.0)} for w in r["words"][:2000]]}
                    ctl.log("warn" if r["flags"] else "info", f"Page {p + 1}: {r['word_count']} words, mean confidence {r['mean_conf']}" + (f" - {'; '.join(r['flags'])}" if r["flags"] else ""))
                except OcrError as e:
                    if e.code == "canceled":
                        per_page[p + 1] = {"page": p + 1, "status": "canceled", "error": "Canceled"}
                    else:
                        per_page[p + 1] = {"page": p + 1, "status": "failed", "error_code": e.code, "error": e.message}
                        ctl.log("error", f"Page {p + 1} failed: {e.message}")
                except Exception as e:  # unexpected - never swallow silently
                    per_page[p + 1] = {"page": p + 1, "status": "failed", "error_code": "internal", "error": f"{type(e).__name__}: {e}"}
                    ctl.log("error", f"Page {p + 1} failed unexpectedly: {type(e).__name__}: {e}")
                done += 1
                ctl.progress(done, len(pages), f"{done} of {len(pages)} pages processed")
                if time.time() - t0 > cfg.ocr_job_timeout_s:
                    ctl.cancel.set()
                    ctl.log("error", f"Job time limit of {cfg.ocr_job_timeout_s:.0f}s reached; remaining pages canceled")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if ctl.cancel.is_set():
        raise OcrError("canceled", "OCR was canceled; no output file was written")
    if not results:
        raise OcrError("all_failed", "OCR failed on every selected page: " + "; ".join(sorted({v.get("error", "") for v in per_page.values()})[:3]))
    ctl.log("info", "Writing the text layer into the PDF")
    written = apply_layers(src, dst, results)
    for idx, n in written.items():
        per_page[idx + 1]["words_written"] = n
    return {"engine": eng["version"], "languages": langs, "dpi": dpi, "psm": psm, "pages_requested": len(pages), "pages_ok": len(results),
            "pages_failed": sorted(p for p, v in per_page.items() if v["status"] != "ok"), "per_page": [per_page[k] for k in sorted(per_page)],
            "seconds": round(time.time() - t0, 1), "skip_overlap": skip_overlap}, results


# ----------------------------------------------------------------------------- verification
def _norm_words(text):
    return [w for w in re.findall(r"[^\W_]{3,}", text.lower())]


def _extract_pages(path, idxs):
    out = {}
    with LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            for i in idxs:
                pg = doc[i]
                tp = pg.get_textpage()
                out[i] = tp.get_text_range() or ""
                tp.close()
                pg.close()
        finally:
            doc.close()
    return out


def _thumb(path, i):
    with LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            pg = doc[i]
            a = np.asarray(pg.render(scale=1.0).to_pil().convert("L"), dtype=np.int16)
            pg.close()
        finally:
            doc.close()
    return a


def verify_output(src, dst, ocr_results, per_page_summary):
    """Independent post-processing checks on the written file. Returns list of check dicts."""
    checks = []

    def add(name, ok, detail, level=None):
        checks.append({"name": name, "ok": ok, "level": level or ("pass" if ok else "fail"), "detail": detail})

    with pikepdf.open(str(src)) as a, pikepdf.open(str(dst)) as b:
        add("Page count unchanged", len(a.pages) == len(b.pages), f"{len(a.pages)} -> {len(b.pages)}")
        geo = []
        for i, (pa, pb) in enumerate(zip(a.pages, b.pages)):
            ka = [str(pa.obj.get(k)) for k in ("/MediaBox", "/CropBox", "/Rotate")]
            kb = [str(pb.obj.get(k)) for k in ("/MediaBox", "/CropBox", "/Rotate")]
            if ka != kb:
                geo.append(i + 1)
        add("Page size, crop box and rotation unchanged", not geo, "identical on all pages" if not geo else f"differs on pages {geo[:10]}")
        ann_a = [len(p.obj.get("/Annots", [])) for p in a.pages]
        ann_b = [len(p.obj.get("/Annots", [])) for p in b.pages]
        add("Annotations (links, fields) preserved", ann_a == ann_b, f"{sum(ann_a)} annotations before and after" if ann_a == ann_b else "annotation counts changed")
        sa, sb = a.Root.get("/StructTreeRoot"), b.Root.get("/StructTreeRoot")
        if sa is not None:
            from .structure import build_tree
            from .config import Config
            ia = {p.objgen: i + 1 for i, p in enumerate(a.pages)}
            ib = {p.objgen: i + 1 for i, p in enumerate(b.pages)}
            ta_, tb_ = build_tree(a, ia, Config()), build_tree(b, ib, Config())
            sig = lambda t: [(n["s"], n["pg"], n["d"], tuple(n["mc"]), n["alt"]) for n in t.nodes]
            same = sb is not None and sig(ta_) == sig(tb_) and len(sig(ta_)) > 0
            add("Existing tag structure preserved", same, f"all {len(ta_.nodes)} structure elements identical (type, page, depth, content, alt text)" if same else "the structure tree differs after OCR")
        else:
            add("Existing tag structure preserved", True, "The input had no tag structure to preserve", "info")
        for k in ("/AcroForm", "/Outlines", "/Lang", "/MarkInfo"):
            if k in a.Root:
                add(f"Catalog entry {k} preserved", k in b.Root, "present" if k in b.Root else "missing")
    idx_all = sorted(ocr_results)
    with pikepdf.open(str(src)) as _p:
        n_pages = len(_p.pages)
    others = [i for i in range(n_pages) if i not in ocr_results]
    ta, tb = _extract_pages(src, others), _extract_pages(dst, others)
    changed = [i + 1 for i in others if re.sub(r"\s+", "", ta[i]) != re.sub(r"\s+", "", tb[i])]
    add("Text of pages that were not OCR'd is unchanged", not changed, "identical" if not changed else f"differs on pages {changed[:10]}")
    ta, tb = _extract_pages(src, idx_all), _extract_pages(dst, idx_all)
    lost = [i + 1 for i in idx_all if not set(_norm_words(ta[i])) <= set(_norm_words(tb[i]))]
    add("Native text on OCR'd pages preserved", not lost, "all original words still extractable" if not lost else f"words lost on pages {lost}")
    pd_ok = shutil.which("pdftotext")
    for i in idx_all:
        r = ocr_results[i]
        want = [w for w in _norm_words(" ".join(x["text"] for x in r["words"]))]
        if not want:
            add(f"Page {i + 1}: searchable text layer", False, "OCR produced no words, so there is nothing to search", "warn")
            continue
        got = set(_norm_words(tb[i]))
        rec = sum(1 for w in want if w in got) / len(want)
        detail = f"{rec:.0%} of {len(want)} OCR words are found by pdfium text extraction"
        if pd_ok:
            try:
                o = subprocess.run([pd_ok, "-f", str(i + 1), "-l", str(i + 1), str(dst), "-"], capture_output=True, text=True, timeout=60).stdout
                g2 = set(_norm_words(o))
                detail += f"; {sum(1 for w in want if w in g2) / len(want):.0%} by poppler pdftotext"
            except Exception:
                pass
        add(f"Page {i + 1}: searchable text layer", rec >= 0.9, detail)
        d = np.abs(_thumb(src, i) - _thumb(dst, i))
        mx = int(d.max()) if d.size else 0
        add(f"Page {i + 1}: appearance unchanged", mx <= 8, f"largest pixel difference {mx}/255 after rendering before/after at 72 dpi")
    return checks
