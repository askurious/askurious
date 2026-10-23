"""Reading scanned pages of a paper questionnaire (papierfragebogen-konzept.md E4, E7, E10).

Everything here runs on this server with numpy and Pillow; no page goes to a service. A page is found by its four
corner squares, straightened with the transform they give (mm on the sheet -> pixels of the scan; a slanted scan
or a photo from above works too), and named by the row of cells along its foot (app/paper_pdf.py: key, page and a
check sum; read in all four turns, so a page scanned upside down is found as well). Then every box and writing field
of the plan is measured: the share of dark pixels inside it, away from its printed border.

A box is "o" (empty), "x" (ticked), "f" (filled in completely: a correction, counted as not ticked, as the sheet
tells) or "?" (in between: checked by hand). A writing field is "ink" or "o".

Uploads: PDF files from a scanner (the page images inside are read with pypdf, BSD; a PDF made of text, not of
scans, has none and is refused), JPEG, PNG and TIFF (also several pages)."""
import io

import numpy as np
from PIL import Image

from app import paper_pdf as P

MAX_SIDE = 3600  # a page larger than this (more than about 300 dpi) is made smaller before reading and storing
MIN_SIDE = 1200  # narrower than about 145 dpi: the boxes are too small to read
DARK = 0.62  # a pixel darker than this share of the paper's white counts as ink
LIGHT = 0.82  # ... and darker than this as faint ink: a box that only has faint ink is uncertain, never empty
FAINT = 0.06  # share of faint ink in a box from which it is looked at
# shares of ink inside a box (away from its border): below EMPTY empty, from TICK ticked, from FILLED filled in
EMPTY, TICK, FULL, FILLED = 0.035, 0.08, 0.85, 0.93
INK = 0.006  # share of ink inside a writing field from which something is written in it
INSET_BOX, INSET_WRITE = 0.8, 1.0  # mm kept away from the printed border


