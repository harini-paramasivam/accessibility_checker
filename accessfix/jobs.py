"""Background jobs with real progress, logs and cooperative cancellation."""
from __future__ import annotations
import logging
import threading
import time
import traceback
import uuid

log = logging.getLogger("accessfix.jobs")


class Job:
    def __init__(self, kind, doc_id):
        self.id = uuid.uuid4().hex
        self.kind, self.doc_id = kind, doc_id
        self.status = "queued"
        self.done = self.total = 0
        self.message = ""
        self.logs: list[dict] = []
        self.result = None
        self.error = None
        self.cancel = threading.Event()
        self.procs: set = set()
        self.created = time.time()
        self.finished = None
        self._lock = threading.Lock()

    # control interface used by workers
    def progress(self, done, total, msg=""):
        with self._lock:
            self.done, self.total, self.message = done, total, msg

    def log(self, level, msg):
        with self._lock:
            self.logs.append({"t": round(time.time() - self.created, 1), "level": level, "msg": str(msg)[:500]})
            del self.logs[:-300]
        log.log({"error": logging.ERROR, "warn": logging.WARNING}.get(level, logging.INFO), "[%s %s] %s", self.kind, self.id[:8], msg)

    def request_cancel(self):
        self.cancel.set()
        for p in list(self.procs):
            try:
                p.kill()
            except Exception:
                pass

    def to_dict(self):
        with self._lock:
            return {"id": self.id, "kind": self.kind, "doc_id": self.doc_id, "status": self.status, "done": self.done, "total": self.total,
                    "message": self.message, "logs": list(self.logs), "result": self.result, "error": self.error,
                    "elapsed": round((self.finished or time.time()) - self.created, 1), "cancelable": self.kind == "ocr" and self.status in ("queued", "running")}


class JobManager:
    def __init__(self, max_concurrent=2):
        self.jobs: dict[str, Job] = {}
        self.sem = threading.Semaphore(max_concurrent)
        self._lock = threading.Lock()

    def submit(self, kind, doc_id, fn):
        job = Job(kind, doc_id)
        with self._lock:
            self.jobs[job.id] = job
            for k in [k for k, j in self.jobs.items() if j.finished and time.time() - j.finished > 6 * 3600]:
                del self.jobs[k]

        def run():
            with self.sem:
                if job.cancel.is_set():
                    job.status, job.finished = "canceled", time.time()
                    return
                job.status = "running"
                try:
                    res = fn(job)
                    job.result = res
                    job.status = "completed_with_errors" if (isinstance(res, dict) and res.get("has_errors")) else "completed"
                except Exception as e:
                    code = getattr(e, "code", "internal_error")
                    if code == "canceled":
                        job.status = "canceled"
                        job.error = {"code": "canceled", "message": "The job was canceled; no output was produced."}
                    else:
                        job.status = "failed"
                        msg = getattr(e, "message", None) or f"{type(e).__name__}: {e}"
                        job.error = {"code": code, "message": msg}
                        job.log("error", msg)
                        if code == "internal_error":
                            log.error("Job failed:\n%s", traceback.format_exc())
                finally:
                    job.finished = time.time()
                    job.progress(job.done, job.total, job.message)

        threading.Thread(target=run, daemon=True, name=f"job-{job.id[:6]}").start()
        return job

    def get(self, job_id):
        return self.jobs.get(job_id)
