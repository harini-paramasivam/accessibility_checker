"""Document analysis: opens a PDF, runs every implemented check, returns plain JSON-able data."""
from __future__ import annotations
import re
import time
from collections import defaultdict
import pikepdf
import pypdfium2 as pdfium
from pikepdf import Array, Dictionary

from . import __version__
from .findings import (Finding, ERROR, WARNING, MANUAL, NOT_RUN, SEVERITY_ORDER, ua, wcag, mh, iso32000,
                       CLASS_STRUCTURAL_ERROR, CLASS_ACCESSIBILITY_FAILURE, CLASS_SEMANTIC_ISSUE,
                       CLASS_MANUAL_REVIEW, CLASS_NOT_RUN, VERIF_CONFIRMED, VERIF_SUSPECTED,
                       VERIF_MANUAL, VERIF_NOT_RUN)
from .pdfium_content import LOCK, user_to_display, page_objects, page_display_info, T_TEXT, T_IMAGE, T_PATH, T_SHADING
from .scan import classify_page, summarize, text_quality
from .structure import build_tree, validate, _name, _og, _text

GENERIC_LINK = re.compile(r"^\s*(click here|here|read more|more|link|this link|learn more|details|continue|download|this|see more|>>?)\s*\.?\s*$", re.I)
URL_TEXT = re.compile(r"^\s*(https?://|www\.)\S+\s*$", re.I)
LANG_RE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*")
NOT_RUN_CHECKS = [
    ("Colour contrast of text and graphics", "Needs rendered colour analysis per element.", [wcag("1.4.3", "contrast-minimum")]),
    ("Correctness of reading order", "Only a geometric heuristic exists; correctness needs a person (or screen reader).", [wcag("1.3.2", "meaningful-sequence")]),
    ("Accuracy and usefulness of alternative text", "A program cannot judge whether a description fits its image.", [wcag("1.1.1", "non-text-content")]),
    ("Language changes inside the text (/Lang on spans)", "Detecting passages in another language needs language identification.", [wcag("3.1.2", "language-of-parts")]),
    ("Header/data cell association in complex tables", "Only presence of TH, regularity and Scope are checked.", [wcag("1.3.1", "info-and-relationships")]),
    ("Page headers/footers and decorations classified as artifacts", "Whether untagged repeated content is decoration is a semantic decision.", [ua("7.8")]),
    ("XFA forms, JavaScript actions, optional content, embedded files", "Not inspected by this tool.", [ua("7.15"), ua("7.19"), ua("7.10"), ua("7.11")]),
    ("Full PDF/UA-1 / Matterhorn conformance", "Only a subset of machine-checkable conditions is implemented. Use PAC or veraPDF for a conformance verdict.", [ua("5")]),
]


def _page_index(pdf):
    return {p.objgen: i + 1 for i, p in enumerate(pdf.pages)}


def _xmp(pdf):
    try:
        md = pdf.Root.get("/Metadata")
        if md is None:
            return ""
        return bytes(md.read_bytes()).decode("utf-8", "replace")
    except Exception:
        return ""


def _xmp_title(xmp):
    import html
    m = re.search(r"<dc:title\b[^>]*>.*?<rdf:li[^>]*>(.*?)</rdf:li>", xmp, re.S)
    return html.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else ""


def _iter_fonts(pdf, page, seen):
    """Yield (font_dict, page_no_unused) for fonts reachable from the page resources."""
    out = []

    def from_res(res, depth):
        if not isinstance(res, Dictionary) or depth > 3:
            return
        fonts = res.get("/Font")
        if isinstance(fonts, Dictionary):
            for k in fonts.keys():
                f = fonts[k]
                og = _og(f)
                key = og if og else id(f)
                if key in seen:
                    continue
                seen.add(key)
                out.append(f)
        xo = res.get("/XObject")
        if isinstance(xo, Dictionary):
            for k in xo.keys():
                x = xo[k]
                if isinstance(x, pikepdf.Stream) and _name(x.get("/Subtype")) == "Form":
                    og = _og(x)
                    if og and ("x", og) in seen:
                        continue
                    if og:
                        seen.add(("x", og))
                    from_res(x.get("/Resources"), depth + 1)

    try:
        from_res(page.obj.get("/Resources") or page.resources, 0)
    except Exception:
        pass
    return out


