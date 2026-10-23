"""
Photo Studio engine: passport / stamp / visa / custom-size photos laid out in neat rows on
A4 (or 4x6 inch) paper, with brightness, contrast, black&white, zoom/position and
background colour change.

All sizes are in millimetres; the sheet is rendered at the requested DPI
(100 DPI for the live preview, 300 DPI for the file that is printed).
"""
import re
from typing import Dict, List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

# paper -> (width_mm, height_mm, margin_mm, gap_mm)
PAPERS = {
    "A4": (210.0, 297.0, 8.0, 2.0),
    "4x6": (152.4, 101.6, 3.0, 1.5),   # 6 x 4 inch (landscape photo paper)
}
MAX_ITEMS = 12
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def sanitize_params(raw: Dict) -> Dict:
    """Clamps every value that comes from the browser."""
    raw = raw or {}
    paper = raw.get("paper") if raw.get("paper") in PAPERS else "A4"

    items = []
    for it in (raw.get("items") or [])[:MAX_ITEMS]:
        w = _num(it.get("w"), 10, 190, 0)
        h = _num(it.get("h"), 10, 280, 0)
        count = int(_num(it.get("count"), 0, 60, 0))
        if w and h and count > 0:
            items.append({"w": w, "h": h, "count": count})
    if not items:
        items = [{"w": 45.0, "h": 55.0, "count": 6}]

    bg = raw.get("bg") or "none"
    if bg != "none" and not HEX_RE.match(str(bg)):
        bg = "none"

    return {
        "paper": paper,
        "items": items,
        "fill": bool(raw.get("fill")),                      # fill the page with the first size
        "brightness": _num(raw.get("brightness"), -60, 60, 0),
        "contrast": _num(raw.get("contrast"), -60, 60, 0),
        "bw": bool(raw.get("bw")),
        "border": raw.get("border", True) is not False,
        "bg": bg,
        "tol": _num(raw.get("tol"), 10, 100, 45),
        "zoom": _num(raw.get("zoom"), 1.0, 3.0, 1.0),
        "ox": _num(raw.get("ox"), -1.0, 1.0, 0.0),
        "oy": _num(raw.get("oy"), -1.0, 1.0, 0.0),
    }


# ── image operations ────────────────────────────────────────────────────────
def load_working(path: str, max_side: int) -> Image.Image:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return im


