"""Synthetic test documents (no third-party content).

TaggedBuilder writes real PDF structure: StructTreeRoot, ParentTree, StructElem
trees, marked-content sequences with MCIDs, an embedded TrueType font, link
annotations with OBJR, and optional defects.
"""
from __future__ import annotations
import io
import random
from pathlib import Path
import pikepdf
from pikepdf import Array, Dictionary, Name, Stream, String
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "static" / "legacy" / "fonts"
SANS = FONT_DIR / "LiberationSans-Regular.ttf"
SERIF = FONT_DIR / "LiberationSerif-Regular.ttf"

_W = None


def _embed_font(pdf):
    """Embed Liberation Sans as a simple TrueType font with WinAnsiEncoding."""
    data = SANS.read_bytes()
    tt = TTFont(str(SANS))
    upm = tt["head"].unitsPerEm
    cmap = tt.getBestCmap()
    hmtx = tt["hmtx"]
    widths = []
    for code in range(32, 256):
        ch = bytes([code]).decode("cp1252", "replace")
        g = cmap.get(ord(ch))
        widths.append(round(hmtx[g][0] * 1000 / upm) if g else 0)
    ff = pdf.make_stream(data)
    ff.Length1 = len(data)
    bb = [round(v * 1000 / upm) for v in (tt["head"].xMin, tt["head"].yMin, tt["head"].xMax, tt["head"].yMax)]
    fd = pdf.make_indirect(Dictionary(Type=Name.FontDescriptor, FontName=Name("/LiberationSans"), Flags=32, FontBBox=Array(bb),
                                      ItalicAngle=0, Ascent=905, Descent=-212, CapHeight=716, StemV=80, FontFile2=ff))
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.TrueType, BaseFont=Name("/LiberationSans"), FirstChar=32, LastChar=255,
                                        Widths=Array(widths), FontDescriptor=fd, Encoding=Name.WinAnsiEncoding))
    return font


