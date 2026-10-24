"""PDF logical-structure inspection and validation (real object graph, no regexes).

Walks /StructTreeRoot -> /K recursively (iteratively, cycle-safe), resolves
/RoleMap, cross-checks /ParentTree in both directions against marked content
that pdfium reports on the pages, then applies parent/child and semantic rules.
"""
from __future__ import annotations
import re
from collections import defaultdict
import pikepdf
from pikepdf import Array, Dictionary, Name, NumberTree

from .findings import (Finding, ERROR, WARNING, MANUAL, ua, wcag, mh, iso32000, iso32000_2,
                       CLASS_STRUCTURAL_ERROR, CLASS_ACCESSIBILITY_FAILURE, CLASS_SEMANTIC_ISSUE,
                       CLASS_MANUAL_REVIEW, CLASS_NOT_RUN, VERIF_CONFIRMED, VERIF_SUSPECTED,
                       VERIF_MANUAL, VERIF_NOT_RUN)

STD_17 = set("""Document Part Art Sect Div BlockQuote Caption TOC TOCI Index NonStruct Private
P H H1 H2 H3 H4 H5 H6 L LI Lbl LBody Table TR TH TD THead TBody TFoot
Span Quote Note Reference BibEntry Code Link Annot Ruby RB RT RP Warichu WT WP
Figure Formula Form""".split())
STD_20_EXTRA = set("DocumentFragment Aside Title FENote Sub Em Strong Artifact".split())
INLINE = {"Span", "Quote", "Code", "Reference", "Link", "Annot", "Note", "BibEntry"}
BLOCKISH = {"P", "H", "H1", "H2", "H3", "H4", "H5", "H6", "L", "Table", "Div", "Sect"}
EMPTY_OK = {"TD", "TH", "Lbl", "Figure", "Formula", "Form", "Link", "Annot", "NonStruct", "Private", "Span"}
TEXT_CONTAINERS = {"P", "H", "H1", "H2", "H3", "H4", "H5", "H6", "LBody", "Caption", "Quote", "BlockQuote"}
GENERIC_ALT = re.compile(r"^(image|figure|picture|photo|graphic|img|logo|chart|diagram|icon|untitled|alt text|image\d*|figure\d*|img[_ -]?\d+)\.?$", re.I)
FILENAME_ALT = re.compile(r"\.(jpe?g|png|gif|tiff?|bmp|svg|webp|emf|wmf)\s*$", re.I)


def _name(o):
    try:
        s = str(o)
        return s[1:] if s.startswith("/") else s
    except Exception:
        return ""


def _text(o):
    if o is None:
        return ""
    try:
        return str(o)
    except Exception:
        return ""


def _og(o):
    try:
        g = o.objgen
        return g if g != (0, 0) else None
    except Exception:
        return None


class Tree:
    def __init__(self):
        self.nodes: list[dict] = []
        self.by_og: dict[tuple, int] = {}
        self.root_children: list[int] = []
        self.rolemap: dict[str, str] = {}
        self.truncated = False
        self.issues: dict[str, list] = defaultdict(list)   # structural integrity evidence
        self.mcid_owner: dict[tuple, list] = defaultdict(list)  # (page, mcid) -> [node ids]
        self.objr: list[tuple] = []   # (node id, page, annot objgen or None)
        self.has_root = False
        self.root_dict = None


def resolve_role(role, rolemap, pdf20):
    """Return (std_type|None, status)."""
    if role in STD_17 and role not in rolemap:
        return role, "standard"
    if role in STD_20_EXTRA and role not in rolemap:
        return role, ("standard" if pdf20 else "pdf20-only")
    seen, cur, hops = {role}, role, 0
    while cur in rolemap and hops < 50:
        nxt = rolemap[cur]
        if nxt in seen:
            return None, "circular"
        seen.add(nxt)
        cur = nxt
        hops += 1
    if cur in STD_17 or (pdf20 and cur in STD_20_EXTRA):
        return cur, ("remapped-standard" if role in STD_17 else "mapped")
    return None, "unmapped"


def _attr(d, key, owner=None):
    try:
        A = d.get("/A")
        if A is None:
            return None
        items = list(A) if isinstance(A, Array) else [A]
        for it in items:
            if isinstance(it, Dictionary) and (owner is None or _name(it.get("/O")) == owner) and key in it:
                return it[key]
    except Exception:
        return None
    return None


