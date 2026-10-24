"""
Enhanced Image Processing Engine:
1. CamScanner-style enhancement (Magic Color, B&W Document, Clean White Flattening)
2. Advanced Background Removal & Replacement (Remove.bg style)
3. Formal Suit/Dress overlay for passport photos
4. Individual Crop & Perspective adjustments
"""
import io
import os
import re
from typing import Optional, Tuple, Dict, List
import numpy as np
from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageDraw

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SUITS_DIR = os.path.join(STATIC_DIR, "suits")

def camscanner_process(img: Image.Image, mode: str = "magic_color", contrast: float = 1.3, brightness: float = 1.1) -> Image.Image:
    """
    CamScanner document enhancement algorithm:
    - Eliminates shadow and uneven yellow/dark background
    - Sharpens text and document borders for high-clarity printing
    """
    img = img.convert("RGB")
    w, h = img.size

    if mode in ("magic_color", "bw_doc"):
        # Fast illumination estimation: downscale 4x before GaussianBlur, then upscale back (10x faster)
        gray = ImageOps.grayscale(img)
        gray_arr = np.array(gray, dtype=np.float32)
        small_w, small_h = max(32, w // 4), max(32, h // 4)
        gray_small = gray.resize((small_w, small_h), Image.Resampling.BILINEAR)
        small_radius = max(4, min(small_w, small_h) // 22)
        bg_small = gray_small.filter(ImageFilter.GaussianBlur(radius=small_radius))
        bg = np.array(bg_small.resize((w, h), Image.Resampling.BILINEAR), dtype=np.float32)

        if mode == "magic_color":
            flattened = np.clip((gray_arr / (bg + 1.0)) * 255.0, 0, 255)
            arr = np.array(img, dtype=np.float32)
            norm_factor = (flattened / (gray_arr + 1.0))[:, :, np.newaxis]
            enhanced = np.clip(arr * norm_factor, 0, 255).astype(np.uint8)
            res = Image.fromarray(enhanced, mode="RGB")
            res = ImageEnhance.Sharpness(res).enhance(1.6)
            res = ImageEnhance.Contrast(res).enhance(contrast)
            res = ImageEnhance.Brightness(res).enhance(brightness)
            return res
        else:
            flattened = np.clip((gray_arr / (bg + 1.0)) * 255.0, 0, 255).astype(np.uint8)
            res = Image.fromarray(flattened, mode="L")
            res = ImageOps.autocontrast(res, cutoff=(4, 4))
            res = ImageEnhance.Sharpness(res).enhance(2.0)
            return res.convert("RGB")

    elif mode == "grayscale":
        gray = ImageOps.grayscale(img)
        res = ImageOps.autocontrast(gray, cutoff=(2, 2))
        return ImageEnhance.Sharpness(res).enhance(1.4).convert("RGB")

    return img


def advanced_remove_background(img: Image.Image, tol: float = 35.0) -> Tuple[Image.Image, Image.Image]:
    """
    Remove.bg style foreground segmentation:
    Returns (rgba_image_with_transparent_bg, alpha_mask)
    """
    small = img.copy()
    small.thumbnail((600, 600), Image.Resampling.BILINEAR)
    arr = np.asarray(small.convert("RGB"), dtype=np.float32)
    h, w, _ = arr.shape

    # Sample borders & corners as background
    t = max(2, int(0.04 * min(h, w)))
    side_h = max(t, int(h * 0.75))
    border = np.concatenate([
        arr[:t].reshape(-1, 3),
        arr[:side_h, :t].reshape(-1, 3),
        arr[:side_h, -t:].reshape(-1, 3)
    ])
    bg_median = np.median(border, axis=0)
    bg_std = np.std(border, axis=0) + 10.0

    # Color distance
    dist = np.sqrt(np.sum(((arr - bg_median) / bg_std) ** 2, axis=2))
    bg_seed = dist < (tol / 18.0)

    # Flood grow from top/sides
    reach = np.zeros_like(bg_seed)
    reach[0, :] = bg_seed[0, :]
    reach[:side_h, 0] = bg_seed[:side_h, 0]
    reach[:side_h, -1] = bg_seed[:side_h, -1]

    for i in range(1200):
        grown = reach.copy()
        grown[1:] |= reach[:-1]
        grown[:-1] |= reach[1:]
        grown[:, 1:] |= reach[:, :-1]
        grown[:, :-1] |= reach[:, 1:]
        grown &= bg_seed
        if i % 8 == 7 and np.array_equal(grown, reach):
            reach = grown
            break
        reach = grown

    fg_mask = (~reach).astype(np.uint8) * 255
    mask_im = Image.fromarray(fg_mask, mode="L").filter(ImageFilter.GaussianBlur(1.2))
    mask_full = mask_im.resize(img.size, Image.Resampling.BICUBIC)

    rgba = img.convert("RGBA")
    rgba.putalpha(mask_full)
    return rgba, mask_full


def apply_dress_overlay(
    photo: Image.Image,
    suit_id: str,
    suit_zoom: float = 1.0,
    suit_ox: float = 0.0,
    suit_oy: float = 0.0
) -> Image.Image:
    """
    Overlays a formal suit/coat/blazer onto a passport photo at exact neck/shoulder alignment.
    suit_id: 'suit1', 'suit2', 'suit3', 'suit4'
    """
    svg_path = os.path.join(SUITS_DIR, f"{suit_id}.svg")
    if not os.path.exists(svg_path):
        return photo

    # We can rasterize SVG or load PNG/Pillow drawings
    # Since Cairo/librsvg may not be on Windows, we convert/render via Pillow directly:
    try:
        suit_img = render_suit_pillow(suit_id, photo.size)
    except Exception as e:
        print(f"[Dress] Error rendering suit: {e}")
        return photo

    # Scale and translate suit
    pw, ph = photo.size
    sw = int(pw * 1.05 * suit_zoom)
    sh = int(ph * 0.9 * suit_zoom)
    suit_resized = suit_img.resize((max(10, sw), max(10, sh)), Image.Resampling.LANCZOS)

    # Base position: anchor at bottom center
    sx = int((pw - sw) / 2 + suit_ox * (pw / 2))
    sy = int(ph * 0.45 + suit_oy * (ph / 2))

    res = photo.copy().convert("RGBA")
    res.paste(suit_resized, (sx, sy), suit_resized)
    return res.convert("RGB")


def render_suit_pillow(suit_id: str, target_size: Tuple[int, int]) -> Image.Image:
    """Procedurally renders high-quality crisp suits directly with Pillow (independent of system fonts/cairo)."""
    w, h = 400, 400
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    if suit_id == "suit2":  # Navy Blue Suit + Gold Tie
        # Shirt
        draw.polygon([(150, 110), (250, 110), (215, 280), (185, 280)], fill="#e0f2fe", outline="#93c5fd")
        draw.polygon([(150, 110), (185, 150), (160, 180)], fill="#f0f9ff", outline="#bae6fd")
        draw.polygon([(250, 110), (215, 150), (240, 180)], fill="#f0f9ff", outline="#bae6fd")
        # Tie
        draw.polygon([(190, 140), (210, 140), (216, 165), (184, 165)], fill="#f59e0b")
        draw.polygon([(186, 165), (214, 165), (220, 340), (200, 375), (180, 340)], fill="#d97706", outline="#78350f")
        # Coat
        draw.polygon([(150, 110), (40, 165), (10, 400), (390, 400), (360, 165), (250, 110), (225, 230), (200, 280), (175, 230)], fill="#1e3a8a")
        # Lapels
        draw.polygon([(150, 110), (90, 180), (165, 225), (180, 270), (200, 280), (190, 240), (160, 170)], fill="#1e40af", outline="#172554")
        draw.polygon([(250, 110), (310, 180), (235, 225), (220, 270), (200, 280), (210, 240), (240, 170)], fill="#1e40af", outline="#172554")
    elif suit_id == "suit3":  # Formal Crisp White Shirt
        draw.polygon([(140, 105), (45, 170), (15, 400), (385, 400), (355, 170), (260, 105), (225, 230), (175, 230)], fill="#ffffff", outline="#cbd5e1")
        draw.rectangle([(188, 160), (212, 400)], fill="#f8fafc", outline="#cbd5e1")
        for cy in (205, 265, 325, 385):
            draw.ellipse([(196, cy - 4), (204, cy + 4)], fill="#ffffff", outline="#94a3b8")
        draw.polygon([(140, 105), (190, 165), (175, 185), (130, 120)], fill="#ffffff", outline="#cbd5e1")
        draw.polygon([(260, 105), (210, 165), (225, 185), (270, 120)], fill="#ffffff", outline="#cbd5e1")
    elif suit_id == "suit4":  # Charcoal Executive Blazer
        draw.polygon([(145, 115), (255, 115), (210, 260), (190, 260)], fill="#f8fafc", outline="#e2e8f0")
        draw.polygon([(145, 115), (45, 165), (20, 400), (380, 400), (355, 165), (255, 115), (220, 240), (180, 240)], fill="#1e293b")
        draw.polygon([(145, 115), (95, 185), (170, 225), (185, 280), (200, 290), (190, 245), (160, 175)], fill="#334155", outline="#0f172a")
        draw.polygon([(255, 115), (305, 185), (230, 225), (215, 280), (200, 290), (210, 245), (240, 175)], fill="#334155", outline="#0f172a")
    else:  # suit1: Classic Black Tuxedo + Red Tie
        draw.polygon([(150, 110), (250, 110), (215, 280), (185, 280)], fill="#f8fafc", outline="#cbd5e1")
        draw.polygon([(150, 110), (185, 150), (160, 180)], fill="#ffffff", outline="#cbd5e1")
        draw.polygon([(250, 110), (215, 150), (240, 180)], fill="#ffffff", outline="#cbd5e1")
        draw.polygon([(190, 140), (210, 140), (216, 165), (184, 165)], fill="#b91c1c")
        draw.polygon([(186, 165), (214, 165), (220, 340), (200, 375), (180, 340)], fill="#991b1b", outline="#450a0a")
        draw.polygon([(150, 110), (40, 165), (10, 400), (390, 400), (360, 165), (250, 110), (225, 230), (200, 280), (175, 230)], fill="#0f172a")
        draw.polygon([(150, 110), (90, 180), (165, 225), (180, 270), (200, 280), (190, 240), (160, 170)], fill="#1e293b", outline="#020617")
        draw.polygon([(250, 110), (310, 180), (235, 225), (220, 270), (200, 280), (210, 240), (240, 170)], fill="#1e293b", outline="#020617")
        draw.rectangle([(75, 230), (110, 234)], fill="#020617")
        draw.polygon([(80, 230), (90, 218), (100, 230)], fill="#ffffff")

    return im


def order_quad_points(pts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Orders 4 points into [Top-Left, Top-Right, Bottom-Right, Bottom-Left]."""
    pts_sorted_y = sorted(pts, key=lambda p: (p[1], p[0]))
    top_two = sorted(pts_sorted_y[:2], key=lambda p: p[0])
    bot_two = sorted(pts_sorted_y[2:], key=lambda p: p[0])
    tl, tr = top_two[0], top_two[1]
    bl, br = bot_two[0], bot_two[1]
    return [tl, tr, br, bl]


def perspective_crop_4points(
    img: Image.Image,
    norm_points: List[Tuple[float, float]],
    target_ratio: Optional[float] = None,
    max_out_dim: Optional[int] = None
) -> Image.Image:
    """
    CamScanner-style 4-point perspective warp (homography transform):
    Takes 4 normalized corner coordinates (0..1) of an uneven/skewed document or card
    and warps the quadrilateral into a flat, straight rectangle.
    """
    w, h = img.size
    pixel_pts = [
        (max(0.0, min(float(w), float(px) * w)), max(0.0, min(float(h), float(py) * h)))
        for px, py in norm_points[:4]
    ]
    tl, tr, br, bl = order_quad_points(pixel_pts)

    w_top = float(np.hypot(tr[0] - tl[0], tr[1] - tl[1]))
    w_bot = float(np.hypot(br[0] - bl[0], br[1] - bl[1]))
    h_left = float(np.hypot(bl[0] - tl[0], bl[1] - tl[1]))
    h_right = float(np.hypot(br[0] - tr[0], br[1] - tr[1]))

    out_w = max(24, int(round(max(w_top, w_bot))))
    out_h = max(24, int(round(max(h_left, h_right))))

    if target_ratio and target_ratio > 0:
        out_h = max(24, int(round(out_w / target_ratio)))

    if max_out_dim and max(out_w, out_h) > max_out_dim:
        scale = float(max_out_dim) / float(max(out_w, out_h))
        out_w = max(24, int(round(out_w * scale)))
        out_h = max(24, int(round(out_h * scale)))

    dst = [
        (0.0, 0.0),
        (float(out_w), 0.0),
        (float(out_w), float(out_h)),
        (0.0, float(out_h))
    ]
    src = [tl, tr, br, bl]

    matrix = []
    rhs = []
    for (X, Y), (x, y) in zip(dst, src):
        matrix.append([X, Y, 1.0, 0.0, 0.0, 0.0, -X * x, -Y * x])
        matrix.append([0.0, 0.0, 0.0, X, Y, 1.0, -X * y, -Y * y])
        rhs.append(x)
        rhs.append(y)

    coeffs = np.linalg.solve(np.array(matrix, dtype=np.float64), np.array(rhs, dtype=np.float64)).tolist()
    resample = Image.Resampling.BILINEAR if max_out_dim else Image.Resampling.BICUBIC
    warped = img.transform((out_w, out_h), Image.Transform.PERSPECTIVE, coeffs, resample)
    return warped


def detect_document_corners(img: Image.Image) -> List[Dict[str, float]]:
    """
    Automatically estimates the 4 corners of a document or card against its background.
    Returns 4 normalized points [{"x": .., "y": ..}, ...] in [TL, TR, BR, BL] order.
    """
    default_pts = [
        {"x": 0.07, "y": 0.07},
        {"x": 0.93, "y": 0.07},
        {"x": 0.93, "y": 0.93},
        {"x": 0.07, "y": 0.93}
    ]
    try:
        small = img.copy().convert("RGB")
        small.thumbnail((320, 320), Image.Resampling.BILINEAR)
        arr = np.asarray(small, dtype=np.float32)
        sh, sw, _ = arr.shape
        if sh < 30 or sw < 30:
            return default_pts

        t = max(2, int(0.04 * min(sh, sw)))
        border = np.concatenate([
            arr[:t, :, :].reshape(-1, 3),
            arr[-t:, :, :].reshape(-1, 3),
            arr[:, :t, :].reshape(-1, 3),
            arr[:, -t:, :].reshape(-1, 3)
        ])
        bg_med = np.median(border, axis=0)
        bg_std = np.std(border, axis=0) + 12.0

        dist = np.sqrt(np.sum(((arr - bg_med) / bg_std) ** 2, axis=2))
        mask = dist > 1.85

        fg_ratio = float(np.mean(mask))
        if fg_ratio < 0.08 or fg_ratio > 0.94:
            return default_pts

        ys, xs = np.where(mask)
        if len(xs) < 50:
            return default_pts

        nx = xs.astype(np.float32) / float(sw - 1)
        ny = ys.astype(np.float32) / float(sh - 1)

        # Find extreme points along diagonal axes (x+y and x-y)
        s = nx + ny
        d = nx - ny

        idx_tl = int(np.argmin(s))
        idx_br = int(np.argmax(s))
        idx_tr = int(np.argmax(d))
        idx_bl = int(np.argmin(d))

        pts = [
            (float(np.clip(nx[idx_tl], 0.02, 0.98)), float(np.clip(ny[idx_tl], 0.02, 0.98))),
            (float(np.clip(nx[idx_tr], 0.02, 0.98)), float(np.clip(ny[idx_tr], 0.02, 0.98))),
            (float(np.clip(nx[idx_br], 0.02, 0.98)), float(np.clip(ny[idx_br], 0.02, 0.98))),
            (float(np.clip(nx[idx_bl], 0.02, 0.98)), float(np.clip(ny[idx_bl], 0.02, 0.98)))
        ]
        ordered = order_quad_points(pts)
        return [{"x": round(p[0], 4), "y": round(p[1], 4)} for p in ordered]
    except Exception:
        return default_pts