def _esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class TaggedBuilder:
    def __init__(self, n_pages=1, size=(612, 792), embed_font=True):
        self.pdf = pikepdf.new()
        self.size = size
        self.font = _embed_font(self.pdf) if embed_font else self.pdf.make_indirect(
            Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding))
        self.pages, self.content, self.mcid, self.pt = [], [], [], []   # pt[i] = list indexed by mcid
        self.annots_pt = {}
        self.root = self.pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot, K=Array(), RoleMap=Dictionary()))
        self.elems = []
        self._next_key = 1000
        for _ in range(n_pages):
            self.add_page()

    # -- pages ---------------------------------------------------------------
    def add_page(self):
        i = len(self.pages)
        page = self.pdf.add_blank_page(page_size=self.size)
        page.obj.Resources = Dictionary(Font=Dictionary(F1=self.font))
        page.obj.StructParents = i
        self.pages.append(page)
        self.content.append([])
        self.mcid.append(0)
        self.pt.append([])
        return i

    # -- structure -------------------------------------------------------------
    def elem(self, s, parent=None, page=None, **kw):
        d = Dictionary(Type=Name.StructElem, S=Name("/" + s), K=Array())
        if page is not None:
            d.Pg = self.pages[page].obj
        for k, v in kw.items():
            d[Name("/" + k)] = v if not isinstance(v, str) else String(v)
        e = self.pdf.make_indirect(d)
        if parent is None:
            e.P = self.root
            self.root.K.append(e)
        else:
            e.P = parent
            parent.K.append(e)
        self.elems.append(e)
        return e

    def _mc(self, page, elem, tag, ops, artifact=False):
        if artifact:
            self.content[page].append(f"/Artifact BMC\n{ops}\nEMC")
            return None
        m = self.mcid[page]
        self.mcid[page] += 1
        self.content[page].append(f"/{tag} <</MCID {m}>> BDC\n{ops}\nEMC")
        elem.K.append(m)
        if elem.get("/Pg") is None:
            elem.Pg = self.pages[page].obj
        self.pt[page].append(elem)
        return m

    def text(self, page, parent, tag, s, x, y, size=12, elem=None):
        e = elem or self.elem(tag, parent, page)
        self._mc(page, e, tag, f"BT /F1 {size} Tf {x} {y} Td ({_esc(s)}) Tj ET")
        return e

    def figure(self, page, parent, rect, alt=None, tag="Figure"):
        kw = {"Alt": alt} if alt is not None else {}
        e = self.elem(tag, parent, page, **kw)
        x, y, w, h = rect
        self._mc(page, e, "Figure", f"0.2 0.4 0.8 rg {x} {y} {w} {h} re f")
        return e

    def artifact(self, page, text, x, y):
        self._mc(page, None, "Artifact", f"BT /F1 9 Tf {x} {y} Td ({_esc(text)}) Tj ET", artifact=True)

    def link(self, page, parent, text, uri, x, y, contents="Example site", size=12, with_obj=True):
        e = self.elem("Link", parent, page)
        self._mc(page, e, "Link", f"BT /F1 {size} Tf {x} {y} Td ({_esc(text)}) Tj ET")
        a = Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array([x, y - 3, x + 6 * len(text), y + size]), Border=Array([0, 0, 0]),
                       A=Dictionary(S=Name.URI, URI=String(uri)))
        if contents:
            a.Contents = String(contents)
        key = self._next_key
        self._next_key += 1
        if with_obj:
            a.StructParent = key
        ann = self.pdf.make_indirect(a)
        pg = self.pages[page].obj
        pg.Annots = pg.get("/Annots", Array())
        pg.Annots.append(ann)
        a.P = pg
        if with_obj:
            e.K.append(Dictionary(Type=Name.OBJR, Pg=pg, Obj=ann))
            self.annots_pt[key] = e
        return e

    def simple_list(self, page, parent, items, x, y):
        L = self.elem("L", parent, page)
        for n, it in enumerate(items):
            li = self.elem("LI", L, page)
            lb = self.elem("Lbl", li, page)
            self._mc(page, lb, "Lbl", f"BT /F1 12 Tf {x} {y - n * 18} Td (\\225) Tj ET")
            body = self.elem("LBody", li, page)
            self._mc(page, body, "LBody", f"BT /F1 12 Tf {x + 16} {y - n * 18} Td ({_esc(it)}) Tj ET")
        return L

    def simple_table(self, page, parent, rows, x, y, header=True):
        T = self.elem("Table", parent, page)
        for r, row in enumerate(rows):
            tr = self.elem("TR", T, page)
            for c, cell in enumerate(row):
                kind = "TH" if (header and r == 0) else "TD"
                td = self.elem(kind, tr, page)
                if kind == "TH":
                    td.A = Dictionary(O=Name.Table, Scope=Name.Column)
                self._mc(page, td, kind, f"BT /F1 11 Tf {x + c * 110} {y - r * 20} Td ({_esc(cell)}) Tj ET")
        return T

    # -- finish -----------------------------------------------------------------
    def finish(self, path, title="Test document", lang="en-US", marked=True, parent_tree=True, meta=True, tabs=True):
        for i, page in enumerate(self.pages):
            body = "\n".join(self.content[i]).encode("latin-1")
            page.obj.Contents = self.pdf.make_stream(body)
            if tabs and page.obj.get("/Annots") is not None:
                page.obj.Tabs = Name.S
        if parent_tree:
            nums = Array()
            for i, arr in enumerate(self.pt):
                nums.append(i)
                nums.append(Array(arr))
            for k, e in self.annots_pt.items():
                nums.append(k)
                nums.append(e)
            self.root.ParentTree = self.pdf.make_indirect(Dictionary(Nums=nums))
            self.root.ParentTreeNextKey = self._next_key
        self.pdf.Root.StructTreeRoot = self.root
        if marked:
            self.pdf.Root.MarkInfo = Dictionary(Marked=True)
        if lang:
            self.pdf.Root.Lang = String(lang)
        if meta:
            self.pdf.Root.ViewerPreferences = Dictionary(DisplayDocTitle=True)
            self.pdf.docinfo["/Title"] = title
            with self.pdf.open_metadata(set_pikepdf_as_editor=False) as m:
                m["dc:title"] = title
        self.pdf.save(str(path))
        return Path(path)


def good_tagged(path):
    """3 pages: headings H1/H2, paragraphs, list, table, figure with alt, link, artifact footer."""
    b = TaggedBuilder(3)
    doc = b.elem("Document")
    b.text(0, doc, "H1", "Annual Accessibility Report", 72, 700, 22)
    b.text(0, doc, "P", "This document describes how the quarterly review was carried out.", 72, 670)
    b.text(0, doc, "H2", "Scope of the review", 72, 640, 16)
    b.simple_list(0, doc, ["First finding about contrast", "Second finding about headings"], 72, 610)
    b.link(0, doc, "Visit example site", "https://example.org/a11y", 72, 560, contents="Example accessibility site")
    b.artifact(0, "Page 1", 290, 30)
    b.text(1, doc, "H2", "Results table", 72, 700, 16)
    b.simple_table(1, doc, [["Metric", "Q1", "Q2"], ["Errors", "12", "7"], ["Warnings", "30", "18"]], 72, 670)
    b.figure(1, doc, (72, 520, 200, 100), alt="Bar chart: errors fell from twelve to seven between Q1 and Q2")
    b.text(2, doc, "H2", "Conclusion", 72, 700, 16)
    b.text(2, doc, "P", "Errors decreased in the second quarter.", 72, 670)
    return b.finish(path)


