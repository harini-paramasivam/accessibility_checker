"""Safe, explicit remediation. Every action writes a NEW file; the source is never modified.

Deterministic metadata fixes run directly. Anything that needs human knowledge (title text,
language, alt text, link/field descriptions) needs an explicit value. Structural edits
(changing a tag type, renumbering headings) need confirm=true. Nothing invents tags.
"""
from __future__ import annotations
import re
import pikepdf
from pikepdf import Array, Dictionary, Name, String
from .structure import STD_17, _name

LANG_RE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*")
NEEDS_CONFIRM = {"set_struct_type", "set_heading_levels"}


class RemediationError(Exception):
    pass


def _obj(pdf, ref):
    try:
        n, g = (int(x) for x in str(ref).split())
        o = pdf.get_object((n, g))
        if o is None or not isinstance(o, (Dictionary, pikepdf.Stream)):
            raise ValueError
        return o
    except Exception:
        raise RemediationError(f"Object '{ref}' does not exist in this version of the file")


def _is_struct(o):
    return isinstance(o, Dictionary) and "/S" in o and ("/P" in o or "/K" in o)


def apply_actions(src, dst, actions, confirm=False):
    applied, skipped = [], []
    with pikepdf.open(str(src)) as pdf:
        if pdf.is_encrypted:
            raise RemediationError("Encrypted PDFs cannot be modified. Remove the encryption in the source application first.")
        root = pdf.Root
        for act in actions:
            a = act.get("action")
            p = act.get("params") or {}
            if a in NEEDS_CONFIRM and not (confirm or act.get("confirm")):
                skipped.append({"action": a, "reason": "Structural change: needs explicit confirmation"})
                continue
            try:
                msg = _do(pdf, root, a, p)
                applied.append({"action": a, "result": msg})
            except RemediationError as e:
                skipped.append({"action": a, "reason": str(e)})
        if not applied:
            raise RemediationError("; ".join(s["reason"] for s in skipped) or "No actions were applied")
        pdf.save(str(dst), object_stream_mode=pikepdf.ObjectStreamMode.preserve)
    return applied, skipped


