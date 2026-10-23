"""Reading handwritten numbers on paper questionnaires, locally (papierfragebogen-konzept.md E8 B,
Ralf 2026-10-10: "ja" to the data set).

A number field of a scanned sheet is cut out and straightened (paper_scan.snippet), made black and white, and split into
its strokes (connected dark pixels; strokes above one another are one digit, a small one at the foot is a decimal
point). Every digit is fitted into 32 x 32 pixels keeping its proportions, counted in 4 x 4 blocks (8 x 8 numbers from
0 to 16) and compared with 5,620 digits written by 43 people (k nearest neighbours, k = 3). Nothing leaves the server.

The result is only a suggestion in the field of the review page: a person confirms it with "Übernehmen" as any typed
value. A number is suggested only when every digit was recognised with all three neighbours agreeing; otherwise the
field stays empty.

Data: app/data/paper_digits.npz, built by tools/build_paper_digits.py from "Optical Recognition of Handwritten Digits"
(Alpaydin & Kaynak, 1998), UCI Machine Learning Repository, CC BY 4.0, https://doi.org/10.24432/C50P49. On the
writer-independent test set of that data the method reads 98 % of the digits right."""
import os

import numpy as np
from PIL import Image

K = 3
SCALE = 8.0  # pixels per mm when cutting out a field
MIN_PIXELS = 12  # smaller ink is a speck
MAX_DIGITS = 12
_DATA = None


def _model():
    global _DATA
    if _DATA is None:
        path = os.path.join(os.path.dirname(__file__), "data", "paper_digits.npz")
        with np.load(path) as d:
            _DATA = (d["x"].astype(np.float32), d["y"].astype(np.int64))
    return _DATA


def features(bitmap):
    """A digit (bool array, ink True) as the 64 numbers of the data set: fitted into 32 x 32 keeping its proportions,
    centred, counted in 4 x 4 blocks."""
    ys, xs = np.nonzero(bitmap)
    if not len(xs):
        return None
    crop = bitmap[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = crop.shape
    s = 32 / max(h, w)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    img = Image.fromarray((crop * 255).astype(np.uint8)).resize((nw, nh), Image.BILINEAR)
    canvas = Image.new("L", (32, 32))
    canvas.paste(img, ((32 - nw) // 2, (32 - nh) // 2))
    b = np.asarray(canvas) > 127
    return b.reshape(8, 4, 8, 4).sum(axis=(1, 3)).astype(np.float32).ravel()


def classify(vector):
    """(digit, votes of the K nearest neighbours for it)."""
    x, y = _model()
    d = ((x - vector) ** 2).sum(axis=1)
    near = y[np.argpartition(d, K)[:K]]
    counts = np.bincount(near, minlength=10)
    return int(counts.argmax()), int(counts.max())


def _components(ink):
    """Connected dark pixels (8 neighbours): [(ys, xs)]."""
    seen = np.zeros(ink.shape, bool)
    h, w = ink.shape
    out = []
    for y0, x0 in zip(*np.nonzero(ink)):
        if seen[y0, x0]:
            continue
        stack, ys, xs = [(y0, x0)], [], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            ys.append(y)
            xs.append(x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and ink[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        out.append((np.array(ys), np.array(xs)))
    return out


def read_number(img, H, field):
    """The number written in a field of a scanned page, or None (nothing written, or not sure enough)."""
    from app import paper_scan

    inset = 1.0
    area = {"x": field["x"] + inset, "y": field["y"] + inset, "w": field["w"] - 2 * inset, "h": field["h"] - 2 * inset}
    cut = np.asarray(paper_scan.snippet(img, H, area, pad=0.0, scale=SCALE).convert("L"), dtype=np.float32)
    if cut.size == 0:
        return None
    white = float(np.percentile(cut, 95)) or 255.0
    ink = cut < white * paper_scan.DARK
    parts = [(ys, xs) for ys, xs in _components(ink) if len(xs) >= MIN_PIXELS or ys.min() > ink.shape[0] * 0.55]
    parts = [(ys, xs) for ys, xs in parts if len(xs) >= 3]
    if not parts:
        return None
    # strokes above one another belong to one digit (a 5 written in two strokes, the dot over nothing)
    boxes = sorted([[xs.min(), xs.max(), ys.min(), ys.max(), [(ys, xs)]] for ys, xs in parts], key=lambda b: b[0])
    merged = []
    for b in boxes:
        if merged:
            m = merged[-1]
            overlap = min(m[1], b[1]) - max(m[0], b[0])
            if overlap > 0.5 * min(m[1] - m[0] + 1, b[1] - b[0] + 1):
                m[0], m[1], m[2], m[3] = min(m[0], b[0]), max(m[1], b[1]), min(m[2], b[2]), max(m[3], b[3])
                m[4] += b[4]
                continue
        merged.append(b)
    height = max(m[3] - m[2] + 1 for m in merged)
    text = ""
    for x0, x1, y0, y1, pieces in merged[:MAX_DIGITS + 1]:
        if (y1 - y0 + 1) < 0.35 * height and (x1 - x0 + 1) < 0.35 * height:
            if y0 > ink.shape[0] * 0.5 and text:  # a small mark at the foot: a decimal comma or point
                text += ","
            continue
        bitmap = np.zeros((y1 - y0 + 1, x1 - x0 + 1), bool)
        for ys, xs in pieces:
            bitmap[ys - y0, xs - x0] = True
        vector = features(bitmap)
        if vector is None:
            return None
        digit, votes = classify(vector)
        if votes < K:
            return None  # not sure: no suggestion
        text += str(digit)
    text = text.strip(",")
    return text if text and len(text.replace(",", "")) <= MAX_DIGITS else None