def build_defect(path, defect):
    b = TaggedBuilder(1)
    doc = b.elem("Document")
    if defect == "heading_skip":
        b.text(0, doc, "H1", "Title", 72, 700, 20)
        b.text(0, doc, "H3", "Skipped straight to level three", 72, 670, 14)
        b.text(0, doc, "P", "Body", 72, 640)
    elif defect == "heading_mixed":
        b.text(0, doc, "H", "Plain heading", 72, 700, 20)
        b.text(0, doc, "H2", "Numbered heading", 72, 670, 14)
    elif defect == "bad_list":
        L = b.elem("L", doc, 0)
        b.text(0, L, "P", "Paragraph directly inside a list", 72, 700)
        li = b.elem("LI", L, 0)
        b.text(0, li, "P", "LI with a P instead of LBody", 72, 680)
    elif defect == "bad_table":
        T = b.elem("Table", doc, 0)
        b.text(0, T, "TD", "TD directly inside Table", 72, 700)
        tr = b.elem("TR", T, 0)
        b.text(0, tr, "TD", "a", 72, 680)
        b.text(0, tr, "TD", "b", 150, 680)
        tr2 = b.elem("TR", T, 0)
        b.text(0, tr2, "TD", "only one cell", 72, 660)
    elif defect == "table_no_th":
        b.simple_table(0, doc, [["a", "b"], ["c", "d"]], 72, 700, header=False)
    elif defect == "figure_no_alt":
        b.figure(0, doc, (72, 600, 100, 80), alt=None)
        b.figure(0, doc, (200, 600, 100, 80), alt="")
        b.figure(0, doc, (330, 600, 100, 80), alt="image.png")
    elif defect == "unmapped_role":
        b.text(0, doc, "MyCustomTag", "Custom tag without role map", 72, 700)
    elif defect == "circular_role":
        b.root.RoleMap = Dictionary(Foo=Name("/Bar"), Bar=Name("/Foo"))
        b.text(0, doc, "Foo", "Circular", 72, 700)
    elif defect == "link_no_obj":
        b.link(0, doc, "click here", "https://example.org", 72, 700, contents=None, with_obj=False)
    elif defect == "no_parenttree":
        b.text(0, doc, "P", "Text", 72, 700)
        return b.finish(path, parent_tree=False)
    elif defect == "parenttree_mismatch":
        e1 = b.text(0, doc, "P", "One", 72, 700)
        e2 = b.text(0, doc, "P", "Two", 72, 680)
        b.pt[0][0], b.pt[0][1] = e2, e1       # swapped entries
    elif defect == "dangling_mcid":
        e = b.text(0, doc, "P", "Real", 72, 700)
        e.K.append(99)
    elif defect == "untagged_content":
        b.text(0, doc, "P", "Tagged", 72, 700)
        b.content[0].append("BT /F1 12 Tf 72 600 Td (Untagged text in a tagged file) Tj ET")
    elif defect == "p_mismatch":
        a = b.text(0, doc, "P", "Text", 72, 700)
        a.P = b.root
    elif defect == "duplicate_ref":
        a = b.text(0, doc, "P", "Text", 72, 700)
        doc.K.append(a)
    elif defect == "cycle":
        a = b.elem("Div", doc, 0)
        a.K.append(doc)
    elif defect == "empty_elem":
        b.elem("P", doc, 0)
        b.text(0, doc, "P", "ok", 72, 700)
    elif defect == "no_meta":
        b.text(0, doc, "P", "Text", 72, 700)
        b.link(0, doc, "Example", "https://example.org", 72, 650, contents="Example")
        return b.finish(path, title="", lang="", meta=False, tabs=False)
    elif defect == "not_marked":
        b.text(0, doc, "P", "Text", 72, 700)
        return b.finish(path, marked=False)
    elif defect == "pt_orphan":
        b.text(0, doc, "P", "Real paragraph", 72, 700)
        orphan_elem = b.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc))
        b.pt[0].append(orphan_elem)
    elif defect == "not_embedded":
        b2 = TaggedBuilder(1, embed_font=False)
        d2 = b2.elem("Document")
        b2.text(0, d2, "P", "Helvetica not embedded", 72, 700)
        return b2.finish(path)
    return b.finish(path)


# ---------------------------------------------------------------------------- untagged / scans
def untagged_text(path, pages=2):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    for i in range(pages):
        c.setFont("Helvetica", 14)
        c.drawString(72, 750, f"Untagged document page {i + 1}")
        c.setFont("Helvetica", 11)
        for n in range(8):
            c.drawString(72, 720 - n * 16, "This is ordinary native text produced without any tags in the file.")
        c.showPage()
    c.save()
    return Path(path)