class Unreadable(Exception):
    """A file or a page that cannot be read; `code` says why (for the page)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# A file of a few kilobytes can hold a page of 13000 x 13000 pixels (one colour compresses to nothing; audit
# 2026-10-10, P-S1: 1.9 GB to read a 20 KB PNG). So the size of every page is read from its header before it is
# decoded, as app/media.py (open_image) does for uploaded images: an A4 page at 600 dpi has some 35 megapixels.
MAX_PIXELS = 40_000_000
FORMATS = ("JPEG", "PNG", "TIFF")  # nothing else goes to Pillow's decoders (P-S5)
_EXIF_TURN = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180, 4: Image.Transpose.FLIP_TOP_BOTTOM,
              5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
              8: Image.Transpose.ROTATE_90}


def is_pdf(data):
    return data[:5] == b"%PDF-"


def pages_from_upload(name, data, limit):
    """The pages of an uploaded file, one after another: (grey PIL image, None) or (None, why) for a page that cannot
    be read ("file", "pixels"). Every page is decoded only when it is asked for and made small at once, so no more than
    one page at full size is held; at most `limit` pages. Unreadable("file") if the file is none of the formats."""
    if limit <= 0:
        return
    if is_pdf(data):
        yield from _pdf_pages(data, limit)
        return
    if (name or "").lower().endswith(".pdf"):
        raise Unreadable("file")
    try:
        img = Image.open(io.BytesIO(data), formats=FORMATS)
    except Exception as e:  # noqa: BLE001 - not an image of the formats, or Pillow's own limit
        raise Unreadable("file") from e
    for k in range(limit):
        if k:
            try:
                img.seek(k)  # the next frame of a TIFF: its header only
            except EOFError:
                return
            except Exception:  # noqa: BLE001 - a broken frame ends the file
                yield None, "file"
                return
        yield _page(img)


def _page(img):
    try:
        w, h = img.size
        if w * h > MAX_PIXELS:
            return None, "pixels"
        return _grey(img), None
    except Exception:  # noqa: BLE001 - a page that cannot be decoded
        return None, "file"


def _pdf_pages(data, limit):
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages
        count = len(pages)
    except Exception as e:  # noqa: BLE001 - not a PDF pypdf can read
        raise Unreadable("file") from e
    last = (None, None)  # the image XObject read last and its result: pages that show the same scan read it once
    forms = {}  # the forms walked already, over all pages: a form every page shows is walked once (J5)
    for k in range(min(count, limit)):
        try:
            xobj = _largest_image(pages[k], forms=forms)
        except Exception:  # noqa: BLE001
            xobj = None
        if xobj is None:
            yield None, "file"  # a page without a scan
            continue
        ref = getattr(xobj.indirect_reference, "idnum", None) if xobj.indirect_reference is not None else None
        if ref is not None and last[0] == ref:
            yield last[1]
            continue
        result = _pdf_image(xobj)
        last = (ref, result)
        yield result


def _image_size(xobj):
    try:
        return int(xobj["/Width"]), int(xobj["/Height"])
    except (KeyError, TypeError, ValueError):
        return 0, 0


def _largest_image(page, depth=0, seen=None, forms=None):
    """The largest image XObject a page (or a form on it, three levels deep) holds, by the size its dictionary names;
    nothing is decoded here. forms: {object number: its largest image} of the forms walked on earlier pages of the
    file, so a form shared by every page (or nested ones) is not walked again on each (code review 2026-10-15, J5)."""
    seen = set() if seen is None else seen
    forms = {} if forms is None else forms
    try:
        xobjects = page["/Resources"].get_object()["/XObject"].get_object()
    except (KeyError, AttributeError, TypeError):
        return None
    best, best_px = None, 0
    for key in list(xobjects.keys())[:200]:
        obj = xobjects[key].get_object()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        sub = obj.get("/Subtype")
        if sub == "/Image":
            w, h = _image_size(obj)
            if w * h > best_px:
                best, best_px = obj, w * h
        elif sub == "/Form" and depth < 3:
            ref = getattr(getattr(obj, "indirect_reference", None), "idnum", None)
            if ref is not None and ref in forms:
                inner = forms[ref]
            else:
                inner = _largest_image(obj, depth + 1, seen, forms)
                if ref is not None:
                    forms[ref] = inner
            if inner is not None and _image_size(inner)[0] * _image_size(inner)[1] > best_px:
                best = inner
                best_px = _image_size(inner)[0] * _image_size(inner)[1]
    return best


def _pdf_image(xobj):
    """One image XObject of a PDF page, checked before it is decoded: its own size, the size its JPEG names (it may
    differ from the dictionary's) and the size of its mask."""
    w, h = _image_size(xobj)
    if not w or not h:
        return None, "file"
    if w * h > MAX_PIXELS:
        return None, "pixels"
    try:
        mask = xobj.get("/SMask")
        if mask is not None:
            mw, mh = _image_size(mask.get_object())
            if mw * mh > MAX_PIXELS:
                return None, "pixels"
        filters = xobj.get("/Filter")
        filters = [filters] if isinstance(filters, str) else list(filters or [])
        if filters and filters[-1] in ("/DCTDecode", "/JPXDecode"):
            inner = Image.open(io.BytesIO(xobj.get_data()), formats=("JPEG", "JPEG2000"))
            if inner.size[0] * inner.size[1] > MAX_PIXELS:
                return None, "pixels"
        img = xobj.decode_as_image()
    except Exception:  # noqa: BLE001 - an image pypdf cannot decode
        return None, "file"
    return _page(img)


def _grey(img):
    """A page as a grey image of at most MAX_SIDE pixels a side. A JPEG is decoded at a smaller scale where it can
    be; transparency is laid on white without an RGBA copy of the page."""
    try:
        turn = _EXIF_TURN.get(img.getexif().get(0x0112))
    except Exception:  # noqa: BLE001
        turn = None
    if img.format == "JPEG" and max(img.size) > MAX_SIDE:
        img.draft("L", (img.width * MAX_SIDE // max(img.size), img.height * MAX_SIDE // max(img.size)))
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        grey, alpha = img.convert("LA").split()
        back = Image.new("L", img.size, 255)
        back.paste(grey, mask=alpha)
        img = back
    else:
        img = img.convert("L")
    side = max(img.size)
    if side > MAX_SIDE:
        f = MAX_SIDE / side
        img = img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))), Image.LANCZOS)
    if turn is not None:
        img = img.transpose(turn)
    return img


# --- finding the page ----------------------------------------------------------------------------


def _dark(img, level=DARK):
    a = np.asarray(img, dtype=np.float32)
    white = float(np.percentile(a, 95)) or 255.0
    return a < white * level


def _mark_in(dark, x0, y0, x1, y1, k):
    """The centre (x, y) of the darkest k x k square in a region, refined to the centre of its ink, or None."""
    region = dark[y0:y1, x0:x1].astype(np.float32)
    if region.shape[0] <= k or region.shape[1] <= k:
        return None
    ii = np.pad(region.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    sums = ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]
    best = np.unravel_index(int(np.argmax(sums)), sums.shape)
    if sums[best] < 0.75 * k * k:
        return None
    cy, cx = best[0] + k / 2, best[1] + k / 2
    r = int(k * 0.7)
    ya, yb = max(0, int(cy - r)), min(region.shape[0], int(cy + r) + 1)
    xa, xb = max(0, int(cx - r)), min(region.shape[1], int(cx + r) + 1)
    ys, xs = np.nonzero(region[ya:yb, xa:xb])
    if len(xs):
        cx, cy = xa + xs.mean() + 0.5, ya + ys.mean() + 0.5
    return x0 + cx, y0 + cy


