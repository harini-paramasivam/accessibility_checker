"""Page-content inspection through pdfium (pypdfium2).

Gives, per page: every page object with its bounds (PDF user space), type,
marked-content IDs, artifact flag, text and render mode, image metadata.
This is what ties the logical structure tree (MCIDs) to real page content.
"""
from __future__ import annotations
import ctypes
import threading
from dataclasses import dataclass, field
import pypdfium2 as pdfium
import pypdfium2.raw as raw

LOCK = threading.RLock()   # pdfium is not thread-safe: every pdfium call goes through this lock

T_TEXT, T_PATH, T_IMAGE, T_SHADING, T_FORM = 1, 2, 3, 4, 5


@dataclass
class PObj:
    kind: int
    bbox: tuple            # (l, b, r, t) PDF user space
    mcids: list = field(default_factory=list)   # all MCIDs on marks (outer->inner)
    artifact: bool = False
    tagged_marks: bool = False
    text: str = ""
    render_mode: int = 0
    img_w: int = 0
    img_h: int = 0
    in_form: bool = False


def _mark_name(mark) -> str:
    n = ctypes.c_ulong(0)
    raw.FPDFPageObjMark_GetName(mark, None, 0, ctypes.byref(n))
    if not n.value:
        return ""
    buf = (ctypes.c_ushort * (n.value // 2 + 1))()
    raw.FPDFPageObjMark_GetName(mark, buf, n.value, ctypes.byref(n))
    return ctypes.string_at(buf, max(0, n.value - 2)).decode("utf-16-le", "replace")


def _marks(obj_raw):
    """Return (mcids, is_artifact, has_marks)."""
    mcids, artifact = [], False
    cnt = raw.FPDFPageObj_CountMarks(obj_raw)
    for i in range(cnt):
        m = raw.FPDFPageObj_GetMark(obj_raw, i)
        if not m:
            continue
        name = _mark_name(m)
        if name == "Artifact":
            artifact = True
        iv = ctypes.c_int(0)
        if raw.FPDFPageObjMark_GetParamIntValue(m, b"MCID", ctypes.byref(iv)):
            mcids.append(iv.value)
    return mcids, artifact, cnt > 0


def _text_of(obj_raw, textpage_raw) -> str:
    n = raw.FPDFTextObj_GetText(obj_raw, textpage_raw, None, 0)
    if n <= 2:
        return ""
    buf = (ctypes.c_ushort * (n // 2 + 1))()
    raw.FPDFTextObj_GetText(obj_raw, textpage_raw, buf, n)
    return ctypes.string_at(buf, n - 2).decode("utf-16-le", "replace")


def _bounds(obj_raw):
    l, b, r, t = (ctypes.c_float() for _ in range(4))
    if raw.FPDFPageObj_GetBounds(obj_raw, l, b, r, t):
        return (l.value, b.value, r.value, t.value)
    return (0.0, 0.0, 0.0, 0.0)


def _walk(obj_raw, textpage_raw, out, inherited_mcids, inherited_art, in_form, depth=0):
    kind = raw.FPDFPageObj_GetType(obj_raw)
    mcids, art, has = _marks(obj_raw)
    mcids = inherited_mcids + mcids
    art = art or inherited_art
    if kind == T_FORM and depth < 6:
        cnt = raw.FPDFFormObj_CountObjects(obj_raw)
        for i in range(cnt):
            sub = raw.FPDFFormObj_GetObject(obj_raw, i)
            if sub:
                _walk(sub, textpage_raw, out, mcids, art, True, depth + 1)
        return
    p = PObj(kind=kind, bbox=_bounds(obj_raw), mcids=mcids, artifact=art,
             tagged_marks=bool(mcids), in_form=in_form)
    if kind == T_TEXT:
        p.render_mode = raw.FPDFTextObj_GetTextRenderMode(obj_raw)
        if not in_form:
            p.text = _text_of(obj_raw, textpage_raw)
    elif kind == T_IMAGE:
        meta = raw.FPDF_IMAGEOBJ_METADATA()
        if raw.FPDFImageObj_GetImageMetadata(obj_raw, None, meta):
            p.img_w, p.img_h = meta.width, meta.height
    out.append(p)


def page_objects(page: pdfium.PdfPage):
    """List of PObj for a pdfium page. Text of objects inside forms is not read."""
    tp = page.get_textpage()
    out: list[PObj] = []
    try:
        for o in page.get_objects(max_depth=0):
            _walk(o.raw, tp.raw, out, [], False, False)
    finally:
        tp.close()
    return out


def page_display_info(page: pdfium.PdfPage):
    """Geometry needed to map user space <-> displayed pixels."""
    mb = page.get_mediabox()
    cb = page.get_cropbox() or mb
    rot = page.get_rotation() % 360
    x0, y0, x1, y1 = cb
    cw, ch = x1 - x0, y1 - y0
    w, h = (cw, ch) if rot in (0, 180) else (ch, cw)
    return {"crop": (x0, y0, x1, y1), "rotation": rot, "width": w, "height": h}


def user_to_display(info, x, y):
    """PDF user-space point -> displayed point in pt from top-left."""
    x0, y0, x1, y1 = info["crop"]
    rot = info["rotation"]
    if rot == 0:
        return x - x0, y1 - y
    if rot == 90:
        return y - y0, x - x0
    if rot == 180:
        return x1 - x, y - y0
    return y1 - y, x1 - x  # 270


def display_to_user(info, u, v):
    """Displayed point (pt from top-left) -> PDF user space."""
    x0, y0, x1, y1 = info["crop"]
    rot = info["rotation"]
    if rot == 0:
        return x0 + u, y1 - v
    if rot == 90:
        return x0 + v, y0 + u
    if rot == 180:
        return x1 - u, y0 + v
    return x0 + (y1 - y0 - v), y0 + (x1 - x0 - u)


def extract_page_text_layout(page: pdfium.PdfPage, ocr_words: list | None = None) -> dict:
    """Extract full text, lines, and words with normalized (0.0 - 1.0) bounding boxes.
    Enables precise, aligned text selection and copying in PDF viewers.
    """
    info = page_display_info(page)
    pw = max(1.0, float(info["width"]))
    ph = max(1.0, float(info["height"]))

    tp = page.get_textpage()
    try:
        full_text = tp.get_text_range() or ""
        n_chars = tp.count_chars()
        if n_chars <= 0:
            return {
                "text": "",
                "words": [],
                "lines": [],
                "display_width": pw,
                "display_height": ph,
                "rotation": info["rotation"],
            }

        words = []
        lines = []

        cur_word_chars: list[str] = []
        cur_word_boxes: list[tuple[float, float, float, float]] = []
        cur_line_words: list[dict] = []

        def flush_word():
            nonlocal cur_word_chars, cur_word_boxes
            if not cur_word_chars:
                return
            wtext = "".join(cur_word_chars)
            u_mins = [b[0] for b in cur_word_boxes]
            u_maxs = [b[1] for b in cur_word_boxes]
            v_mins = [b[2] for b in cur_word_boxes]
            v_maxs = [b[3] for b in cur_word_boxes]

            w_l = min(u_mins) / pw
            w_r = max(u_maxs) / pw
            w_t = min(v_mins) / ph
            w_b = max(v_maxs) / ph

            l_clamped = max(0.0, min(1.0, w_l))
            t_clamped = max(0.0, min(1.0, w_t))
            w_clamped = max(0.0005, min(1.0, w_r - w_l))
            h_clamped = max(0.0005, min(1.0, w_b - w_t))

            # Match OCR word confidence if available
            conf = None
            if ocr_words:
                # Find closest word in ocr_words
                for ow in ocr_words:
                    if ow.get("text") == wtext:
                        conf = ow.get("conf")
                        break

            w_obj = {
                "text": wtext,
                "l": round(l_clamped, 4),
                "t": round(t_clamped, 4),
                "w": round(w_clamped, 4),
                "h": round(h_clamped, 4),
                "conf": conf,
                "low_conf": bool(conf is not None and conf < 60),
            }
            words.append(w_obj)
            cur_line_words.append(w_obj)
            cur_word_chars = []
            cur_word_boxes = []

        def flush_line():
            nonlocal cur_line_words
            flush_word()
            if not cur_line_words:
                return
            l_text = " ".join(w["text"] for w in cur_line_words)
            ll = min(w["l"] for w in cur_line_words)
            lr = max(w["l"] + w["w"] for w in cur_line_words)
            lt = min(w["t"] for w in cur_line_words)
            lb = max(w["t"] + w["h"] for w in cur_line_words)
            lines.append({
                "text": l_text,
                "l": round(ll, 4),
                "t": round(lt, 4),
                "w": round(lr - ll, 4),
                "h": round(lb - lt, 4),
                "words": list(cur_line_words),
            })
            cur_line_words = []

        for i in range(n_chars):
            ch = tp.get_text_range(i, 1)
            if ch in ("\r", "\n"):
                flush_line()
                continue
            if ch in (" ", "\t", "\u00a0"):
                flush_word()
                continue

            cb = tp.get_charbox(i)
            u0, v0 = user_to_display(info, cb[0], cb[3])
            u1, v1 = user_to_display(info, cb[2], cb[1])
            umin, umax = min(u0, u1), max(u0, u1)
            vmin, vmax = min(v0, v1), max(v0, v1)

            cur_word_chars.append(ch)
            cur_word_boxes.append((umin, umax, vmin, vmax))

        flush_line()
        return {
            "text": full_text,
            "words": words,
            "lines": lines,
            "display_width": pw,
            "display_height": ph,
            "rotation": info["rotation"],
        }
    finally:
        tp.close()
