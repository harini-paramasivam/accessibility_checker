"""Vercel Serverless Function Entrypoint for AccessFix PDF."""
from __future__ import annotations
import sys
from pathlib import Path

# Add project root directory to sys.path so the accessfix package can be imported
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accessfix.app import create_app

# Vercel's Python runtime natively detects WSGI application instances named 'app'
app = create_app()
