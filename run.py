#!/usr/bin/env python3
"""Start AccessFix PDF:  python run.py   (then open http://127.0.0.1:5050)"""
import os
from accessfix.app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("ACCESSFIX_HOST", "127.0.0.1")
    port = int(os.environ.get("ACCESSFIX_PORT", "5050"))
    print(f"AccessFix PDF running at http://{host}:{port}")
    app.run(host=host, port=port, threaded=True, debug=False)
