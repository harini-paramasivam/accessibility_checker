"""Per-page detection of scanned, image-only, mixed and already-OCR'd pages.

Every page is classified on its own. A classification is "definitive" only when
the evidence leaves no ambiguity (an image covers essentially the whole page and
the page has no text objects at all); everything else is labelled "heuristic".
"""
from __future__ import annotations
import re
import unicodedata
from .pdfium_content import T_TEXT, T_IMAGE, T_PATH

MIN_TEXT = 25            # alphanumeric chars needed to call a page "has text"
GRID = 64

LABELS = {
    "scan_no_text": "Scanned page, no text",
    "scan_insufficient_text": "Image page, almost no text",
    "scan_ocr_layer": "Scan with OCR text layer",
    "mixed_scan_native": "Image + native text",
    "image_heavy": "Image-heavy, has text",
    "native_text": "Native text",
    "unreliable_text": "Text layer looks unreliable",
    "graphics_sparse": "Graphics, little text",
    "blank": "Blank page",
}


def text_quality(text: str) -> dict:
    n = len(text)
    if n == 0:
        return {"chars": 0, "alnum": 0, "weird_ratio": 0.0, "avg_word": 0.0, "suspicious": False, "reasons": []}
    alnum = sum(1 for c in text if c.isalnum())
    weird = 0
    for c in text:
        cat = unicodedata.category(c)
        if c == "\ufffd" or cat in ("Co", "Cn", "Cs") or (cat == "Cc" and c not in "\n\r\t"):
            weird += 1
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    avg = sum(len(w) for w in words) / len(words) if words else 0.0
    reasons = []
    wr = weird / n
    if wr > 0.05:
        reasons.append(f"{weird} undecodable/private-use characters ({wr:.0%} of the text)")
    if n >= 80 and alnum / n < 0.35:
        reasons.append(f"only {alnum / n:.0%} of characters are letters or digits")
    if n >= 80 and len(words) > 10 and avg < 2.0:
        reasons.append(f"average word length {avg:.1f} suggests broken spacing")
    return {"chars": n, "alnum": alnum, "weird_ratio": round(wr, 4), "avg_word": round(avg, 2), "suspicious": bool(reasons), "reasons": reasons}


def _union_cov(boxes, crop):
    x0, y0, x1, y1 = crop
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return 0.0
    cells = bytearray(GRID * GRID)
    for l, b, r, t in boxes:
        l, r = max(l, x0), min(r, x1)
        b, t = max(b, y0), min(t, y1)
        if r <= l or t <= b:
            continue
        i0, i1 = int((l - x0) / w * GRID), min(GRID - 1, int((r - x0) / w * GRID - 1e-9))
        j0, j1 = int((b - y0) / h * GRID), min(GRID - 1, int((t - y0) / h * GRID - 1e-9))
        for j in range(j0, j1 + 1):
            base = j * GRID
            for i in range(i0, i1 + 1):
                cells[base + i] = 1
    return sum(cells) / (GRID * GRID)


