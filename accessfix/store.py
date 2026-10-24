"""On-disk document store. Every document lives in its own directory named by a random id.

Client input never becomes a path: ids and version ids are validated against strict
patterns, file names are generated server-side, and the display name is sanitised.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from pathlib import Path

DOC_RE = re.compile(r"^[0-9a-f]{32}$")
VER_RE = re.compile(r"^v\d{1,3}$")


class NotFound(Exception):
    pass


def safe_name(name: str) -> str:
    name = unicodedata.normalize("NFC", os.path.basename((name or "").replace("\\", "/")))
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*\x7f')
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "document.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    stem, ext = name[:-4][:100], name[-4:]
    return (stem or "document") + ext


class Store:
    def __init__(self, root: Path):
        self.root = Path(root) / "docs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._glock = threading.Lock()

    def lock(self, doc_id) -> threading.Lock:
        with self._glock:
            return self._locks.setdefault(doc_id, threading.Lock())

    def dir(self, doc_id: str) -> Path:
        if not DOC_RE.match(doc_id or ""):
            raise NotFound("Unknown document")
        d = self.root / doc_id
        if not d.is_dir():
            raise NotFound("Unknown document (it may have expired and been deleted)")
        return d

    def create(self, display_name: str, data: bytes) -> dict:
        doc_id = uuid.uuid4().hex
        d = self.root / doc_id
        d.mkdir(parents=True)
        (d / "v0.pdf").write_bytes(data)
        meta = {"id": doc_id, "name": safe_name(display_name), "created": time.time(), "touched": time.time(),
                "versions": [{"id": "v0", "label": "Original upload", "kind": "original", "file": "v0.pdf", "size": len(data),
                              "sha256": hashlib.sha256(data).hexdigest(), "created": time.time(), "parent": None}]}
        self._write(d, meta)
        return meta

    def _write(self, d: Path, meta: dict):
        tmp = d / "meta.json.tmp"
        tmp.write_text(json.dumps(meta))
        os.replace(tmp, d / "meta.json")

    def meta(self, doc_id) -> dict:
        d = self.dir(doc_id)
        m = json.loads((d / "meta.json").read_text())
        return m

    def touch(self, doc_id):
        m = self.meta(doc_id)
        m["touched"] = time.time()
        self._write(self.dir(doc_id), m)

    def version(self, doc_id, vid=None) -> dict:
        m = self.meta(doc_id)
        if vid in (None, "", "latest"):
            return m["versions"][-1]
        if not VER_RE.match(vid):
            raise NotFound("Unknown version")
        for v in m["versions"]:
            if v["id"] == vid:
                return v
        raise NotFound("Unknown version")

    def path(self, doc_id, vid=None) -> Path:
        v = self.version(doc_id, vid)
        return self.dir(doc_id) / v["file"]

    def new_version_path(self, doc_id) -> tuple[str, Path]:
        m = self.meta(doc_id)
        vid = f"v{len(m['versions'])}"
        return vid, self.dir(doc_id) / f"{vid}.pdf"

    def commit_version(self, doc_id, vid, label, kind, parent, extra=None) -> dict:
        d = self.dir(doc_id)
        f = d / f"{vid}.pdf"
        data = f.read_bytes()
        m = self.meta(doc_id)
        v = {"id": vid, "label": label, "kind": kind, "file": f.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
             "created": time.time(), "parent": parent}
        if extra:
            v.update(extra)
        m["versions"].append(v)
        m["touched"] = time.time()
        self._write(d, m)
        return v

    def commit_file(self, doc_id, tmp: Path, label, kind, parent, extra=None) -> dict:
        """Atomically give a finished temp file the next version number."""
        with self.lock(doc_id):
            vid = f"v{len(self.meta(doc_id)['versions'])}"
            os.replace(tmp, self.dir(doc_id) / f"{vid}.pdf")
            return self.commit_version(doc_id, vid, label, kind, parent, extra)

    def update_version(self, doc_id, vid, **kv):
        m = self.meta(doc_id)
        for v in m["versions"]:
            if v["id"] == vid:
                v.update(kv)
        self._write(self.dir(doc_id), m)

    def save_json(self, doc_id, name, obj):
        d = self.dir(doc_id)
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", name):
            raise ValueError("bad name")
        tmp = d / f"{name}.json.tmp"
        tmp.write_text(json.dumps(obj))
        os.replace(tmp, d / f"{name}.json")

    def load_json(self, doc_id, name):
        f = self.dir(doc_id) / f"{name}.json"
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", name) or not f.is_file():
            return None
        return json.loads(f.read_text())

    def cache_dir(self, doc_id) -> Path:
        c = self.dir(doc_id) / "cache"
        c.mkdir(exist_ok=True)
        return c

    def delete(self, doc_id):
        shutil.rmtree(self.dir(doc_id), ignore_errors=True)

    def cleanup(self, ttl_hours: float) -> int:
        n, cutoff = 0, time.time() - ttl_hours * 3600
        for d in self.root.iterdir():
            try:
                if DOC_RE.match(d.name) and d.is_dir():
                    t = json.loads((d / "meta.json").read_text()).get("touched", 0)
                    if t < cutoff:
                        shutil.rmtree(d, ignore_errors=True)
                        n += 1
            except Exception:
                continue
        return n