SCAN_LINES = [
    ["QUARTERLY REVENUE REPORT", "Invoice 48213 payable to Kowalski Brothers", "Total amount due: 1,250.00 euros by March"],
    ["MEETING MINUTES SUMMARY", "Attendees approved the budget unanimously", "Next review scheduled for September"],
    ["SHIPPING MANIFEST", "Container 7731 departs Rotterdam on Friday", "Handle with care: fragile instruments"],
]


def scan_image(lines, dpi=200, size=(612, 792), noise=True, seed=1):
    random.seed(seed)
    w, h = int(size[0] / 72 * dpi), int(size[1] / 72 * dpi)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype(str(SANS), int(0.30 * dpi))
    reg = ImageFont.truetype(str(SERIF), int(0.19 * dpi))
    y = int(1.0 * dpi)
    for i, ln in enumerate(lines):
        d.text((int(0.9 * dpi), y), ln, font=big if i == 0 else reg, fill=20)
        y += int(0.55 * dpi) if i == 0 else int(0.42 * dpi)
    if noise:
        px = img.load()
        for _ in range(int(w * h * 0.0006)):
            px[random.randrange(w), random.randrange(h)] = random.randrange(120, 255)
        img = img.filter(ImageFilter.GaussianBlur(0.6))
    return img


def scanned_pdf(path, pages=1, rotate=0, dpi=200, lines_sets=None, invisible_text=None):
    """Image-only PDF (optionally with invisible OCR-style text, optionally /Rotate)."""
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    sets = lines_sets or SCAN_LINES
    for i in range(pages):
        img = scan_image(sets[i % len(sets)], dpi=dpi, seed=i + 1)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        from reportlab.lib.utils import ImageReader
        c.drawImage(ImageReader(buf), 0, 0, 612, 792)
        if invisible_text:
            t = c.beginText(72, 700)
            t.setTextRenderMode(3)
            t.setFont("Helvetica", 14)
            for ln in invisible_text:
                t.textLine(ln)
            c.drawText(t)
        c.showPage()
    c.save()
    if rotate:
        with pikepdf.open(str(path), allow_overwriting_input=True) as pdf:
            for p in pdf.pages:
                p.Rotate = rotate
            pdf.save(str(path))
    return Path(path)


def mixed_pdf(path):
    """p1 native text, p2 scan, p3 native text with a small photo-like image."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 14)
    c.drawString(72, 720, "Native introduction page with searchable words: Alpha Bravo Charlie")
    for n in range(6):
        c.drawString(72, 690 - n * 18, "Ordinary running text that already exists in the file and must survive OCR.")
    c.showPage()
    img = scan_image(SCAN_LINES[0], dpi=200)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    c.drawImage(ImageReader(buf), 0, 0, 612, 792)
    c.showPage()
    c.setFont("Helvetica", 14)
    c.drawString(72, 720, "Closing page with native text: Delta Echo Foxtrot")
    for n in range(6):
        c.drawString(72, 690 - n * 18, "More ordinary running text that is not part of any scan at all here.")
    photo = Image.effect_noise((200, 150), 60).convert("RGB")
    b2 = io.BytesIO()
    photo.save(b2, "PNG")
    b2.seek(0)
    c.drawImage(ImageReader(b2), 72, 300, 200, 150)
    c.showPage()
    c.save()
    return Path(path)


def tagged_plus_scan(path):
    """A tagged document (pages 1-2) with a scanned page 3 appended: struct tree must survive OCR."""
    good = good_tagged(Path(path).with_suffix(".base.pdf"))
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    tmp = Path(path).with_suffix(".scan.pdf")
    scanned_pdf(tmp, pages=1)
    with pikepdf.open(str(good)) as pdf, pikepdf.open(str(tmp)) as sc:
        pdf.pages.extend(sc.pages)
        pdf.save(str(path))
    return Path(path)


def encrypted_pdf(path, user="secret", owner="owner", allow_access=True):
    src = untagged_text(Path(path).with_suffix(".src.pdf"), 1)
    with pikepdf.open(str(src)) as pdf:
        if allow_access:
            enc = pikepdf.Encryption(user=user, owner=owner)
        else:   # only R=3 stores an independent "extract for accessibility" bit that readers honour
            enc = pikepdf.Encryption(user=user, owner=owner, R=3, aes=False, metadata=False, allow=pikepdf.Permissions(accessibility=False, extract=False))
        pdf.save(str(path), encryption=enc)
    return Path(path)
