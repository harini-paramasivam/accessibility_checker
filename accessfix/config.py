"""Runtime configuration. Every value can be overridden with an ACCESSFIX_* env var."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _find_tesseract() -> str:
    env = os.environ.get("TESSERACT_CMD")
    if env:
        return env
    import shutil
    w = shutil.which("tesseract")
    if w:
        return w
    candidates = [
        ROOT / "tools" / "tesseract" / "tesseract.exe",
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        Path.home() / "AppData" / "Local" / "Tesseract-OCR" / "tesseract.exe",
        Path.home() / "AppData" / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return "tesseract"


def _find_tessdata() -> str:
    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        return env
    tess_exe = _find_tesseract()
    if tess_exe != "tesseract":
        p = Path(tess_exe).parent / "tessdata"
        if p.is_dir():
            return str(p)
    candidates = [
        ROOT / "tools" / "tesseract" / "tessdata",
        Path(r"C:\Program Files\Tesseract-OCR\tessdata"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tessdata"),
        Path.home() / "AppData" / "Local" / "Tesseract-OCR" / "tessdata",
    ]
    for c in candidates:
        if c.is_dir():
            return str(c)
    return ""


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("ACCESSFIX_DATA_DIR", ROOT / "data")))
    max_upload_mb: int = field(default_factory=lambda: _int("ACCESSFIX_MAX_UPLOAD_MB", 50))
    max_pages: int = field(default_factory=lambda: _int("ACCESSFIX_MAX_PAGES", 300))
    max_struct_nodes: int = field(default_factory=lambda: _int("ACCESSFIX_MAX_STRUCT_NODES", 150000))
    analysis_time_budget_s: float = field(default_factory=lambda: _float("ACCESSFIX_ANALYSIS_BUDGET_S", 120))
    ttl_hours: float = field(default_factory=lambda: _float("ACCESSFIX_TTL_HOURS", 24))
    tesseract_cmd: str = field(default_factory=lambda: _find_tesseract())
    tessdata_dir: str = field(default_factory=lambda: _find_tessdata())
    ocr_page_timeout_s: float = field(default_factory=lambda: _float("ACCESSFIX_OCR_PAGE_TIMEOUT_S", 180))
    ocr_job_timeout_s: float = field(default_factory=lambda: _float("ACCESSFIX_OCR_JOB_TIMEOUT_S", 3600))
    ocr_workers: int = field(default_factory=lambda: _int("ACCESSFIX_OCR_WORKERS", max(1, min(4, (os.cpu_count() or 1)))))
    ocr_max_pixels: int = field(default_factory=lambda: _int("ACCESSFIX_OCR_MAX_PIXELS", 60_000_000))
    max_concurrent_jobs: int = field(default_factory=lambda: _int("ACCESSFIX_MAX_JOBS", 2))
    cors_origins: str = field(default_factory=lambda: os.environ.get("ACCESSFIX_CORS_ORIGINS", ""))

    @property
    def max_upload_bytes(self):
        return self.max_upload_mb * 1024 * 1024