def _background_mask(img: Image.Image, tol: float) -> Image.Image:
    """
    Finds the plain background: colour of the image border (top + sides) is taken as the
    background colour, then every similar pixel that is CONNECTED to the border is background.
    Works best with plain / light walls. Returns an 'L' mask (255 = background).
    """
    small = img.copy()
    small.thumbnail((480, 480), Image.Resampling.BILINEAR)
    a = np.asarray(small, dtype=np.float32)
    h, w, _ = a.shape
    t = max(2, int(0.03 * min(h, w)))
    side_h = max(t, int(h * 0.7))
    border = np.concatenate([a[:t].reshape(-1, 3), a[:side_h, :t].reshape(-1, 3), a[:side_h, -t:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    similar = np.sqrt(((a - bg) ** 2).sum(axis=2)) < tol

    reach = np.zeros_like(similar)
    reach[0, :] = similar[0, :]
    reach[:side_h, 0] = similar[:side_h, 0]
    reach[:side_h, -1] = similar[:side_h, -1]
    for i in range(1500):
        grown = reach.copy()
        grown[1:] |= reach[:-1]
        grown[:-1] |= reach[1:]
        grown[:, 1:] |= reach[:, :-1]
        grown[:, :-1] |= reach[:, 1:]
        grown &= similar
        if i % 8 == 7 and np.array_equal(grown, reach):
            reach = grown
            break
        reach = grown

    mask = Image.fromarray((reach * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(1.2))
    return mask.resize(img.size, Image.Resampling.BICUBIC)


def replace_background(img: Image.Image, hex_color: str, tol: float) -> Image.Image:
    mask = _background_mask(img, tol)
    solid = Image.new("RGB", img.size, hex_color)
    return Image.composite(solid, img, mask)


def adjust(img: Image.Image, brightness: float, contrast: float, bw: bool) -> Image.Image:
    if brightness:
        img = ImageEnhance.Brightness(img).enhance(1 + brightness / 100.0)
    if contrast:
        img = ImageEnhance.Contrast(img).enhance(1 + contrast / 100.0)
    if bw:
        img = ImageOps.grayscale(img).convert("RGB")
    return img


def crop_frame(img: Image.Image, w_px: int, h_px: int, zoom: float, ox: float, oy: float) -> Image.Image:
    """Cover-crops img to the frame aspect ratio, then applies zoom and position (ox, oy in -1..1)."""
    iw, ih = img.size
    target = w_px / h_px
    if iw / ih > target:
        bh, bw = ih, ih * target
    else:
        bw, bh = iw, iw / target
    bw, bh = bw / zoom, bh / zoom
    cx = iw / 2 + ox * (iw - bw) / 2
    cy = ih / 2 + oy * (ih - bh) / 2
    left = min(max(cx - bw / 2, 0), iw - bw)
    top = min(max(cy - bh / 2, 0), ih - bh)
    box = (int(left), int(top), int(left + bw), int(top + bh))
    return img.crop(box).resize((w_px, h_px), Image.Resampling.LANCZOS)


# ── layout (shelf packing in rows) ──────────────────────────────────────────
def pack(items: List[Tuple[int, float, float]], page_w: float, page_h: float, margin: float, gap: float):
    """
    items: [(item_index, w_mm, h_mm)] — tallest first, laid out left→right in rows,
    every row centred. Returns (placements, unplaced) where placements = [(item_index, x, y, w, h)].
    """
    avail_w = page_w - 2 * margin
    bottom = page_h - margin
    rows: List[Dict] = []
    unplaced = []

    for idx, w, h in sorted(items, key=lambda t: -t[2]):
        if w > avail_w or h > bottom - margin:
            unplaced.append(idx)
            continue
        row = rows[-1] if rows else None
        if row and row["w"] + gap + w <= avail_w and h <= row["h"] + 1e-6:
            row["items"].append((idx, w, h))
            row["w"] += gap + w
            continue
        y = margin if not rows else rows[-1]["y"] + rows[-1]["h"] + gap
        if y + h <= bottom + 1e-6:
            rows.append({"y": y, "h": h, "w": w, "items": [(idx, w, h)]})
        else:
            unplaced.append(idx)

    placements = []
    for row in rows:
        x = margin + (avail_w - row["w"]) / 2
        for idx, w, h in row["items"]:
            placements.append((idx, x, row["y"], w, h))
            x += w + gap
    return placements, unplaced


def render_sheet(src_path: str, params: Dict, dpi: int = 300) -> Tuple[Image.Image, Dict]:
    """Returns (sheet image, info) where info tells how many photos of each size fit."""
    p = sanitize_params(params) if "paper" not in params or "fill" not in params else params
    page_w, page_h, margin, gap = PAPERS[p["paper"]]
    mm = lambda v: int(round(v / 25.4 * dpi))

    items = p["items"][:1] if p["fill"] else p["items"]
    expanded = []
    for i, it in enumerate(items):
        n = 400 if p["fill"] else it["count"]
        expanded += [(i, it["w"], it["h"])] * n
    placements, _ = pack(expanded, page_w, page_h, margin, gap)

    # 4x6 photo paper: try the page both ways and keep the one that holds more photos
    if p["paper"] == "4x6":
        alt, _ = pack(expanded, page_h, page_w, margin, gap)
        if len(alt) >= len(placements):
            placements, page_w, page_h = alt, page_h, page_w

    # working photo: big enough for the largest frame at this DPI (even when zoomed)
    max_side = max(700, int(dpi * 8))
    photo = load_working(src_path, max_side)
    if p["bg"] != "none":
        photo = replace_background(photo, p["bg"], p["tol"])
    photo = adjust(photo, p["brightness"], p["contrast"], p["bw"])

    sheet = Image.new("RGB", (mm(page_w), mm(page_h)), "white")
    draw = ImageDraw.Draw(sheet)
    cache: Dict[Tuple[int, int], Image.Image] = {}
    line = max(1, dpi // 150)

    for idx, x, y, w, h in placements:
        key = (mm(w), mm(h))
        if key not in cache:
            cache[key] = crop_frame(photo, key[0], key[1], p["zoom"], p["ox"], p["oy"])
        px, py = mm(x), mm(y)
        sheet.paste(cache[key], (px, py))
        if p["border"]:
            draw.rectangle([px, py, px + key[0] - 1, py + key[1] - 1], outline="#9ca3af", width=line)

    placed = [0] * len(items)
    for idx, *_ in placements:
        placed[idx] += 1
    info = {
        "placed": placed,
        "requested": [None if p["fill"] else it["count"] for it in items],
        "total": len(placements),
        "orientation": "landscape" if page_w > page_h else "portrait",
    }
    return sheet, info


def save_sheet_pdf(sheet: Image.Image, out_path: str, dpi: int = 300) -> str:
    sheet.save(out_path, "PDF", resolution=float(dpi))
    return out_path