def _font_findings(pdf, pages_fonts):
    notemb, nouni, badglyph = defaultdict(list), defaultdict(list), defaultdict(list)
    for pno, fonts in pages_fonts:
        for f in fonts:
            try:
                sub = _name(f.get("/Subtype"))
                base = _name(f.get("/BaseFont")) or "(unnamed)"
                if sub == "Type3":
                    continue
                desc_font = f
                if sub == "Type0":
                    d = f.get("/DescendantFonts")
                    desc_font = d[0] if d is not None and len(d) else f
                fd = desc_font.get("/FontDescriptor")
                emb = isinstance(fd, Dictionary) and any(k in fd for k in ("/FontFile", "/FontFile2", "/FontFile3"))
                if not emb:
                    notemb[base].append(pno)
                has_tu = "/ToUnicode" in f
                enc = f.get("/Encoding")
                if sub == "Type0":
                    if not has_tu and (enc is None or _name(enc) in ("Identity-H", "Identity-V")):
                        nouni[base].append(pno)
                else:
                    diffs = enc.get("/Differences") if isinstance(enc, Dictionary) else None
                    if diffs is not None and not has_tu:
                        names = [_name(x) for x in diffs if isinstance(x, pikepdf.Name)]
                        bad = [n for n in names if re.fullmatch(r"(g|G|glyph|cid|c|index|char|Char|uni0*)?\d{1,5}", n) or re.fullmatch(r"(g|glyph|cid)\d+", n)]
                        if bad:
                            badglyph[base].append(pno)
                    elif enc is None and not has_tu and sub in ("TrueType", "Type1") and fd is not None and isinstance(fd, Dictionary) \
                            and int(fd.get("/Flags", 32)) & 4 and emb:
                        nouni[base].append(pno)
            except Exception:
                continue
    F = []
    if notemb:
        F.append(Finding("FONT-NOT-EMBEDDED", ERROR, "Fonts", f"{len(notemb)} font(s) are not embedded",
                         evidence=[f"{b} (pages {sorted(set(p))[:8]})" for b, p in notemb.items()][:15], pages=[p for v in notemb.values() for p in v],
                         refs=[ua("7.21.4"), mh("31", "Fonts")], count=len(notemb),
                         recommendation="Embed all fonts when exporting or printing to PDF. The legacy tagging workbench can embed metric-compatible substitutes for common fonts."))
    if nouni:
        F.append(Finding("FONT-NO-TOUNICODE", ERROR, "Fonts", f"{len(nouni)} font(s) have no usable Unicode mapping",
                         evidence=[f"{b} (pages {sorted(set(p))[:8]}): no /ToUnicode and an encoding that cannot be mapped by itself" for b, p in nouni.items()][:15],
                         pages=[p for v in nouni.values() for p in v], refs=[ua("7.21.7"), mh("31", "Fonts"), wcag("1.3.1", "info-and-relationships")], count=len(nouni),
                         recommendation="Re-export from the source with Unicode mapping enabled. Without it, copy, search and screen readers return wrong characters."))
    if badglyph:
        F.append(Finding("FONT-GLYPH-NAMES", WARNING, "Fonts", f"{len(badglyph)} font(s) use glyph names that cannot be mapped to Unicode",
                         evidence=[f"{b} (pages {sorted(set(p))[:8]})" for b, p in badglyph.items()][:15], pages=[p for v in badglyph.values() for p in v],
                         refs=[ua("7.21.7")], count=len(badglyph), recommendation="Check extracted text for wrong characters; add /ToUnicode if available."))
    return F


def analyze(path, cfg, progress=None, display_name="document.pdf"):
    t0 = time.time()
    res = {"tool": {"name": "AccessFix PDF", "version": __version__,
                    "libraries": {"pikepdf": pikepdf.__version__, "pypdfium2": pdfium.version.PYPDFIUM_INFO.version if hasattr(pdfium, "version") else "?"}},
           "file": {"name": display_name}, "state": "ok", "tag_status": None, "findings": [], "pages": [], "structure": None}
    try:
        pdf = pikepdf.open(path)
    except pikepdf.PasswordError:
        res.update(state="encrypted_locked", message="This PDF needs a password to open. Remove the password in the source application and upload it again.")
        return res
    except Exception as e:
        res.update(state="unreadable", message=f"The file could not be parsed as a PDF: {str(e)[:300]}")
        return res
    with pdf:
        n_pages = len(pdf.pages)
        if n_pages == 0:
            res.update(state="empty", message="The PDF contains no pages.")
            return res
        if n_pages > cfg.max_pages:
            res.update(state="too_many_pages", message=f"The PDF has {n_pages} pages; this server allows at most {cfg.max_pages} (ACCESSFIX_MAX_PAGES).")
            return res
        return _analyze_open(pdf, path, cfg, progress, res, n_pages, t0)


