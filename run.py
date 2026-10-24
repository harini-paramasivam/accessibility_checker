#!/usr/bin/env python3
"""Start AccessFix PDF:  python run.py   (then open http://127.0.0.1:5050)"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accessfix.app import create_app

app = create_app()

if __name__ == "__main__" and not os.environ.get("VERCEL"):
    host = os.environ.get("ACCESSFIX_HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    port = int(os.environ.get("PORT") or os.environ.get("ACCESSFIX_PORT", "5050"))
    print(f"AccessFix PDF running at http://{host}:{port}")
    app.run(host=host, port=port, threaded=True, debug=False)
