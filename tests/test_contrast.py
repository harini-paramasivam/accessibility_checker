"""WCAG contrast of the UI's own colour tokens (computed from static/app.css)."""
import re, unittest
from pathlib import Path
CSS = (Path(__file__).resolve().parent.parent / "static" / "app.css").read_text()
def _hex(h): return "#" + "".join(c * 2 for c in h[1:]) if len(h) == 4 else h
V = {k: _hex(v) for k, v in re.findall(r"--([a-z-]+):(#[0-9a-fA-F]{3,6})\b", CSS)}


def lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True); return (la + 0.05) / (lb + 0.05)


class Contrast(unittest.TestCase):
    def test_pairs_meet_aa(self):
        pairs = [("ink", "bg"), ("ink", "surface"), ("muted", "surface"), ("muted", "bg"), ("accent-ink", "accent"), ("accent", "surface"), ("accent", "accent-soft"),
                 ("err", "err-bg"), ("warn", "warn-bg"), ("man", "man-bg"), ("nr", "nr-bg"), ("ok", "ok-bg"), ("err", "surface"), ("focus", "surface")]
        for fg, bg in pairs:
            with self.subTest(pair=f"{fg} on {bg}"):
                self.assertGreaterEqual(ratio(V[fg], V[bg]), 4.5, f"{V[fg]} on {V[bg]} = {ratio(V[fg], V[bg]):.2f}")

if __name__ == "__main__":
    unittest.main()