def _analyze_open(pdf, path, cfg, progress, res, n_pages, t0):
    F: list[Finding] = []
    root = pdf.Root
    try:
        version = float(str(pdf.pdf_version))
    except Exception:
        version = 1.7
    # ---- file-level integrity (qpdf) ------------------------------------------
    problems = []
    try:
        problems += [str(w) for w in pdf.get_warnings()]
        problems += [str(w) for w in pdf.check_pdf_syntax()]
    except Exception as e:  # a failing checker is reported as a limitation, not as a file defect
        problems = []
        F.append(Finding("BUDGET", NOT_RUN, "File", "Low-level file integrity check could not run", evidence=[str(e)[:160]]))
    if problems:
        F.append(Finding("FILE-STRUCTURE", WARNING, "File", "PDF file structure has problems (repaired while reading)",
                         evidence=[str(p)[:200] for p in problems[:10]], count=len(problems), refs=[iso32000("7")],
                         recommendation="Re-save the file in a PDF editor. Tag results may be less reliable for damaged files."))
    # ---- metadata ------------------------------------------------------------------
    info_title = ""
    try:
        info_title = str(pdf.docinfo.get("/Title", "")).strip()
    except Exception:
        pass
    xmp = _xmp(pdf)
    xmp_title = _xmp_title(xmp)
    lang = _text(root.get("/Lang")).strip() if "/Lang" in root else ""
    mi = root.get("/MarkInfo")
    marked = bool(isinstance(mi, Dictionary) and mi.get("/Marked") is True)
    vp = root.get("/ViewerPreferences")
    ddt = bool(isinstance(vp, Dictionary) and vp.get("/DisplayDocTitle") is True)
    outlines = root.get("/Outlines")
    has_outlines = isinstance(outlines, Dictionary) and "/First" in outlines
    claims_ua = bool(re.search(r"pdfuaid:part\s*(=\s*[\"']\d|>\s*\d)", xmp))
    encrypted = bool(pdf.is_encrypted)
    allow_access = True
    if encrypted:
        try:
            allow_access = bool(pdf.allow.accessibility)
        except Exception:
            allow_access = True
    res["file"].update({
        "pages": n_pages, "size": path.stat().st_size, "pdf_version": f"{version:.1f}", "title": info_title or xmp_title, "info_title": info_title,
        "xmp_title": xmp_title, "language": lang, "producer": str(pdf.docinfo.get("/Producer", "")) if pdf.docinfo else "",
        "creator": str(pdf.docinfo.get("/Creator", "")) if pdf.docinfo else "", "encrypted": encrypted, "marked": marked,
        "display_doc_title": ddt, "has_bookmarks": has_outlines, "claims_pdfua": claims_ua,
        "has_acroform": "/AcroForm" in root})

    pidx = _page_index(pdf)
    # ---- per-page content pass -----------------------------------------------------
    pdoc = None
    pages_info, page_content, scan_pages, untagged, artmix, text_anoms = [], {}, [], defaultdict(lambda: defaultdict(int)), defaultdict(int), {}
    pages_fonts, seen_fonts = [], set()
    pages_not_analysed = []
    try:
        pdoc = pdfium.PdfDocument(str(path))
    except Exception as e:
        F.append(Finding("PAGES-UNREADABLE", WARNING, "File", "The page renderer could not open the file; content checks skipped", evidence=[str(e)[:200]]))
    page_tagged_objs, real_content_pages = defaultdict(int), set()
    for i in range(n_pages):
        pno = i + 1
        if progress:
            progress(i, n_pages, f"Inspecting page {pno} of {n_pages}")
        pages_fonts.append((pno, _iter_fonts(pdf, pdf.pages[i], seen_fonts)))
        if pdoc is None:
            continue
        if time.time() - t0 > cfg.analysis_time_budget_s:
            pages_not_analysed.append(pno)
            continue
        try:
            with LOCK:
                pg = pdoc[i]
                info = page_display_info(pg)
                objs = page_objects(pg)
                tp = pg.get_textpage()
                try:
                    full = tp.get_text_range() or ""
                finally:
                    tp.close()
                pg.close()
        except Exception as e:
            pages_not_analysed.append(pno)
            scan_pages.append({"page": pno, "class": "graphics_sparse", "label": "Page could not be read", "confidence": "heuristic", "ocr_candidate": False,
                               "ocr_optional": False, "reasons": [f"Page could not be read by the renderer: {str(e)[:160]}"], "error": True, "width": 0, "height": 0, "rotation": 0,
                               "visible_chars": 0, "invisible_chars": 0, "image_count": 0, "image_coverage": 0, "largest_image_coverage": 0, "text_quality": text_quality("")})
            continue
        page_content[pno] = objs
        sp = classify_page(pno, objs, info, full)
        scan_pages.append(sp)
        pages_info.append({"page": pno, "width": info["width"], "height": info["height"], "rotation": info["rotation"], "crop": info["crop"]})
        q = sp["text_quality"]
        if q["suspicious"] or "\ufffd" in full or any(0xE000 <= ord(c) <= 0xF8FF for c in full):
            text_anoms[pno] = q
        for o in objs:
            if o.kind not in (T_TEXT, T_IMAGE, T_PATH, T_SHADING):
                continue
            if o.kind == T_TEXT and not o.text.strip() and not o.in_form:
                continue
            if o.artifact and o.mcids:
                artmix[pno] += 1
            if o.artifact:
                continue
            real_content_pages.add(pno)
            if o.mcids:
                page_tagged_objs[pno] += 1
            else:
                k = {T_TEXT: "invisible text (OCR layer)" if o.render_mode == 3 else "text", T_IMAGE: "image", T_PATH: "vector graphic", T_SHADING: "shading"}[o.kind]
                untagged[pno][k] += 1
    if progress:
        progress(n_pages, n_pages, "Checking structure")
    res["pages"] = scan_pages
    res["scan"] = summarize(scan_pages) if scan_pages else None
    if pages_not_analysed:
        F.append(Finding("BUDGET", NOT_RUN, "Limits", f"{len(pages_not_analysed)} page(s) were not analysed (time budget {cfg.analysis_time_budget_s:.0f}s reached or page unreadable)",
                         pages=pages_not_analysed, evidence=["Content, scan and tag cross-checks cover only the analysed pages."]))

    # ---- structure tree ------------------------------------------------------------------
    page_index_by_og = pidx
    tree = build_tree(pdf, page_index_by_og, cfg, pdf20=version >= 2.0)
    sfind, ssum = validate(pdf, tree, page_content, page_index_by_og, n_pages, version)
    F += sfind
    nodes = tree.nodes
    geo = {pi["page"]: pi for pi in pages_info}
    for n in nodes:      # display-space box as fractions of the page, for highlighting in the preview
        n["dbox"] = None
        if n.get("bbox") and n.get("bpg") in geo:
            g = geo[n["bpg"]]
            l, b, r, t = n["bbox"]
            pts = [user_to_display(g, x, y) for x, y in ((l, b), (r, t), (l, t), (r, b))]
            xs, ys = [q[0] / g["width"] for q in pts], [q[1] / g["height"] for q in pts]
            n["dbox"] = [round(max(0, min(xs)), 4), round(max(0, min(ys)), 4), round(min(1, max(xs)), 4), round(min(1, max(ys)), 4)]
    n_elements = len(nodes)

    # ---- tag status ----------------------------------------------------------------------------
    integrity_codes = {"STRUCT-CYCLE", "STRUCT-DUPLICATE", "STRUCT-PARENT-LINK", "STRUCT-BAD-KIDS", "ROLE-CIRCULAR", "ROLE-UNMAPPED",
                       "MCID-DANGLING", "MCID-UNREFERENCED", "MCID-DUPLICATE", "PT-MISMATCH", "PT-ORPHAN", "PT-MISSING", "PT-UNREADABLE"}
    integ = [f for f in sfind if f.code in integrity_codes and f.severity == ERROR]
    untagged_text_pages = sorted(p for p, d in untagged.items() if any(k.startswith(("text", "invisible")) for k in d))
    untagged_total = sum(sum(d.values()) for d in untagged.values())
    reasons = []
    if not tree.has_root and not marked:
        status = "untagged"
        reasons.append("The document has no /StructTreeRoot and /MarkInfo /Marked is not true.")
    elif not tree.has_root and marked:
        status = "malformed"
        reasons.append("/MarkInfo /Marked is true but there is no /StructTreeRoot.")
    elif n_elements == 0:
        status = "untagged"
        reasons.append("A /StructTreeRoot exists but contains no structure elements.")
    elif integ:
        status = "malformed"
        reasons.append("Structure integrity errors: " + ", ".join(sorted({f.code for f in integ})))
    else:
        tagged_pages = {p for p, c in page_tagged_objs.items() if c}
        empty_pages = sorted(p for p in real_content_pages if p not in tagged_pages)
        if empty_pages or untagged_text_pages:
            status = "partially_tagged"
            if empty_pages:
                reasons.append(f"{len(empty_pages)} page(s) with content have no tagged content: {empty_pages[:12]}")
            if untagged_text_pages:
                reasons.append(f"Untagged text on page(s) {untagged_text_pages[:12]}")
        else:
            status = "tagged"
            reasons.append(f"{n_elements} structure elements; every page with content has tagged content.")
    if tree.has_root and n_elements and not marked:
        reasons.append("But /MarkInfo /Marked is not true, so readers may ignore the tags.")
    res["tag_status"] = {"status": status, "reasons": reasons, "elements": n_elements, "marked": marked, "has_struct_root": tree.has_root}

    # ---- document-level findings ---------------------------------------------------------------
    if encrypted and not allow_access:
        F.append(Finding("SEC-ACCESSIBILITY", ERROR, "Document", "Encryption blocks content extraction for accessibility", refs=[ua("7.16"), mh("26", "Security")],
                         recommendation="Allow 'content access for accessibility' in the document security settings.", evidence=["pikepdf reports allow.accessibility = False"]))
    if status == "untagged":
        F.append(Finding("TAG-NONE", ERROR, "Structure", "The document is not tagged",
                         detail="Screen readers cannot tell headings, paragraphs, lists, tables or figures apart in an untagged PDF.",
                         evidence=reasons, refs=[ua("7.1"), mh("01", "Real content tagged"), wcag("1.3.1", "info-and-relationships")],
                         recommendation="Re-export from the source application with tagging enabled, or use the Tagging workbench (heuristic auto-tagging plus manual review). For scans, run OCR first; OCR alone does not tag."))
    if tree.has_root and n_elements and not marked:
        F.append(Finding("MARKINFO", ERROR, "Structure", "Document is not flagged as tagged (/MarkInfo /Marked)", evidence=["MarkInfo.Marked is missing or false although a structure tree exists."],
                         refs=[ua("7.1"), mh("06", "Metadata / MarkInfo")], recommendation="Set Marked to true once the tag tree is correct.",
                         fix={"action": "set_mark_info", "label": "Set Marked = true", "needs_confirmation": status in ("malformed",), "params": {},
                              "description": "Declares the document as tagged. Deterministic, but only correct if the tags are."}))
    if tree.has_root is False and marked:
        F.append(Finding("MARKINFO-NOTREE", ERROR, "Structure", "Flagged as tagged but there is no structure tree", refs=[ua("7.1")]))
    if not (info_title or xmp_title):
        F.append(Finding("META-TITLE", ERROR, "Document", "Document title is missing", detail="Screen readers announce the file name instead of a title.",
                         refs=[ua("7.1"), mh("06", "Metadata"), wcag("2.4.2", "page-titled")], recommendation="Enter a descriptive title.",
                         fix={"action": "set_title", "label": "Set document title", "needs_input": True, "needs_confirmation": False, "params": {}, "description": "Writes /Info Title and XMP dc:title."}))
    elif not xmp_title:
        F.append(Finding("META-XMP-TITLE", ERROR, "Document", "Title exists in Info but not in XMP metadata (dc:title)", evidence=[f"Info title: {info_title[:80]}"],
                         refs=[ua("7.1"), mh("06", "Metadata")], recommendation="Copy the title into XMP.",
                         fix={"action": "sync_xmp_title", "label": "Write title to XMP", "needs_confirmation": False, "params": {}, "description": "Copies the existing title into XMP dc:title."}))
    t_eff = (info_title or xmp_title)
    if t_eff and (re.search(r"\.(pdf|docx?|indd|pptx?)$", t_eff, re.I) or t_eff.lower() in ("untitled", "document", "microsoft word - document1")):
        F.append(Finding("META-TITLE-WEAK", WARNING, "Document", "Title looks like a file name or placeholder (heuristic)", evidence=[t_eff[:100]], refs=[wcag("2.4.2", "page-titled")],
                         recommendation="Use a title that describes the document."))
    if not ddt:
        F.append(Finding("META-DDT", ERROR, "Document", "Window title does not show the document title (DisplayDocTitle)", refs=[ua("7.1"), mh("07", "Viewer preferences"), wcag("2.4.2", "page-titled")],
                         recommendation="Set ViewerPreferences/DisplayDocTitle to true.",
                         fix={"action": "set_display_doc_title", "label": "Turn on DisplayDocTitle", "needs_confirmation": False, "params": {}, "description": "Safe metadata change."}))
    if not lang:
        F.append(Finding("META-LANG", ERROR, "Document", "Document language is not declared", detail="Screen readers may use the wrong voice and pronunciation.",
                         refs=[ua("7.2"), mh("11", "Natural language"), wcag("3.1.1", "language-of-page")], recommendation="Choose the main language of the document.",
                         fix={"action": "set_lang", "label": "Set document language", "needs_input": True, "needs_confirmation": False, "params": {}, "description": "Writes /Lang on the catalog."}))
    elif not LANG_RE.fullmatch(lang):
        F.append(Finding("META-LANG-INVALID", ERROR, "Document", f"Document language \"{lang}\" is not a valid language tag", refs=[ua("7.2"), wcag("3.1.1", "language-of-page")],
                         fix={"action": "set_lang", "label": "Set document language", "needs_input": True, "needs_confirmation": False, "params": {}, "description": ""}))
    if n_pages > 20 and not has_outlines:
        F.append(Finding("NAV-BOOKMARKS", WARNING, "Navigation", f"{n_pages} pages and no bookmarks", refs=[ua("7.17"), wcag("2.4.5", "multiple-ways")],
                         recommendation="Add bookmarks (from headings) so readers can navigate."))
    if claims_ua:
        F.append(Finding("PDFUA-CLAIM", MANUAL, "Document", "The file claims PDF/UA conformance (pdfuaid:part in XMP)", evidence=["This tool does not verify the claim."],
                         refs=[ua("5")], recommendation="Validate with PAC or veraPDF."))

    # ---- annotations: links, widgets, others ----------------------------------------------------------
    link_total = link_nocontents = link_untagged = link_generic = 0
    link_ev_nc, link_ev_ut, link_ev_gen = [], [], []
    widgets_noname, widgets_untagged, annots_untagged, tabs_missing = [], [], [], []
    fixable = {"links": [], "widgets": []}
    parent_tree = None
    try:
        if tree.has_root and tree.root_dict.get("/ParentTree") is not None:
            parent_tree = pikepdf.NumberTree(tree.root_dict.get("/ParentTree"))
    except Exception:
        parent_tree = None
    node_by_og = tree.by_og

    def struct_type_of(key):
        if parent_tree is None:
            return None
        try:
            if key in parent_tree:
                e = parent_tree[key]
                if isinstance(e, Dictionary) and "/S" in e:
                    og = _og(e)
                    if og in node_by_og:
                        return nodes[node_by_og[og]]["t"]
        except Exception:
            pass
        return None

    link_text_budget = 300
    for i in range(n_pages):
        pno = i + 1
        pg = pdf.pages[i]
        annots = pg.obj.get("/Annots")
        if not isinstance(annots, Array) or not len(annots):
            continue
        real = [a for a in annots if isinstance(a, Dictionary) and _name(a.get("/Subtype")) not in ("Popup", "PrinterMark", "TrapNet")]
        if real and _name(pg.obj.get("/Tabs")) != "S":
            tabs_missing.append(pno)
        for a in real:
            sub = _name(a.get("/Subtype"))
            sp = a.get("/StructParent")
            stype = struct_type_of(int(sp)) if sp is not None else None
            if sub == "Link":
                link_total += 1
                if not _text(a.get("/Contents")).strip():
                    link_nocontents += 1
                    link_ev_nc.append(f"Page {pno}: link without /Contents")
                    og = _og(a)
                    if og and len(fixable["links"]) < 200:
                        uri = _text(a.get("/A").get("/URI")) if isinstance(a.get("/A"), Dictionary) and "/URI" in a.get("/A") else ""
                        fixable["links"].append({"obj": f"{og[0]} {og[1]}", "page": pno, "uri": uri[:120]})
                if sp is None or (stype is not None and stype != "Link") or (tree.has_root is False):
                    link_untagged += 1
                    link_ev_ut.append(f"Page {pno}: link annotation is not inside a Link structure element" + (f" (found {stype})" if stype else ""))
                if pdoc is not None and link_text_budget > 0:
                    try:
                        link_text_budget -= 1
                        r = [float(x) for x in a.get("/Rect")]
                        l, b, rr, t = min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])
                        with LOCK:
                            pp = pdoc[i]
                            tp = pp.get_textpage()
                            tx = tp.get_text_bounded(l, b, rr, t).strip()
                            tp.close()
                            pp.close()
                        if tx and (GENERIC_LINK.match(tx) or URL_TEXT.match(tx)):
                            link_generic += 1
                            link_ev_gen.append(f"Page {pno}: \"{tx[:60]}\"")
                    except Exception:
                        pass
            elif sub == "Widget":
                fld = a
                n_guard = 0
                while isinstance(fld, Dictionary) and "/T" not in fld and "/Parent" in fld and n_guard < 20:
                    fld = fld["/Parent"]
                    n_guard += 1
                tu = ""
                f2 = a
                n_guard = 0
                while isinstance(f2, Dictionary) and n_guard < 20:
                    if "/TU" in f2:
                        tu = _text(f2["/TU"]).strip()
                        break
                    f2 = f2.get("/Parent") if "/Parent" in f2 else None
                    n_guard += 1
                if not tu:
                    widgets_noname.append(f"Page {pno}: field \"{_text(fld.get('/T'))[:40]}\" has no /TU description")
                    og = _og(fld)
                    if og and len(fixable["widgets"]) < 200:
                        fixable["widgets"].append({"obj": f"{og[0]} {og[1]}", "page": pno, "name": _text(fld.get("/T"))[:60]})
                if sp is None or stype != "Form":
                    widgets_untagged.append(f"Page {pno}: widget \"{_text(fld.get('/T'))[:40]}\" is not inside a Form structure element" + (f" (found {stype})" if stype else ""))
            else:
                if sp is None or (stype is not None and stype not in ("Annot", "Link", "Form")):
                    annots_untagged.append(f"Page {pno}: {sub} annotation is not inside an Annot structure element")
    if link_nocontents:
        F.append(Finding("LINK-NO-CONTENTS", ERROR, "Links", f"{link_nocontents} of {link_total} link annotation(s) have no description (/Contents)", evidence=link_ev_nc[:20],
                         pages=[int(re.search(r"Page (\d+)", e).group(1)) for e in link_ev_nc[:200]], count=link_nocontents,
                         refs=[ua("7.18.5"), mh("28", "Annotations"), wcag("2.4.4", "link-purpose-in-context")],
                         recommendation="Give each link a description that states its purpose."))
    if link_untagged:
        F.append(Finding("LINK-UNTAGGED", ERROR, "Links", f"{link_untagged} link annotation(s) are not inside a Link tag", evidence=link_ev_ut[:20],
                         pages=[int(re.search(r"Page (\d+)", e).group(1)) for e in link_ev_ut[:200]], count=link_untagged,
                         refs=[ua("7.18.5"), mh("28", "Annotations")], recommendation="Tag each link as Link containing the link text and an OBJR to the annotation."))
    if link_generic:
        F.append(Finding("LINK-TEXT", WARNING, "Links", f"{link_generic} link(s) with generic or bare-URL text (heuristic)", evidence=link_ev_gen[:20],
                         pages=[int(re.search(r"Page (\d+)", e).group(1)) for e in link_ev_gen[:200]], count=link_generic,
                         refs=[wcag("2.4.4", "link-purpose-in-context")], recommendation="Use link text that makes sense out of context."))
    if widgets_noname:
        F.append(Finding("FORM-NO-TU", ERROR, "Forms", f"{len(widgets_noname)} form field(s) without a description (/TU)", evidence=widgets_noname[:20],
                         pages=[int(re.search(r"Page (\d+)", e).group(1)) for e in widgets_noname[:200]], count=len(widgets_noname),
                         refs=[ua("7.18.4"), mh("28", "Annotations"), wcag("4.1.2", "name-role-value"), wcag("1.3.1", "info-and-relationships")],
                         recommendation="Give every field a tooltip that states what to enter.",
                         fix={"action": "set_widget_tooltip", "label": "Name form fields", "needs_input": True, "needs_confirmation": False, "params": {}, "description": "Provide the label for each field."}))
    if widgets_untagged:
        F.append(Finding("FORM-UNTAGGED", ERROR, "Forms", f"{len(widgets_untagged)} form field(s) are not inside a Form tag", evidence=widgets_untagged[:20],
                         pages=[int(re.search(r"Page (\d+)", e).group(1)) for e in widgets_untagged[:200]], count=len(widgets_untagged), refs=[ua("7.18.4"), mh("28", "Annotations")],
                         recommendation="Tag each field as Form with an OBJR to its widget."))
    if annots_untagged:
        F.append(Finding("ANNOT-UNTAGGED", ERROR, "Annotations", f"{len(annots_untagged)} annotation(s) are not tagged", evidence=annots_untagged[:20],
                         pages=[int(re.search(r"Page (\d+)", e).group(1)) for e in annots_untagged[:200]], count=len(annots_untagged), refs=[ua("7.18.1"), mh("28", "Annotations")]))
    if tabs_missing:
        F.append(Finding("NAV-TABS", ERROR, "Navigation", f"{len(tabs_missing)} page(s) with annotations do not use structure tab order", pages=tabs_missing,
                         evidence=[f"Pages {tabs_missing[:15]} lack /Tabs /S"], count=len(tabs_missing), refs=[ua("7.18.3"), mh("28", "Annotations"), wcag("2.4.3", "focus-order")],
                         recommendation="Set the tab order to 'Use document structure'.",
                         fix={"action": "set_tabs_structure", "label": "Set tab order to structure", "needs_confirmation": False, "params": {}, "description": "Sets /Tabs /S on the listed pages."}))

    # ---- content coverage --------------------------------------------------------------------------------
    if untagged_total and status != "untagged":
        ev = []
        kinds = defaultdict(int)
        for p, d in sorted(untagged.items()):
            for k, v in d.items():
                kinds[k] += v
            ev.append(f"Page {p}: " + ", ".join(f"{v} {k}" for k, v in d.items()))
        F.append(Finding("CONTENT-UNTAGGED", ERROR, "Content", f"{untagged_total} content object(s) are neither tagged nor marked as artifacts",
                         detail="Totals: " + ", ".join(f"{v} {k}" for k, v in kinds.items()), evidence=ev[:25], pages=sorted(untagged), count=untagged_total,
                         refs=[ua("7.1"), mh("01", "Real content tagged"), wcag("1.3.1", "info-and-relationships")],
                         recommendation="Tag the content with the right structure type, or mark decoration as an Artifact. " + (
                             "Invisible text listed here is an OCR text layer: it is searchable but not part of the tag tree." if any("invisible" in k for k in kinds) else "")))
    if artmix:
        F.append(Finding("CONTENT-ARTIFACT-MIX", ERROR, "Content", f"{sum(artmix.values())} content object(s) are both tagged and marked as artifacts", pages=sorted(artmix),
                         evidence=[f"Page {p}: {c}" for p, c in sorted(artmix.items())[:20]], count=sum(artmix.values()),
                         refs=[mh("01", "Real content tagged"), ua("7.1")], recommendation="Content is either real content (tagged) or an artifact, never both."))
    F += _font_findings(pdf, pages_fonts)
    if text_anoms:
        F.append(Finding("TEXT-MAPPING", WARNING, "Text extraction", f"Suspicious characters in the extracted text of {len(text_anoms)} page(s)",
                         evidence=[f"Page {p}: " + ("; ".join(q["reasons"]) or "private-use or replacement characters present") for p, q in list(text_anoms.items())[:15]],
                         pages=list(text_anoms), count=len(text_anoms), refs=[ua("7.21.7"), wcag("1.3.1", "info-and-relationships")],
                         recommendation="Compare the page with its extracted text (Scanned pages & OCR tab > text preview); fix fonts or re-OCR if the text layer is wrong."))

    # ---- scanned / OCR suitability -----------------------------------------------------------------------------
    if scan_pages:
        defin = [p["page"] for p in scan_pages if p["class"] == "scan_no_text" and p["confidence"] == "definitive"]
        heur = [p["page"] for p in scan_pages if p["ocr_candidate"] and p["page"] not in defin and p["class"] != "unreliable_text"]
        unrel = [p["page"] for p in scan_pages if p["class"] == "unreliable_text"]
        if defin:
            F.append(Finding("SCAN-NO-TEXT", ERROR, "Scanned pages", f"{len(defin)} page(s) are images with no text at all (definitive)", pages=defin, count=len(defin),
                             evidence=[f"Page {p}: {next(x for x in scan_pages if x['page'] == p)['reasons'][0]}" for p in defin[:15]],
                             refs=[wcag("1.4.5", "images-of-text"), wcag("1.1.1", "non-text-content"), ua("7.1")],
                             recommendation="Run OCR on these pages (Scanned pages & OCR tab), then tag the result: OCR makes text searchable but does not make the page accessible.",
                             fix={"action": "ocr", "label": "Open OCR panel", "needs_confirmation": False, "params": {"pages": defin}, "description": ""}))
        if heur:
            F.append(Finding("SCAN-LIKELY", WARNING, "Scanned pages", f"{len(heur)} page(s) are probably scans or have almost no text (heuristic)", pages=heur, count=len(heur),
                             evidence=[f"Page {p}: {next(x for x in scan_pages if x['page'] == p)['reasons'][0]}" for p in heur[:15]],
                             refs=[wcag("1.4.5", "images-of-text")], recommendation="Check these pages visually, then OCR them if they contain text."))
        if unrel:
            F.append(Finding("TEXT-UNRELIABLE", WARNING, "Scanned pages", f"{len(unrel)} page(s) have a text layer that looks unreliable (heuristic)", pages=unrel, count=len(unrel),
                             evidence=[f"Page {p}: " + "; ".join(next(x for x in scan_pages if x['page'] == p)['text_quality']['reasons']) for p in unrel[:15]],
                             refs=[wcag("1.3.1", "info-and-relationships")], recommendation="Re-run OCR on these pages and compare the text with the page image."))
        ocrl = res["scan"]["ocr_layer_pages"]
        if ocrl:
            F.append(Finding("OCR-LAYER-REVIEW", MANUAL, "Scanned pages", f"{len(ocrl)} page(s) carry an existing OCR text layer", pages=ocrl, count=len(ocrl),
                             evidence=["The tool detected invisible text over a page image. It cannot know whether the recognised text is correct."],
                             refs=[wcag("1.1.1", "non-text-content")], recommendation="Spot-check the text against the image; OCR errors reach screen-reader users."))
        mx = res["scan"]["mixed_pages"]
        if mx:
            F.append(Finding("MIXED-IMAGE-TEXT", MANUAL, "Scanned pages", f"{len(mx)} page(s) combine a large image with native text", pages=mx, count=len(mx),
                             evidence=["Text drawn inside the image would not be searchable. Decide whether the image contains text that matters."],
                             refs=[wcag("1.4.5", "images-of-text")], recommendation="OCR is optional for these pages. Existing native text is preserved if you choose to run it."))

    # ---- manual review & not run ----------------------------------------------------------------------------------
    if status in ("tagged", "partially_tagged", "malformed"):
        F.append(Finding("MANUAL-READING-ORDER", MANUAL, "Reading order", "Verify the reading order",
                         evidence=["Structure order is what screen readers follow; it is not inferred from the page layout."], refs=[wcag("1.3.2", "meaningful-sequence")],
                         recommendation="Use the Tag tree 'Reading order' view and a screen reader (NVDA, JAWS, VoiceOver)."))
        if ssum["headings"]:
            F.append(Finding("MANUAL-HEADINGS", MANUAL, "Headings", "Confirm that headings reflect the document outline", refs=[wcag("2.4.6", "headings-and-labels")],
                             evidence=[f"{len(ssum['headings'])} heading element(s)."]))
        if ssum["tables"]:
            F.append(Finding("MANUAL-TABLES", MANUAL, "Tables", "Confirm header cells and that data tables are not layout tables", refs=[ua("7.5")], evidence=[f"{ssum['tables']} table(s)."]))
    for title, why, refs in NOT_RUN_CHECKS:
        F.append(Finding("NOTRUN", NOT_RUN, "Not performed", title, evidence=[why], refs=refs))
    if status == "untagged":
        F.append(Finding("NOTRUN-STRUCT", NOT_RUN, "Not performed", "Heading, list, table, figure and reading-order checks",
                         evidence=["These need a tag tree. The document has none, so no result exists (this is not a pass)."], refs=[ua("7.1")]))

    # ---- attach node issues, finalise -----------------------------------------------------------------------------
    for fi, f in enumerate(F):
        if f.severity in (ERROR, WARNING, MANUAL):
            for n in f.nodes:
                if 0 <= n < n_elements and len(nodes[n]["issues"]) < 16:
                    nodes[n]["issues"].append({
                        "f": fi,
                        "code": f.code,
                        "sev": f.severity,
                        "classification": f.classification,
                        "verification": f.verification,
                        "title": f.title,
                    })
    F_dicts = []
    for f in F:
        d = f.to_dict()
        d["index"] = len(F_dicts)
        F_dicts.append(d)
    res["fixable"] = fixable
    res["findings"] = F_dicts
    counts = {s: sum(1 for f in F if f.severity == s) for s in (ERROR, WARNING, MANUAL, NOT_RUN)}
    res["counts"] = counts
    res["classification_counts"] = {
        CLASS_STRUCTURAL_ERROR: sum(1 for f in F if f.classification == CLASS_STRUCTURAL_ERROR),
        CLASS_ACCESSIBILITY_FAILURE: sum(1 for f in F if f.classification == CLASS_ACCESSIBILITY_FAILURE),
        CLASS_SEMANTIC_ISSUE: sum(1 for f in F if f.classification == CLASS_SEMANTIC_ISSUE),
        CLASS_MANUAL_REVIEW: sum(1 for f in F if f.classification == CLASS_MANUAL_REVIEW),
        CLASS_NOT_RUN: sum(1 for f in F if f.classification == CLASS_NOT_RUN),
    }
    res["structure"] = {"present": tree.has_root and n_elements > 0, **ssum, "role_map": tree.rolemap, "truncated": tree.truncated,
                        "nodes": [{k: v for k, v in n.items() if not k.startswith("_")} for n in nodes], "roots": tree.root_children}
    res["checked"] = ["File integrity (qpdf)", "Encryption/permissions", "Tag presence and MarkInfo", "Tag tree walk with RoleMap resolution", "ParentTree vs. marked content, both directions",
                      "Parent/child rules for lists, tables, inline/block", "Headings (levels, mixing, emptiness)", "Figure/Formula alternative text", "Table headers and regularity",
                      "Title (Info + XMP), DisplayDocTitle, language", "Fonts: embedding and Unicode mapping", "Links, form fields, annotations and tab order",
                      "Untagged / double-marked content", "Reading-order heuristic", "Scanned-page detection per page", "Bookmarks for long documents"]
    res["analysis_seconds"] = round(time.time() - t0, 2)
    res["encrypted_note"] = "Opened with an empty password; permissions apply." if encrypted else ""
    if pdoc is not None:
        try:
            pdoc.close()
        except Exception:
            pass
    return res