def _do(pdf, root, a, p):
    if a == "set_title":
        t = (p.get("value") or "").strip()
        if not t or len(t) > 300:
            raise RemediationError("Title must be 1-300 characters")
        pdf.docinfo["/Title"] = t
        with pdf.open_metadata(set_pikepdf_as_editor=False) as m:
            m["dc:title"] = t
        return f"Title set to \"{t}\" (Info and XMP)"
    if a == "sync_xmp_title":
        t = str(pdf.docinfo.get("/Title", "")).strip()
        if not t:
            raise RemediationError("There is no Info title to copy")
        with pdf.open_metadata(set_pikepdf_as_editor=False) as m:
            m["dc:title"] = t
        return "XMP dc:title written from the Info title"
    if a == "set_lang":
        l = (p.get("value") or "").strip()
        if not LANG_RE.fullmatch(l):
            raise RemediationError("Language must be a BCP 47 tag such as en-US or ta-IN")
        root.Lang = String(l)
        return f"Document language set to {l}"
    if a == "set_display_doc_title":
        vp = root.get("/ViewerPreferences")
        if not isinstance(vp, Dictionary):
            root.ViewerPreferences = Dictionary()
        root.ViewerPreferences.DisplayDocTitle = True
        return "DisplayDocTitle turned on"
    if a == "set_tabs_structure":
        n = 0
        for pg in pdf.pages:
            if isinstance(pg.obj.get("/Annots"), Array) and len(pg.obj.Annots) and _name(pg.obj.get("/Tabs")) != "S":
                pg.obj.Tabs = Name.S
                n += 1
        if not n:
            raise RemediationError("No page needed a tab-order change")
        return f"Tab order set to document structure on {n} page(s)"
    if a == "set_mark_info":
        st = root.get("/StructTreeRoot")
        if st is None or not len(st.get("/K", Array()) if isinstance(st.get("/K"), Array) else [st.get("/K")] if st.get("/K") is not None else []):
            raise RemediationError("There is no structure tree, so the file cannot be flagged as tagged")
        root.MarkInfo = Dictionary(Marked=True)
        return "MarkInfo /Marked set to true"
    if a == "clean_pt_orphans":
        st = root.get("/StructTreeRoot")
        if st is None or "/ParentTree" not in st:
            raise RemediationError("The document has no /ParentTree to clean")
        pt = pikepdf.NumberTree(st.ParentTree)
        reachable = set()

        def _walk_tree(elem):
            if not isinstance(elem, Dictionary):
                return
            og = getattr(elem, "objgen", None)
            if og and og != (0, 0):
                reachable.add(og)
            kids = elem.get("/K")
            if isinstance(kids, Array):
                for k in kids:
                    _walk_tree(k)
            elif isinstance(kids, Dictionary):
                _walk_tree(kids)

        _walk_tree(st)

        cleaned = 0
        keys_to_delete = []
        for k, v in list(pt.items()):
            if isinstance(v, Array):
                new_arr = Array()
                modified = False
                for item in v:
                    og = getattr(item, "objgen", None)
                    if isinstance(item, Dictionary) and "/S" in item and og and og != (0, 0) and og not in reachable:
                        new_arr.append(pikepdf.Name.null)
                        cleaned += 1
                        modified = True
                    else:
                        new_arr.append(item)
                if modified:
                    pt[k] = new_arr
            elif isinstance(v, Dictionary) and "/S" in v:
                og = getattr(v, "objgen", None)
                if og and og != (0, 0) and og not in reachable:
                    keys_to_delete.append(k)
                    cleaned += 1

        for k in keys_to_delete:
            del pt[k]

        if not cleaned:
            return "No orphaned ParentTree references found"
        return f"Cleaned {cleaned} orphaned reference(s) from /ParentTree"
    if a == "set_figure_alt":
        items = p.get("items") or []
        n = 0
        for it in items:
            txt = (it.get("text") or "").strip()
            if not txt:
                continue
            o = _obj(pdf, it.get("obj"))
            if not _is_struct(o):
                raise RemediationError(f"Object {it.get('obj')} is not a structure element")
            o.Alt = String(txt)
            n += 1
        if not n:
            raise RemediationError("No alternative text was provided")
        return f"Alternative text written for {n} element(s)"
    if a == "set_link_contents":
        n = 0
        for it in p.get("items") or []:
            txt = (it.get("text") or "").strip()
            if txt:
                o = _obj(pdf, it.get("obj"))
                if _name(o.get("/Subtype")) != "Link":
                    raise RemediationError(f"Object {it.get('obj')} is not a link annotation")
                o.Contents = String(txt)
                n += 1
        if not n:
            raise RemediationError("No link descriptions were provided")
        return f"Description written for {n} link(s)"
    if a == "set_widget_tooltip":
        n = 0
        for it in p.get("items") or []:
            txt = (it.get("text") or "").strip()
            if txt:
                _obj(pdf, it.get("obj")).TU = String(txt)
                n += 1
        if not n:
            raise RemediationError("No field descriptions were provided")
        return f"Description (TU) written for {n} field(s)"
    if a == "set_struct_type":
        n = 0
        for it in p.get("items") or []:
            t = it.get("type")
            if t not in STD_17:
                raise RemediationError(f"'{t}' is not a standard structure type")
            o = _obj(pdf, it.get("obj"))
            if not _is_struct(o):
                raise RemediationError(f"Object {it.get('obj')} is not a structure element")
            o.S = Name("/" + t)
            n += 1
        return f"Structure type changed on {n} element(s)"
    if a == "set_heading_levels":
        lv = p.get("levels") or {}
        n = 0
        for ref, level in lv.items():
            if level not in (1, 2, 3, 4, 5, 6):
                raise RemediationError("Heading level must be 1-6")
            o = _obj(pdf, ref)
            if not _is_struct(o) or not re.fullmatch(r"H[1-6]", _name(o.get("/S"))):
                raise RemediationError(f"Object {ref} is not a numbered heading (it may have changed since the analysis)")
            o.S = Name(f"/H{level}")
            n += 1
        if not n:
            raise RemediationError("No headings to change")
        return f"{n} heading level(s) renumbered"
    raise RemediationError(f"Unknown action '{a}'")