def build_tree(pdf, page_index, cfg, pdf20=False) -> Tree:
    t = Tree()
    root = pdf.Root.get("/StructTreeRoot")
    if root is None or not isinstance(root, Dictionary):
        return t
    t.has_root = True
    t.root_dict = root
    rm = root.get("/RoleMap")
    if isinstance(rm, Dictionary):
        for k in rm.keys():
            t.rolemap[_name(k)] = _name(rm[k])

    stack = []

    def kids_of(K):
        if K is None:
            return []
        if isinstance(K, Array):
            return list(K)
        return [K]

    for ch in reversed(kids_of(root.get("/K"))):
        stack.append((ch, None, 0, None))
    while stack:
        item, parent, depth, page = stack.pop()
        if isinstance(item, (int,)) and not isinstance(item, bool):
            continue  # MCID directly under root: handled as an issue below
        if not isinstance(item, Dictionary):
            t.issues["bad-k-entry"].append((parent, repr(item)[:40]))
            continue
        typ = _name(item.get("/Type"))
        if typ in ("MCR", "OBJR") or "/S" not in item:
            if parent is None:
                t.issues["root-content"].append(typ or "dictionary without /S")
            continue
        og = _og(item)
        if og is not None and og in t.by_og:
            other = t.by_og[og]
            anc, p = False, parent
            while p is not None:
                if p == other:
                    anc = True
                    break
                p = t.nodes[p]["p"]
            t.issues["cycle" if anc else "duplicate-ref"].append((parent, other))
            continue
        if len(t.nodes) >= cfg.max_struct_nodes:
            t.truncated = True
            break
        nid = len(t.nodes)
        raw = _name(item.get("/S"))
        std, status = resolve_role(raw, t.rolemap, pdf20)
        pg = item.get("/Pg")
        pgi = page_index.get(_og(pg)) if pg is not None else None
        eff_page = pgi if pgi is not None else page
        alt = _text(item.get("/Alt")) if "/Alt" in item else None
        act = _text(item.get("/ActualText")) if "/ActualText" in item else None
        node = {"id": nid, "obj": f"{og[0]} {og[1]}" if og else None, "s": raw, "t": std or raw, "status": status,
                "p": parent, "d": depth, "pg": eff_page, "k": [], "mc": [], "objr": 0,
                "alt": alt, "actual": act, "lang": _text(item.get("/Lang")) if "/Lang" in item else None,
                "text": "", "bbox": None, "bpg": None, "issues": [], "_d": item}
        P = item.get("/P")
        if P is not None and parent is not None:
            pog = _og(P)
            if pog is not None and t.nodes[parent]["obj"] != f"{pog[0]} {pog[1]}":
                t.issues["p-mismatch"].append((nid, parent))
        elif P is None and parent is not None:
            t.issues["p-missing"].append((nid, parent))
        t.nodes.append(node)
        if og is not None:
            t.by_og[og] = nid
        if parent is None:
            t.root_children.append(nid)
        else:
            t.nodes[parent]["k"].append(nid)
        K = item.get("/K")
        children = []
        for e in kids_of(K):
            if isinstance(e, bool):
                continue
            if isinstance(e, int):
                node["mc"].append((eff_page, int(e)))
                t.mcid_owner[(eff_page, int(e))].append(nid)
            elif isinstance(e, Dictionary):
                et = _name(e.get("/Type"))
                if et == "MCR":
                    epg = e.get("/Pg")
                    pidx = page_index.get(_og(epg)) if epg is not None else eff_page
                    m = e.get("/MCID")
                    if m is not None:
                        node["mc"].append((pidx, int(m)))
                        t.mcid_owner[(pidx, int(m))].append(nid)
                elif et == "OBJR":
                    node["objr"] += 1
                    epg = e.get("/Pg")
                    pidx = page_index.get(_og(epg)) if epg is not None else eff_page
                    ob = e.get("/Obj")
                    t.objr.append((nid, pidx, _og(ob) if ob is not None else None))
                elif "/S" in e:
                    children.append(e)
                else:
                    t.issues["bad-k-entry"].append((nid, "dictionary without /S or /Type"))
            else:
                t.issues["bad-k-entry"].append((nid, repr(e)[:40]))
        for ch in reversed(children):
            stack.append((ch, nid, depth + 1, eff_page))
    return t


# ----------------------------------------------------------------------------
def _num(n):
    return f"#{n['id']} {n['s']}" + (f" (p.{n['pg']})" if n["pg"] else "")