def classify_page(index, objs, info, full_text: str) -> dict:
    crop = info["crop"]
    area = max(1.0, (crop[2] - crop[0]) * (crop[3] - crop[1]))
    imgs = [o for o in objs if o.kind == T_IMAGE]
    texts = [o for o in objs if o.kind == T_TEXT]
    paths = [o for o in objs if o.kind == T_PATH]
    inv_txt = "".join(o.text for o in texts if o.render_mode == 3)
    inv_alnum = sum(1 for c in inv_txt if c.isalnum())
    total_alnum = sum(1 for c in full_text if c.isalnum())
    vis_alnum = max(0, total_alnum - inv_alnum)
    img_boxes = [o.bbox for o in imgs]
    cov = _union_cov(img_boxes, crop)
    max_single = max([((min(b[2], crop[2]) - max(b[0], crop[0])) * (min(b[3], crop[3]) - max(b[1], crop[1]))) / area
                      for b in img_boxes if min(b[2], crop[2]) > max(b[0], crop[0]) and min(b[3], crop[3]) > max(b[1], crop[1])] or [0.0])
    dpis = []
    for o in imgs:
        wpt = o.bbox[2] - o.bbox[0]
        if o.img_w and wpt > 0 and wpt * (o.bbox[3] - o.bbox[1]) / area > 0.2:
            dpis.append(o.img_w / (wpt / 72.0))
    q = text_quality(full_text)
    reasons, conf = [], "heuristic"
    if not objs and total_alnum == 0:
        cls = "blank"
        reasons.append("No page objects and no text.")
        conf = "definitive"
    elif max_single >= 0.5 and inv_alnum >= MIN_TEXT and vis_alnum < MIN_TEXT:
        cls = "scan_ocr_layer"
        reasons.append(f"An image covers {max_single:.0%} of the page and {inv_alnum} characters of invisible text (render mode 3) sit on top: an existing OCR layer.")
        if q["suspicious"]:
            cls = "unreliable_text"
            reasons += ["OCR layer looks unreliable: " + r for r in q["reasons"]]
    elif max_single >= 0.5 and total_alnum < MIN_TEXT:
        if max_single >= 0.9 and not texts:
            cls = "scan_no_text"
            conf = "definitive"
            reasons.append(f"One image covers {max_single:.0%} of the page and the page has no text objects at all.")
        elif total_alnum < 10:
            cls = "scan_no_text"
            reasons.append(f"An image covers {max_single:.0%} of the page; only {total_alnum} text characters were extracted.")
        else:
            cls = "scan_insufficient_text"
            reasons.append(f"An image covers {max_single:.0%} of the page but only {total_alnum} characters of text exist.")
    elif max_single >= 0.5 and vis_alnum >= MIN_TEXT:
        cls = "mixed_scan_native"
        reasons.append(f"An image covers {max_single:.0%} of the page and {vis_alnum} characters of native text exist. Text inside the image (if any) is not searchable.")
    elif q["suspicious"] and total_alnum >= 1:
        cls = "unreliable_text"
        reasons += q["reasons"]
    elif cov >= 0.3 and total_alnum >= MIN_TEXT:
        cls = "image_heavy"
        reasons.append(f"Images cover {cov:.0%} of the page; {total_alnum} characters of native text exist.")
    elif total_alnum >= MIN_TEXT:
        cls = "native_text"
        reasons.append(f"{total_alnum} characters of native text.")
    else:
        cls = "graphics_sparse"
        reasons.append(f"{total_alnum} characters of text, {len(paths)} vector objects, images cover {cov:.0%}.")
    if cls == "unreliable_text" and conf == "definitive":
        conf = "heuristic"
    candidate = cls in ("scan_no_text", "scan_insufficient_text", "unreliable_text") or (cls == "unreliable_text")
    optional = cls in ("mixed_scan_native", "image_heavy", "scan_ocr_layer", "graphics_sparse") and bool(imgs)
    if cls == "scan_ocr_layer":
        reasons.append("Already searchable: re-running OCR would duplicate text unless overlapping text is skipped.")
    return {
        "page": index, "width": round(info["width"], 1), "height": round(info["height"], 1), "rotation": info["rotation"],
        "class": cls, "label": LABELS[cls], "confidence": conf, "ocr_candidate": candidate, "ocr_optional": optional,
        "visible_chars": vis_alnum, "invisible_chars": inv_alnum, "text_objects": len(texts), "image_count": len(imgs),
        "path_count": len(paths), "image_coverage": round(cov, 3), "largest_image_coverage": round(max_single, 3),
        "min_dpi": round(min(dpis)) if dpis else None, "max_dpi": round(max(dpis)) if dpis else None,
        "text_quality": q, "reasons": reasons,
    }


def summarize(pages: list[dict]) -> dict:
    counts = {}
    for p in pages:
        counts[p["class"]] = counts.get(p["class"], 0) + 1
    cand = [p["page"] for p in pages if p["ocr_candidate"]]
    content_pages = [p for p in pages if p["class"] != "blank"]
    if cand and len(cand) == len(content_pages):
        doc_class = "image_only"
    elif cand:
        doc_class = "mixed"
    elif counts.get("scan_ocr_layer"):
        doc_class = "has_ocr_layer"
    else:
        doc_class = "native"
    return {"counts": counts, "candidate_pages": cand, "doc_class": doc_class,
            "ocr_layer_pages": [p["page"] for p in pages if p["class"] == "scan_ocr_layer"],
            "mixed_pages": [p["page"] for p in pages if p["class"] == "mixed_scan_native"],
            "optional_pages": [p["page"] for p in pages if p["ocr_optional"] and not p["ocr_candidate"]],
            "note": "Page classes are heuristics from text extraction, image coverage and text render modes. Only classes marked 'definitive' leave no ambiguity."}
