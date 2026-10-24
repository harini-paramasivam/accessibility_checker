"""Vercel Flask entrypoint for AccessFix PDF."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run import app

if __name__ == "__main__" and not os.environ.get("VERCEL"):
    host = os.environ.get("ACCESSFIX_HOST", "127.0.0.1")
    port = int(os.environ.get("ACCESSFIX_PORT", "5050"))
    app.run(host=host, port=port, threaded=True, debug=False)