def validate(pdf, tree: Tree, page_content, page_index, pages_n, pdf_version) -> tuple[list[Finding], dict]:
    """Return (findings, summary). page_content[i] = list of PObj for page i+1."""
    F: list[Finding] = []
    N = tree.nodes
    pdf20 = pdf_version >= 2.0

    def add(code, sev, cat, title, ev=None, nodes=None, pages=None, refs=None, rec="", detail="", fix=None, count=None,
            classification="", verification="", impact=""):
        nodes = nodes or []
        pgs = list(pages or [])
        for n in nodes:
            if N[n]["pg"]:
                pgs.append(N[n]["pg"])
        obj_refs = [N[n]["obj"] for n in nodes if N[n].get("obj")]
        F.append(Finding(code=code, severity=sev, category=cat, title=title, detail=detail, evidence=ev or [],
                         pages=pgs, nodes=nodes, refs=refs or [], recommendation=rec, fix=fix,
                         count=count if count is not None else max(len(nodes), len(ev or []), 1),
                         classification=classification, verification=verification, impact=impact,
                         obj_refs=obj_refs))

    summary = {"elements": len(N), "types": {}, "truncated": tree.truncated, "headings": [], "tables": 0,
               "lists": 0, "figures": 0, "links": 0, "max_depth": 0}
    if not tree.has_root or not N:
        return F, summary

    # ---- per-page content lookups (mcid -> bbox/text) ----------------------
    content = {}
    forms_with_marks = set()
    for pi, objs in page_content.items():
        m = defaultdict(lambda: {"text": [], "bbox": None, "kinds": set()})
        for o in objs:
            if not o.mcids:
                continue
            if o.in_form:
                forms_with_marks.add(pi)
            key = o.mcids[-1]
            e = m[key]
            e["kinds"].add(o.kind)
            if o.text.strip():
                e["text"].append(o.text)
            b = o.bbox
            e["bbox"] = b if e["bbox"] is None else (min(e["bbox"][0], b[0]), min(e["bbox"][1], b[1]), max(e["bbox"][2], b[2]), max(e["bbox"][3], b[3]))
        content[pi] = m

    # ---- node text / bbox, post-order ---------------------------------------
    order = list(range(len(N)))
    sub_text = {}
    for nid in reversed(order):
        n = N[nid]
        parts, bb = [], None
        bpg = None
        for pg, mc in n["mc"]:
            e = content.get(pg, {}).get(mc)
            if e:
                parts.extend(e["text"])
                if e["bbox"]:
                    if bpg is None:
                        bpg = pg
                    if pg == bpg:
                        b = e["bbox"]
                        bb = b if bb is None else (min(bb[0], b[0]), min(bb[1], b[1]), max(bb[2], b[2]), max(bb[3], b[3]))
        own = "".join(parts)
        txt = own
        for c in n["k"]:
            txt += " " + sub_text.get(c, "")
            cb, cpg = N[c]["bbox"], N[c]["bpg"]
            if cb and (bpg is None or cpg == bpg):
                bpg = cpg
                bb = cb if bb is None else (min(bb[0], cb[0]), min(bb[1], cb[1]), max(bb[2], cb[2]), max(bb[3], cb[3]))
        sub_text[nid] = txt if len(txt) < 600 else txt[:600]
        n["text"] = re.sub(r"\s+", " ", sub_text[nid]).strip()[:160]
        n["own_text"] = bool(own.strip())
        n["own"] = re.sub(r"\s+", " ", own).strip()[:160]
        n["bbox"] = [round(x, 1) for x in bb] if bb else None
        n["bpg"] = bpg
        n["has_content"] = bool(n["mc"]) or n["objr"] > 0 or any(N[c]["has_content"] if "has_content" in N[c] else False for c in n["k"])

    _reading_order(N, content, add)

    # ---- type distribution ---------------------------------------------------
    types = defaultdict(int)
    for n in N:
        types[n["t"]] += 1
        summary["max_depth"] = max(summary["max_depth"], n["d"])
    summary["types"] = dict(sorted(types.items(), key=lambda kv: -kv[1]))
    summary["tables"], summary["lists"], summary["figures"] = types.get("Table", 0), types.get("L", 0), types.get("Figure", 0)
    summary["links"] = types.get("Link", 0)

    # ---- integrity -------------------------------------------------------------
    if len(tree.root_children) != 1 or N[tree.root_children[0]]["t"] != "Document":
        add("STRUCT-ROOT", WARNING, "Structure", "Structure root is not a single Document element",
            [f"Top-level elements: {', '.join(N[c]['s'] for c in tree.root_children[:10])}"],
            nodes=tree.root_children[:20], refs=[ua("7.1"), mh("09", "Document structure")],
            rec="Wrap content in one Document element. PDF/UA readers expect a single root.")
    if tree.truncated:
        add("STRUCT-TRUNCATED", WARNING, "Structure", f"Tag tree larger than the inspection limit ({len(N)} elements read)",
            ["Remaining elements were not read; checks that need the full tree are incomplete."])
    if tree.issues.get("cycle"):
        add("STRUCT-CYCLE", ERROR, "Structure integrity", "Circular reference in the tag tree",
            [f"Element #{b} is referenced again inside its own subtree (from #{a})" for a, b in tree.issues["cycle"][:10]],
            nodes=[b for a, b in tree.issues["cycle"] if b is not None][:50], refs=[iso32000("14.7.2")],
            rec="The tag tree must be a tree. Re-export the document from its source or rebuild the tags.")
    if tree.issues.get("duplicate-ref"):
        add("STRUCT-DUPLICATE", ERROR, "Structure integrity", "Structure element referenced from more than one parent",
            [f"Element #{b} appears again under #{a}" for a, b in tree.issues["duplicate-ref"][:10]],
            nodes=[b for a, b in tree.issues["duplicate-ref"]][:50], refs=[iso32000("14.7.2")],
            rec="Each structure element must have exactly one parent.")
    if tree.issues.get("p-mismatch") or tree.issues.get("p-missing"):
        ev = [f"{_num(N[a])}: /P does not point to its actual parent #{b}" for a, b in tree.issues.get("p-mismatch", [])[:10]]
        ev += [f"{_num(N[a])}: /P entry missing" for a, b in tree.issues.get("p-missing", [])[:10]]
        nn = [a for a, _ in tree.issues.get("p-mismatch", [])] + [a for a, _ in tree.issues.get("p-missing", [])]
        add("STRUCT-PARENT-LINK", ERROR, "Structure integrity", "Structure elements with a wrong or missing /P parent link",
            ev, nodes=nn[:50], refs=[iso32000("14.7.2"), mh("09", "Document structure")],
            rec="Repair the parent links (most PDF editors do this on re-save) or re-export from the source.")
    if tree.issues.get("bad-k-entry") or tree.issues.get("root-content"):
        ev = [f"{p}: {d}" for p, d in tree.issues.get("bad-k-entry", [])[:10]] + [f"Under root: {x}" for x in tree.issues.get("root-content", [])[:5]]
        add("STRUCT-BAD-KIDS", ERROR, "Structure integrity", "Invalid entries in structure element /K arrays", ev,
            nodes=[a for a, _ in tree.issues.get("bad-k-entry", []) if a is not None][:50], refs=[iso32000("14.7.2")])

    # ---- role map / types ----------------------------------------------------
    circ = sorted({n["s"] for n in N if n["status"] == "circular"})
    if circ:
        add("ROLE-CIRCULAR", ERROR, "Structure types", "Circular role mapping",
            [f"{c} maps back to itself through /RoleMap" for c in circ], nodes=[n["id"] for n in N if n["status"] == "circular"][:50],
            refs=[mh("02", "Role mapping"), ua("7.1")], rec="Remove the loop in /RoleMap so every custom type ends in a standard type.")
    unm = sorted({n["s"] for n in N if n["status"] == "unmapped"})
    if unm:
        add("ROLE-UNMAPPED", ERROR, "Structure types", "Non-standard structure types without a mapping to a standard type",
            [f"Type /{u} is not a standard type and /RoleMap does not map it to one" for u in unm],
            nodes=[n["id"] for n in N if n["status"] == "unmapped"][:50], refs=[mh("02", "Role mapping"), ua("7.1")],
            rec="Add the missing /RoleMap entries or retag the elements with standard types.")
    p20 = sorted({n["s"] for n in N if n["status"] == "pdf20-only"})
    if p20:
        add("ROLE-PDF20", WARNING, "Structure types", "Structure types that only exist in PDF 2.0",
            [f"/{u} used in a PDF {pdf_version:.1f} document" for u in p20], nodes=[n["id"] for n in N if n["status"] == "pdf20-only"][:50],
            refs=[ua("7.1")], rec="PDF/UA-1 is based on PDF 1.7. Map these types to PDF 1.7 types.")
    rem = sorted({n["s"] for n in N if n["status"] == "remapped-standard"})
    if rem:
        add("ROLE-REMAPPED", WARNING, "Structure types", "Standard types remapped through /RoleMap",
            [f"/{r} -> /{tree.rolemap.get(r)}" for r in rem], nodes=[n["id"] for n in N if n["status"] == "remapped-standard"][:50],
            refs=[mh("02", "Role mapping")], rec="Standard types should keep their standard meaning; confirm the remapping is intended.")

    # ---- parent/child rules -----------------------------------------------------
    CH = {"L": ({"LI", "Caption"}, "L may contain only LI (and Caption)"),
          "LI": ({"Lbl", "LBody"}, "LI may contain only Lbl and LBody"),
          "Table": ({"TR", "THead", "TBody", "TFoot", "Caption"}, "Table may contain only TR, THead, TBody, TFoot, Caption"),
          "THead": ({"TR"}, "THead may contain only TR"), "TBody": ({"TR"}, "TBody may contain only TR"), "TFoot": ({"TR"}, "TFoot may contain only TR"),
          "TR": ({"TH", "TD"}, "TR may contain only TH and TD"),
          "TOC": ({"TOC", "TOCI", "Caption"}, "TOC may contain only TOC, TOCI, Caption")}
    PAR = {"LI": {"L"}, "Lbl": {"LI"}, "LBody": {"LI"}, "TR": {"Table", "THead", "TBody", "TFoot"}, "TH": {"TR"}, "TD": {"TR"},
           "THead": {"Table"}, "TBody": {"Table"}, "TFoot": {"Table"}, "TOCI": {"TOC"}}
    bad_list, bad_li, bad_tbl, bad_par, nested_l = [], [], [], [], []
    for n in N:
        t = n["t"]
        if t in CH:
            allowed, msg = CH[t]
            for c in n["k"]:
                ct = N[c]["t"]
                if ct not in allowed:
                    rec = (n["id"], c, f"{_num(n)} contains {N[c]['t']} - {msg}")
                    (bad_list if t in ("L", "LI") else bad_tbl).append(rec)
                elif t == "L" and ct == "L":
                    nested_l.append((n["id"], c))
        if t in PAR and n["p"] is not None and N[n["p"]]["t"] not in PAR[t]:
            bad_par.append((n["id"], f"{_num(n)} is inside {N[n['p']]['t']}, expected {'/'.join(sorted(PAR[t]))}"))
        elif t in PAR and n["p"] is None:
            bad_par.append((n["id"], f"{_num(n)} is at the top level"))
        if t == "LI" and n["k"] and not any(N[c]["t"] == "LBody" for c in n["k"]):
            bad_li.append(n["id"])
        if t in INLINE and any(N[c]["t"] in BLOCKISH for c in n["k"]):
            bad_par.append((n["id"], f"{_num(n)} (inline) contains block-level {', '.join(sorted({N[c]['t'] for c in n['k'] if N[c]['t'] in BLOCKISH}))}"))
    if bad_list:
        add("LIST-STRUCTURE", ERROR, "Lists", "List structure is invalid", [e[2] for e in bad_list[:15]],
            nodes=[e[1] for e in bad_list][:80], refs=[ua("7.6"), mh("16", "Lists"), wcag("1.3.1", "info-and-relationships")],
            rec="Use L > LI > (Lbl, LBody). Move stray content into an LBody.")
    if bad_li:
        add("LIST-NO-LBODY", ERROR, "Lists", "List items without an LBody", [f"{_num(N[i])} has no LBody child" for i in bad_li[:15]],
            nodes=bad_li[:80], refs=[ua("7.6"), mh("16", "Lists")], rec="Each LI needs an LBody that holds the item content.")
    if nested_l:
        add("LIST-NESTED", WARNING, "Lists", "Nested list placed directly inside a list", [f"{_num(N[a])} contains list #{b} directly" for a, b in nested_l[:10]],
            nodes=[b for _, b in nested_l][:50], refs=[ua("7.6")], rec="Place a nested L inside the LBody of its parent item.")
    if bad_tbl:
        add("TABLE-STRUCTURE", ERROR, "Tables", "Table structure is invalid", [e[2] for e in bad_tbl[:15]],
            nodes=[e[1] for e in bad_tbl][:80], refs=[ua("7.5"), mh("15", "Tables"), wcag("1.3.1", "info-and-relationships")],
            rec="Use Table > TR > (TH|TD), optionally grouped by THead/TBody/TFoot.")
    if bad_par:
        add("PARENT-CHILD", ERROR, "Structure", "Structure elements in an invalid parent", [e[1] for e in bad_par[:15]],
            nodes=[e[0] for e in bad_par][:80], refs=[ua("7.1"), iso32000("14.8.4")], rec="Move the element inside the container its type requires.")

    # ---- tables: headers and regularity ----------------------------------------
    tables_nohdr, tables_irreg, tables_nothscope = [], [], []

    def rows_of(tn):
        rows = []
        for c in tn["k"]:
            if N[c]["t"] == "TR":
                rows.append(c)
            elif N[c]["t"] in ("THead", "TBody", "TFoot"):
                rows += [r for r in N[c]["k"] if N[r]["t"] == "TR"]
        return rows

    def span(c, key):
        v = _attr(N[c]["_d"], key, "Table")
        try:
            return max(1, int(v)) if v is not None else 1
        except Exception:
            return 1

    for tn in [n for n in N if n["t"] == "Table"]:
        rows = rows_of(tn)
        cells_t = [c for r in rows for c in N[r]["k"] if N[c]["t"] in ("TH", "TD")]
        if not any(N[c]["t"] == "TH" for c in cells_t):
            tables_nohdr.append(tn["id"])
        elif len(rows) <= 60:
            has_scope = any(_attr(N[c]["_d"], "/Scope", "Table") is not None or "/Headers" in (_attr_dict(N[c]) or {}) for c in cells_t if N[c]["t"] == "TH")
            if not has_scope and any(N[c]["t"] == "TH" for r in rows[1:] for c in N[r]["k"]):
                tables_nothscope.append(tn["id"])
        if rows and len(rows) <= 200:
            occ, widths = defaultdict(set), []
            for ri, r in enumerate(rows):
                col = 0
                for c in N[r]["k"]:
                    if N[c]["t"] not in ("TH", "TD"):
                        continue
                    while col in occ[ri]:
                        col += 1
                    cs, rs = span(c, "/ColSpan"), span(c, "/RowSpan")
                    for dr in range(rs):
                        for dc in range(cs):
                            occ[ri + dr].add(col + dc)
                    col += cs
                widths.append(max(occ[ri]) + 1 if occ[ri] else 0)
            if len(set(widths)) > 1:
                tables_irreg.append((tn["id"], widths))
    summary["tables"] = types.get("Table", 0)
    if tables_nohdr:
        add("TABLE-NO-TH", ERROR, "Tables", "Tables without header cells (TH)", [f"{_num(N[i])}: no TH in any row" for i in tables_nohdr[:15]],
            nodes=tables_nohdr[:80], refs=[ua("7.5"), mh("15", "Tables"), wcag("1.3.1", "info-and-relationships")],
            rec="Tag header cells as TH. If this is a layout table it should not be tagged as a Table at all (manual decision).")
    if tables_irreg:
        add("TABLE-IRREGULAR", ERROR, "Tables", "Table rows have different numbers of columns",
            [f"{_num(N[i])}: row widths {w[:12]} after applying ColSpan/RowSpan" for i, w in tables_irreg[:10]],
            nodes=[i for i, _ in tables_irreg][:80], refs=[ua("7.5"), mh("15", "Tables")], rec="Check cell structure and ColSpan/RowSpan attributes so every row covers the same columns.")
    if tables_nothscope:
        add("TABLE-NO-SCOPE", WARNING, "Tables", "Tables with row/column headers but no Scope or Headers attributes",
            [f"{_num(N[i])}" for i in tables_nothscope[:15]], nodes=tables_nothscope[:80], refs=[ua("7.5"), wcag("1.3.1", "info-and-relationships")],
            rec="Set Scope (Row/Column/Both) on TH cells so assistive technology can associate data cells.")

    # ---- headings ---------------------------------------------------------------
    heads = [n for n in N if re.fullmatch(r"H[1-6]?", n["t"])]
    summary["headings"] = [{"id": n["id"], "level": int(n["t"][1:]) if len(n["t"]) > 1 else 0, "text": n["text"][:80], "page": n["bpg"] or n["pg"]} for n in heads[:400]]
    if heads:
        plain, numbered = [h for h in heads if h["t"] == "H"], [h for h in heads if h["t"] != "H"]
        if plain and numbered:
            add("HEAD-MIXED", ERROR, "Headings", "Both unnumbered (H) and numbered (H1-H6) headings are used",
                [f"{len(plain)} H and {len(numbered)} H1-H6 elements"], nodes=[h["id"] for h in plain][:60],
                refs=[ua("7.4"), mh("14", "Headings")], rec="Use one scheme. Numbered headings (H1-H6) are preferred.")
        if numbered:
            lv = [int(h["t"][1]) for h in numbered]
            if lv[0] != 1:
                add("HEAD-FIRST", WARNING, "Headings", f"The first heading is H{lv[0]}, not H1",
                    [f"{_num(numbered[0])}  \"{numbered[0]['text'][:60]}\""], nodes=[numbered[0]["id"]], refs=[ua("7.4"), wcag("1.3.1", "info-and-relationships"), wcag("2.4.6", "headings-and-labels")],
                    rec="Start the heading outline at H1.")
            skips, last = [], 0
            for h, l in zip(numbered, lv):
                if last and l > last + 1:
                    skips.append((h["id"], last, l))
                last = l
            if skips:
                fixmap = _renumber(numbered, lv)
                add("HEAD-SKIP", ERROR, "Headings", "Heading levels are skipped",
                    [f"{_num(N[i])}: H{a} is followed by H{b}  \"{N[i]['text'][:50]}\"" for i, a, b in skips[:12]],
                    nodes=[i for i, _, _ in skips][:60], refs=[ua("7.4"), mh("14", "Headings"), wcag("1.3.1", "info-and-relationships")],
                    rec="Do not jump over levels (for example H1 to H3). A proposed renumbering is available but changes the outline, so it needs your confirmation.",
                    fix={"action": "set_heading_levels", "label": "Apply proposed heading renumbering", "needs_confirmation": True,
                         "params": {"levels": fixmap}, "description": "Renumbers headings so no level is skipped (levels are only lowered, never raised past their parent+1)."} if fixmap else None)
        empt = [h["id"] for h in heads if not h["has_content"] or (not h["text"] and not h["alt"] and not h["actual"] and not any(c for c in h["k"]))]
        if empt:
            add("HEAD-EMPTY", WARNING, "Headings", "Empty headings", [f"{_num(N[i])} has no readable text" for i in empt[:15]],
                nodes=empt[:60], refs=[wcag("2.4.6", "headings-and-labels")], rec="Remove the heading tag or tag real heading text.")
    else:
        # heuristic: long documents without headings
        if pages_n >= 4:
            add("HEAD-NONE", WARNING, "Headings", f"No heading elements in a {pages_n}-page document", [],
                refs=[wcag("2.4.6", "headings-and-labels"), wcag("1.3.1", "info-and-relationships"), ua("7.4")],
                rec="Mark section titles as headings. A person must decide which text is a heading; this tool does not guess.")

    # ---- figures / formulas ------------------------------------------------------
    noalt, empty_alt, weak_alt = [], [], []
    for n in N:
        if n["t"] in ("Figure", "Formula"):
            a, ac = n["alt"], n["actual"]
            if a is None and not (ac or "").strip():
                if n["t"] == "Formula" or not _within_link_or_form(n, N):
                    noalt.append(n["id"])
            elif a is not None and not a.strip() and not (ac or "").strip():
                empty_alt.append(n["id"])
            elif a and (GENERIC_ALT.match(a.strip()) or FILENAME_ALT.search(a)):
                weak_alt.append(n["id"])
    if noalt:
        add("FIG-NO-ALT", ERROR, "Figures", f"{len(noalt)} Figure/Formula element(s) without alternative text",
            [f"{_num(N[i])} ({N[i]['t']}) on page {N[i]['pg'] or '?'} has no /Alt or /ActualText" for i in noalt[:15]],
            nodes=noalt[:100], refs=[ua("7.3"), mh("13", "Graphics"), wcag("1.1.1", "non-text-content")],
            rec="Write alternative text that conveys the purpose of the image. If it is purely decorative, mark it as an Artifact instead of tagging it.",
            fix={"action": "set_figure_alt", "label": "Add alternative text", "needs_input": True, "needs_confirmation": False,
                 "params": {}, "description": "Enter text for each figure; the tool never writes alt text on its own."})
    if empty_alt:
        add("FIG-EMPTY-ALT", WARNING, "Figures", "Figures with empty alternative text", [f"{_num(N[i])}" for i in empty_alt[:15]],
            nodes=empty_alt[:100], refs=[ua("7.3"), wcag("1.1.1", "non-text-content")],
            rec="Decorative images should be Artifacts, not Figures with empty Alt. Otherwise describe the image.")
    if weak_alt:
        add("FIG-WEAK-ALT", WARNING, "Figures", "Alternative text looks generic or like a file name (heuristic)",
            [f"{_num(N[i])}: \"{N[i]['alt'][:60]}\"" for i in weak_alt[:15]], nodes=weak_alt[:100], refs=[wcag("1.1.1", "non-text-content")],
            rec="Replace with a description of what the image shows or why it is there.")
    figs_all = [n for n in N if n["t"] == "Figure"]
    if figs_all:
        add("FIG-REVIEW", MANUAL, "Figures", f"Check that the alternative text of {len(figs_all)} figure(s) is accurate",
            ["A program can check that alt text exists, not that it is correct."], nodes=[n["id"] for n in figs_all][:100],
            refs=[wcag("1.1.1", "non-text-content")], rec="Compare each description with its image.")

    # ---- empty elements ----------------------------------------------------------
    empties = [n["id"] for n in N if not n["k"] and not n["mc"] and not n["objr"] and n["t"] not in EMPTY_OK]
    if empties:
        add("EMPTY-ELEMENT", WARNING, "Structure", "Structure elements with no content or children",
            [f"{_num(N[i])} is empty" for i in empties[:15]], nodes=empties[:100], refs=[ua("7.1")],
            rec="Remove empty elements (extra empty tags add noise for screen-reader users).", detail="Empty tags usually come from conversion tools.")
    blank = [n["id"] for n in N if n["t"] in TEXT_CONTAINERS and n["mc"] and not n["own_text"] and not any(N[c]["has_content"] for c in n["k"])
             and not any(content.get(pg, {}).get(mc, {}).get("kinds", set()) & {3, 2} for pg, mc in n["mc"]) and n["mc"][0][0] in content]
    if blank:
        add("BLANK-TEXT", WARNING, "Structure", "Text elements whose marked content has no visible text (heuristic)",
            [f"{_num(N[i])}" for i in blank[:15]], nodes=blank[:100], refs=[ua("7.1")], rec="Check whether the tag wraps whitespace only.")
    lang_bad = [n["id"] for n in N if n["lang"] and not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*", n["lang"].strip())]
    if lang_bad:
        add("ELEM-LANG", ERROR, "Language", "Invalid /Lang value on structure elements", [f"{_num(N[i])}: \"{N[i]['lang']}\"" for i in lang_bad[:15]],
            nodes=lang_bad[:60], refs=[ua("7.2"), wcag("3.1.2", "language-of-parts")], rec="Use a BCP 47 language tag such as en-US.")

    # ---- MCID / ParentTree cross-check --------------------------------------------
    pt = None
    try:
        ptr = tree.root_dict.get("/ParentTree")
        if ptr is not None:
            pt = NumberTree(ptr)
    except Exception as e:
        add("PT-UNREADABLE", ERROR, "ParentTree", "ParentTree cannot be read", [str(e)[:200]], refs=[iso32000("14.7.4.4"), mh("09", "Document structure")])
    pages_with_struct = sorted({pg for (pg, _mc) in tree.mcid_owner if pg})
    if tree.mcid_owner and pt is None and tree.root_dict.get("/ParentTree") is None:
        add("PT-MISSING", ERROR, "ParentTree", "StructTreeRoot has marked content but no /ParentTree", [],
            refs=[iso32000("14.7.4.4"), mh("09", "Document structure")], rec="Re-save with a tagging-aware tool or retag the file.")
    dang, unref, dupmc, ptbad = [], [], [], []
    for (pg, mc), owners in tree.mcid_owner.items():
        if pg is None:
            continue
        if len(owners) > 1:
            dupmc.append((pg, mc, owners))
        if pg in content and pg not in forms_with_marks and mc not in content[pg]:
            dang.append((pg, mc, owners[0]))
    for pg, m in content.items():
        if pg in forms_with_marks:
            continue
        for mc in m:
            if (pg, mc) not in tree.mcid_owner:
                unref.append((pg, mc))
    if dang:
        add("MCID-DANGLING", ERROR, "ParentTree", "Structure elements point to marked content that does not exist on the page",
            [f"{_num(N[o])} references MCID {mc} on page {pg}, but page {pg} has no such marked-content sequence" for pg, mc, o in dang[:12]],
            nodes=[o for _, _, o in dang][:80], pages=[p for p, _, _ in dang], refs=[iso32000("14.7.4.2"), mh("09", "Document structure")],
            rec="Retag the page or remove the stale elements.")
    if unref:
        add("MCID-UNREFERENCED", ERROR, "ParentTree", "Marked content on pages that no structure element references",
            [f"Page {pg}: MCID {mc} is marked as content but is not in the tag tree" for pg, mc in unref[:12]],
            pages=[p for p, _ in unref], count=len(unref), refs=[ua("7.1"), mh("01", "Real content tagged")],
            rec="Add the content to the structure tree or mark it as an Artifact.")
    if dupmc:
        add("MCID-DUPLICATE", ERROR, "ParentTree", "The same marked content is claimed by more than one element",
            [f"Page {pg} MCID {mc} is used by {', '.join('#' + str(o) for o in ow)}" for pg, mc, ow in dupmc[:12]],
            nodes=[o for _, _, ow in dupmc for o in ow][:80], pages=[p for p, _, _ in dupmc], refs=[iso32000("14.7.4.2")])
    pg_objs = {}
    if pt is not None:
        for pi in pages_with_struct:
            try:
                page = pdf.pages[pi - 1]
                sp = page.obj.get("/StructParents")
                if sp is None:
                    ptbad.append((pi, None, "page has tagged content but no /StructParents key"))
                    continue
                arr = pt.get(int(sp)) if int(sp) in pt else None
                if arr is None or not isinstance(arr, Array):
                    ptbad.append((pi, None, f"/ParentTree has no array for /StructParents {int(sp)}"))
                    continue
                for (p2, mc), owners in tree.mcid_owner.items():
                    if p2 != pi:
                        continue
                    ent = arr[mc] if mc < len(arr) else None
                    eog = _og(ent) if ent is not None else None
                    exp = N[owners[0]]["obj"]
                    if ent is None or eog is None:
                        ptbad.append((pi, owners[0], f"MCID {mc} has no ParentTree entry"))
                    elif f"{eog[0]} {eog[1]}" != exp:
                        ptbad.append((pi, owners[0], f"MCID {mc} maps to object {eog[0]} {eog[1]} in the ParentTree but is held by {exp}"))
            except Exception as e:
                ptbad.append((pi, None, f"unreadable: {e}"))
    if ptbad:
        add("PT-MISMATCH", ERROR, "ParentTree", "ParentTree does not match the structure tree",
            [f"Page {p}: {m}" for p, _n, m in ptbad[:12]], nodes=[n for _, n, _ in ptbad if n is not None][:80], pages=[p for p, _, _ in ptbad],
            count=len(ptbad), refs=[iso32000("14.7.4.4"), mh("09", "Document structure")],
            rec="Rebuilding the ParentTree needs a tagging-aware editor or a re-export; this tool does not rewrite it automatically.")
    # orphans: ParentTree values (elements) not reachable from the root
    orphans = []
    if pt is not None:
        try:
            seen_o = 0
            for k, v in pt.items():
                vals = list(v) if isinstance(v, Array) else [v]
                for e in vals:
                    if isinstance(e, Dictionary) and "/S" in e:
                        og = _og(e)
                        if og is not None and og not in tree.by_og:
                            orphans.append((k, og))
                seen_o += 1
                if seen_o > 20000:
                    break
        except Exception:
            pass
    if orphans:
        add("PT-ORPHAN", ERROR, "ParentTree", "ParentTree refers to structure elements that are not part of the tag tree",
            [f"Key {k}: object {og[0]} {og[1]} is not reachable from the StructTreeRoot" for k, og in orphans[:12]], count=len(orphans),
            refs=[iso32000("14.7.4.4")], rec="Orphaned elements should be removed or reattached.",
            fix={"action": "clean_pt_orphans", "label": "Clean ParentTree orphans", "needs_confirmation": False, "params": {},
                 "description": "Prunes dead object references in /ParentTree that are no longer part of the structure tree."})

    return F, summary


def _attr_dict(n):
    try:
        A = n["_d"].get("/A")
        if isinstance(A, Dictionary):
            return {str(k): 1 for k in A.keys()}
        if isinstance(A, Array):
            out = {}
            for it in A:
                if isinstance(it, Dictionary):
                    out.update({str(k): 1 for k in it.keys()})
            return out
    except Exception:
        pass
    return None


def _within_link_or_form(n, N):
    p = n["p"]
    while p is not None:
        if N[p]["t"] in ("Link", "Form"):
            return True
        p = N[p]["p"]
    return False


def _renumber(numbered, lv):
    """Propose levels that never skip: a heading may be at most previous+1."""
    out, last = {}, 0
    for h, l in zip(numbered, lv):
        new = l
        if last and l > last + 1:
            new = last + 1
        elif not last and l != 1:
            new = 1
        if new != l:
            if h["obj"]:
                out[h["obj"]] = new
        last = new
    return out


def _reading_order(N, content, add):
    """Heuristic: structure order vs on-page geometry. Flags pages where reading jumps
    back up the page without moving right (which a plain column change does not do)."""
    per_page = defaultdict(list)
    for n in N:
        for pg, mc in n["mc"]:
            e = content.get(pg, {}).get(mc)
            if e and e["bbox"] and e["text"]:
                per_page[pg].append((n["id"], e["bbox"]))
    bad_pages, ev, nodes = [], [], []
    for pg, seq in per_page.items():
        if len(seq) < 6:
            continue
        jumps = []
        for (a, ab), (b, bb) in zip(seq, seq[1:]):
            lh = max(1.0, ab[3] - ab[1])
            if bb[1] > ab[3] + lh and bb[0] < ab[2] - 2 * lh:
                jumps.append((a, b))
        if len(jumps) >= 2:
            bad_pages.append(pg)
            ev.append(f"Page {pg}: {len(jumps)} places where the structure order jumps back up the page (first: #{jumps[0][0]} -> #{jumps[0][1]})")
            nodes += [jumps[0][1]]
    if bad_pages:
        add("READ-ORDER-JUMPS", WARNING, "Reading order", "Possible reading-order problems (heuristic)", ev, nodes=nodes[:50], pages=bad_pages,
            refs=[wcag("1.3.2", "meaningful-sequence"), ua("7.1"), mh("09", "Document structure")],
            rec="Open the page preview and the Tag tree 'reading order' view; fix the order in a tagging editor. Sidebars and captions can cause false positives.")