def homography(src, dst):
    """The 3x3 transform that takes the four points `src` to `dst`."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs += [u, v]
    h = np.linalg.solve(np.array(rows, dtype=float), np.array(rhs, dtype=float))
    return np.append(h, 1.0).reshape(3, 3)


def apply(H, xs, ys):
    """Pixels for points in mm (arrays)."""
    xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    d = H[2, 0] * xs + H[2, 1] * ys + H[2, 2]
    return (H[0, 0] * xs + H[0, 1] * ys + H[0, 2]) / d, (H[1, 0] * xs + H[1, 1] * ys + H[1, 2]) / d


def _ink_share(dark, H, x, y, w, h, inset):
    """Share of dark pixels inside the rectangle (mm), `inset` mm away from its edges: sampled every quarter of a
    millimetre through the page's transform, so a slanted scan keeps the printed border out."""
    x0, y0, x1, y1 = x + inset, y + inset, x + w - inset, y + h - inset
    if x1 <= x0 or y1 <= y0:
        return 0.0
    step = max(0.25, (x1 - x0) / 600, (y1 - y0) / 200)
    gx, gy = np.meshgrid(np.arange(x0, x1 + 1e-9, step), np.arange(y0, y1 + 1e-9, step))
    px, py = apply(H, gx.ravel(), gy.ravel())
    hgt, wid = dark.shape
    px = np.clip(np.round(px).astype(int), 0, wid - 1)
    py = np.clip(np.round(py).astype(int), 0, hgt - 1)
    return float(dark[py, px].mean())


def _bits(dark, H):
    bits = []
    for x, y, w, h in P.code_cells():
        bits.append(1 if _ink_share(dark, H, x, y, w, h, 0.5) > 0.5 else 0)
    return bits


def locate(img):
    """Find the page: {"key", "page", "H" (mm -> px, as lists)} or raise Unreadable("small" | "marks" | "code")."""
    if min(img.size) < MIN_SIDE:
        raise Unreadable("small")
    dark = _dark(img)
    hgt, wid = dark.shape
    ppm = max(wid, hgt) / P.PAGE_H  # pixels per mm, the page filling the scan
    k = max(5, int(round(P.MARK * ppm)))
    reach = int(45 * ppm)
    found = [_mark_in(dark, 0, 0, reach, reach, k), _mark_in(dark, wid - reach, 0, wid, reach, k),
             _mark_in(dark, wid - reach, hgt - reach, wid, hgt, k), _mark_in(dark, 0, hgt - reach, reach, hgt, k)]
    if any(f is None for f in found):
        raise Unreadable("marks")
    page = [P.MARKS[0], P.MARKS[1], P.MARKS[3], P.MARKS[2]]  # clockwise: top left, top right, bottom right, left
    for turn in range(4):
        corners = page[turn:] + page[:turn]  # the sheet turned by `turn` quarters in the scan
        try:
            H = homography(corners, found)
        except np.linalg.LinAlgError:
            continue
        decoded = P.decode_bits(_bits(dark, H))
        if decoded:
            key, number = decoded
            return {"key": key, "page": number, "H": H.tolist(), "turn": turn}
    raise Unreadable("code")


def measure(img, H, fields):
    """{index: share of ink} for the fields (index, field) of this page; a box with almost no ink but some faint ink
    as [share, faint share]."""
    dark, light = _dark(img), _dark(img, LIGHT)
    H = np.asarray(H, dtype=float)
    out = {}
    for index, f in fields:
        if f["k"] == "box":
            share = round(_ink_share(dark, H, f["x"], f["y"], f["w"], f["h"], INSET_BOX), 3)
            if share < EMPTY:
                faint = round(_ink_share(light, H, f["x"], f["y"], f["w"], f["h"], INSET_BOX), 3)
                out[str(index)] = [share, faint] if faint >= FAINT else share
            else:
                out[str(index)] = share
        else:
            share = _ink_share(dark, H, f["x"], f["y"], f["w"], f["h"], INSET_WRITE)
            if share < INK:  # faint writing counts as writing: it is typed in by hand anyway
                share = max(share, _ink_share(light, H, f["x"], f["y"], f["w"], f["h"], INSET_WRITE) / 2)
            out[str(index)] = round(share, 4)
    return out


def state(field, share):
    """What a measured field says: "o", "x", "f", "?" for a box; "ink" or "o" for a writing field."""
    if share is None:
        return "o"
    if isinstance(share, list):  # only faint ink in a box: looked at by hand
        return "?"
    if field["k"] != "box":
        return "ink" if share >= INK else "o"
    if share < EMPTY:
        return "o"
    if share < TICK:
        return "?"
    if share < FULL:
        return "x"
    if share < FILLED:
        return "?"
    return "f"


def snippet(img, H, field, pad=2.0, scale=6.0):
    """The field cut out of the page and straightened, `pad` mm around it, `scale` pixels per mm."""
    H = np.asarray(H, dtype=float)
    x0, y0 = field["x"] - pad, field["y"] - pad
    x1, y1 = field["x"] + field["w"] + pad, field["y"] + field["h"] + pad
    px, py = apply(H, [x0, x0, x1, x1], [y0, y1, y1, y0])
    size = (max(1, int((x1 - x0) * scale)), max(1, int((y1 - y0) * scale)))
    quad = (px[0], py[0], px[1], py[1], px[2], py[2], px[3], py[3])
    return img.transform(size, Image.QUAD, quad, Image.BILINEAR, fillcolor=255)
