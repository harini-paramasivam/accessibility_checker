import warnings; warnings.simplefilter("ignore", ResourceWarning)
import shutil, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures as fx
from accessfix.app import create_app
from accessfix.config import Config
from accessfix.analyze import analyze

WORK = Path(tempfile.mkdtemp(prefix="afx-fixtures-"))
_cache = {}


_fixture_lock = threading.Lock()


def fixture(name, builder, *a, **kw):
    with _fixture_lock:
        if name not in _cache:
            p = WORK / name
            if not p.exists():
                builder(p, *a, **kw)
            _cache[name] = p
        return _cache[name]


def good(): return fixture("good.pdf", fx.good_tagged)
def untagged(): return fixture("untagged.pdf", fx.untagged_text)
def scanned(pages=1): return fixture(f"scan{pages}.pdf", fx.scanned_pdf, pages=pages)
def mixed(): return fixture("mixed.pdf", fx.mixed_pdf)
def defect(d): return fixture(f"d_{d}.pdf", fx.build_defect, d)


def make_cfg(**kw):
    cfg = Config()
    cfg.data_dir = Path(tempfile.mkdtemp(prefix="afx-data-"))
    cfg.ocr_workers = 1
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def run_analyze(path, cfg=None):
    return analyze(Path(path), cfg or make_cfg())


def codes(a, sev=None):
    return {f["code"] for f in a["findings"] if sev is None or f["severity"] in (sev if isinstance(sev, tuple) else (sev,))}


def pdftotext(path, first=None, last=None):
    if shutil.which("pdftotext"):
        cmd = ["pdftotext"] + (["-f", str(first), "-l", str(last)] if first else []) + [str(path), "-"]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    import pypdfium2 as pdfium
    from accessfix.pdfium_content import LOCK
    out = []
    with LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            start = (first - 1) if first else 0
            end = last if last else len(doc)
            for i in range(start, end):
                if 0 <= i < len(doc):
                    pg = doc[i]
                    tp = pg.get_textpage()
                    out.append(tp.get_text_range() or "")
                    tp.close()
                    pg.close()
        finally:
            doc.close()
    return "\n".join(out)


def pdfinfo_tagged(path):
    out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30).stdout
    for ln in out.splitlines():
        if ln.startswith("Tagged:"):
            return ln.split(":", 1)[1].strip()
    return None


class ApiCase(unittest.TestCase):
    cfg_kw = {}

    def setUp(self):
        self.cfg = make_cfg(**self.cfg_kw)
        self.app = create_app(self.cfg)
        self.app.config["TESTING"] = True
        self.c = self.app.test_client()

    def upload(self, path, name=None):
        with open(path, "rb") as f:
            r = self.c.post("/api/documents", data={"file": (f, name or Path(path).name)})
        return r

    def wait(self, job_id, timeout=120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.c.get(f"/api/jobs/{job_id}").json
            if j["status"] not in ("queued", "running"):
                return j
            time.sleep(0.15)
        self.fail("job timeout")

    def upload_ok(self, path, name=None):
        r = self.upload(path, name)
        self.assertEqual(r.status_code, 201, r.data)
        self.wait(r.json["job"]["id"])
        return r.json["document"]["id"]

    def analysis(self, doc, ver=None):
        q = f"?version={ver}" if ver else ""
        return self.c.get(f"/api/documents/{doc}/analysis{q}").json["analysis"]

    def ocr(self, doc, **body):
        r = self.c.post(f"/api/documents/{doc}/ocr", json=body)
        return r

HAVE_TESS = shutil.which("tesseract") is not None or Path(Config().tesseract_cmd).is_file()
HAVE_POPPLER = shutil.which("pdftotext") is not None
