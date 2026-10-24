import io
import math
import os
import re
import threading
import time
from typing import Optional, Tuple

import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pypdf import PdfReader, PdfWriter, Transformation

# PyMuPDF is used only for rendering PDF pages as preview images.
# If it is not installed the app still works (preview falls back to an <iframe>).
try:
    import pymupdf as _fitz  # type: ignore
except Exception:  # pragma: no cover
    try:
        import fitz as _fitz  # type: ignore
    except Exception:
        _fitz = None

PDF_RENDER_AVAILABLE = _fitz is not None

# ── Physical sizes ──────────────────────────────────────────────────────────
DPI = 300


def mm_to_px(mm: float, dpi: int = DPI) -> int:
    return int(round(mm / 25.4 * dpi))


A4_W, A4_H = mm_to_px(210), mm_to_px(297)          # 2480 x 3508 px
NID_W_MM, NID_H_MM = 85.60, 53.98                   # ISO/IEC 7810 ID-1 (Smart NID / ID card)
NID_W, NID_H = mm_to_px(NID_W_MM), mm_to_px(NID_H_MM)  # 1011 x 638 px

# Files that must never be accepted through the "file transfer" feature
BLOCKED_EXTENSIONS = {
    "exe", "bat", "cmd", "com", "msi", "scr", "pif", "vbs", "vbe", "js", "jse",
    "wsf", "wsh", "ps1", "psm1", "jar", "dll", "sys", "lnk", "reg", "hta", "cpl",
    "apk", "app", "dmg", "sh",
}


# ── Small helpers ───────────────────────────────────────────────────────────
def safe_filename(name: Optional[str], default: str = "file") -> str:
    """Strips paths and characters that are unsafe for disks / HTML. Keeps Bengali etc."""
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name).strip(" .")
    return name[:120] or default


def get_ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def get_file_page_count(file_path: str, file_type: str) -> int:
    """Accurately calculates total printable pages for PDF or images."""
    if file_type == "pdf":
        try:
            reader = PdfReader(file_path)
            if reader.is_encrypted:
                reader.decrypt("")
            return len(reader.pages)
        except Exception as e:
            print(f"[!] Error reading PDF page count: {e}")
            return 1
    return 1


def is_valid_pdf(file_path: str) -> Tuple[bool, str]:
    try:
        reader = PdfReader(file_path)
        if reader.is_encrypted:
            if not reader.decrypt(""):
                return False, "এই PDF-এ পাসওয়ার্ড দেওয়া আছে, পাসওয়ার্ড ছাড়া ফাইল দিন।"
        if len(reader.pages) == 0:
            return False, "PDF ফাইলটিতে কোনো পাতা নেই।"
        return True, ""
    except Exception:
        return False, "PDF ফাইলটি পড়া যাচ্ছে না। ফাইলটি নষ্ট হতে পারে।"


def generate_shop_qr(shop_url: str) -> bytes:
    """Generates a high-quality QR code PNG for any URL."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(shop_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#111827", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Image helpers ───────────────────────────────────────────────────────────
def normalize_image(src_path: str, dest_path: str, max_side: int = 2400) -> None:
    """
    Opens any supported image, fixes EXIF rotation, flattens transparency,
    limits the size and saves a clean RGB JPEG. Raises ValueError if unreadable.
    """
    try:
        with Image.open(src_path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, "white")
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
            im.save(dest_path, "JPEG", quality=88, optimize=True, dpi=(DPI, DPI))
    except Exception as e:
        raise ValueError(f"ছবিটি পড়া যাচ্ছে না: {e}")


def _apply_color_mode(img: Image.Image, color_mode: str) -> Image.Image:
    if color_mode == "bw":
        img = ImageOps.grayscale(img)
        img = ImageOps.autocontrast(img, cutoff=1)
        return img.convert("RGB")
    return img.convert("RGB")


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except Exception:  # very old Pillow
        return ImageFont.load_default()


# ── NID / ID card sheet (exact size) ────────────────────────────────────────
def _prepare_card(path: str, rotation: int, fit_mode: str, color_mode: str) -> Image.Image:
    """Returns an image of EXACT card size (NID_W x NID_H px) from a photo/scan."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")

    # A card photographed in portrait orientation is turned to landscape first,
    # then the customer's manual rotation (0/90/180/270, clockwise) is applied.
    if im.height > im.width:
        im = im.rotate(90, expand=True)
    rotation = int(rotation or 0) % 360
    if rotation:
        im = im.rotate(-rotation, expand=True)

    im = _apply_color_mode(im, color_mode)

    if fit_mode == "contain":
        fitted = ImageOps.contain(im, (NID_W, NID_H), Image.Resampling.LANCZOS)
        card = Image.new("RGB", (NID_W, NID_H), "white")
        card.paste(fitted, ((NID_W - fitted.width) // 2, (NID_H - fitted.height) // 2))
    else:  # "cover" – fill the whole card, crop the overflow
        card = ImageOps.fit(im, (NID_W, NID_H), Image.Resampling.LANCZOS)
    return card


def _dashed_rect(draw: ImageDraw.ImageDraw, x1, y1, x2, y2, dash=16, color="#6b7280", width=2):
    for x in range(x1, x2, dash * 2):
        draw.line([(x, y1), (min(x + dash, x2), y1)], fill=color, width=width)
        draw.line([(x, y2), (min(x + dash, x2), y2)], fill=color, width=width)
    for y in range(y1, y2, dash * 2):
        draw.line([(x1, y), (x1, min(y + dash, y2))], fill=color, width=width)
        draw.line([(x2, y), (x2, min(y + dash, y2))], fill=color, width=width)


def build_nid_sheet(
    front_path: str,
    back_path: str,
    color_mode: str = "color",
    fit_mode: str = "cover",
    rot_front: int = 0,
    rot_back: int = 0,
    orientation: str = "portrait",
) -> Image.Image:
    """
    A4 page (300 DPI) in Portrait or Landscape with the FRONT and BACK placed at the
    exact Smart-NID / ID-1 size of 85.60 mm x 53.98 mm, with cut guides and a 50 mm
    scale ruler so the shopkeeper can verify the print is at 100 %.
    """
    front = _prepare_card(front_path, rot_front, fit_mode, color_mode)
    back = _prepare_card(back_path, rot_back, fit_mode, color_mode)
    f_title, f_label, f_small = _font(40), _font(32), _font(26)
    margin = 24

    if orientation == "landscape":
        sheet = Image.new("RGB", (A4_H, A4_W), "white")
        draw = ImageDraw.Draw(sheet)
        gap = 180
        total_w = NID_W * 2 + gap
        x_front = (A4_H - total_w) // 2
        x_back = x_front + NID_W + gap
        y_card = 420

        sheet.paste(front, (x_front, y_card))
        sheet.paste(back, (x_back, y_card))

        for cx in (x_front, x_back):
            draw.rectangle([cx, y_card, cx + NID_W - 1, y_card + NID_H - 1], outline="#6b7280", width=1)
            _dashed_rect(draw, cx - margin, y_card - margin, cx + NID_W + margin, y_card + NID_H + margin)

        draw.text((x_front, 160), f"ID / NID CARD (LANDSCAPE A4) - ACTUAL SIZE {NID_W_MM:.1f} x {NID_H_MM:.1f} mm", fill="#111827", font=f_title)
        draw.text((x_front, 225), "Print at 100% (Actual size / No scaling). Cut along the dashed lines.", fill="#4b5563", font=f_small)
        draw.text((x_front, y_card - 70), "FRONT", fill="#374151", font=f_label)
        draw.text((x_back, y_card - 70), "BACK", fill="#374151", font=f_label)

        ruler_y = y_card + NID_H + 260
        ruler_len = mm_to_px(50)
        draw.line([(x_front, ruler_y), (x_front + ruler_len, ruler_y)], fill="#111827", width=3)
        for mm in range(0, 51, 10):
            tx = x_front + mm_to_px(mm)
            draw.line([(tx, ruler_y - 14), (tx, ruler_y + 14)], fill="#111827", width=3)
        draw.text((x_front, ruler_y + 30), "Scale check: this line must measure exactly 50 mm.", fill="#4b5563", font=f_small)
        return sheet

    sheet = Image.new("RGB", (A4_W, A4_H), "white")
    draw = ImageDraw.Draw(sheet)

    x = (A4_W - NID_W) // 2
    y_front = 380
    gap = 170
    y_back = y_front + NID_H + gap

    sheet.paste(front, (x, y_front))
    sheet.paste(back, (x, y_back))

    # hairline frame INSIDE the card area (keeps the exact size)
    for y in (y_front, y_back):
        draw.rectangle([x, y, x + NID_W - 1, y + NID_H - 1], outline="#6b7280", width=1)

    draw.text((x - 24, 150), f"ID / NID CARD - ACTUAL SIZE {NID_W_MM:.1f} x {NID_H_MM:.1f} mm", fill="#111827", font=f_title)
    draw.text((x - 24, 215), "Print at 100% (Actual size / No scaling). Cut along the dashed lines.", fill="#4b5563", font=f_small)
    draw.text((x - 24, y_front - 70), "FRONT", fill="#374151", font=f_label)
    draw.text((x - 24, y_back - 70), "BACK", fill="#374151", font=f_label)

    for y in (y_front, y_back):
        _dashed_rect(draw, x - margin, y - margin, x + NID_W + margin, y + NID_H + margin)

    # 50 mm scale ruler
    ruler_y = y_back + NID_H + 260
    ruler_len = mm_to_px(50)
    draw.line([(x, ruler_y), (x + ruler_len, ruler_y)], fill="#111827", width=3)
    for mm in range(0, 51, 10):
        tx = x + mm_to_px(mm)
        draw.line([(tx, ruler_y - 14), (tx, ruler_y + 14)], fill="#111827", width=3)
    draw.text((x, ruler_y + 30), "Scale check: this line must measure exactly 50 mm.", fill="#4b5563", font=f_small)
    return sheet


def save_nid_sheet(front_path: str, back_path: str, output_path: str, **kwargs) -> str:
    """Creates the printable NID sheet (PDF keeps the exact physical size; JPG also supported)."""
    sheet = build_nid_sheet(front_path, back_path, **kwargs)
    if output_path.lower().endswith(".pdf"):
        sheet.save(output_path, "PDF", resolution=float(DPI))
    else:
        sheet.save(output_path, "JPEG", quality=95, dpi=(DPI, DPI))
    return output_path


def nid_preview_jpeg(front_path: str, back_path: str, width: int = 800, **kwargs) -> bytes:
    sheet = build_nid_sheet(front_path, back_path, **kwargs)
    h = int(sheet.height * width / sheet.width)
    sheet = sheet.resize((width, h), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=80)
    return buf.getvalue()


# ── 4x6 passport sheet ──────────────────────────────────────────────────────
def build_passport_sheet(image_path: str, count: int = 6, color_mode: str = "color") -> Image.Image:
    """4 x 6 inch sheet with passport photos (35 x 45 mm) laid out in rows – nothing is cut off."""
    import studio
    sheet, _ = studio.render_sheet(
        image_path,
        studio.sanitize_params({"paper": "4x6", "items": [{"w": 35, "h": 45, "count": count}], "bw": color_mode == "bw"}),
        dpi=DPI,
    )
    return sheet


def create_4x6_passport_sheet(image_path: str, output_path: str, count: int = 6, color_mode: str = "color") -> str:
    try:
        sheet = build_passport_sheet(image_path, count, color_mode)
        sheet.save(output_path, "JPEG", quality=95, dpi=(DPI, DPI))
        return output_path
    except Exception as e:
        print(f"[!] Error creating passport sheet: {e}")
        return image_path


# ── Preview renderers ───────────────────────────────────────────────────────
def _to_jpeg(img: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def render_image_preview(image_path: str, paper_size: str = "A4", color_mode: str = "color", orientation: str = "portrait") -> bytes:
    """What a single image will look like on the paper."""
    if paper_size == "4x6":
        sheet = build_passport_sheet(image_path, 6, color_mode)
        sheet = sheet.resize((1000, int(sheet.height * 1000 / sheet.width)), Image.Resampling.LANCZOS)
        return _to_jpeg(sheet)

    if orientation == "landscape":
        page_w, page_h = 1169, 827  # A4 Landscape @ 100 DPI
    else:
        page_w, page_h = 827, 1169  # A4 Portrait @ 100 DPI

    page = Image.new("RGB", (page_w, page_h), "white")
    with Image.open(image_path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = _apply_color_mode(im, color_mode)
        margin = 32
        fitted = ImageOps.contain(im, (page_w - 2 * margin, page_h - 2 * margin), Image.Resampling.BILINEAR)
        page.paste(fitted, ((page_w - fitted.width) // 2, (page_h - fitted.height) // 2))
    return _to_jpeg(page)


def save_a4_image_sheet(image_path: str, output_path: str, color_mode: str = "color", orientation: str = "portrait") -> str:
    """Saves an uploaded image onto a 200 DPI A4 Portrait or Landscape sheet so printing/downloading respects orientation & color mode."""
    try:
        # 200 DPI A4: 1654 x 2339 (Portrait) or 2339 x 1654 (Landscape)
        if orientation == "landscape":
            page_w, page_h = 2339, 1654
        else:
            page_w, page_h = 1654, 2339
        page = Image.new("RGB", (page_w, page_h), "white")
        with Image.open(image_path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im = _apply_color_mode(im, color_mode)
            margin = 56
            fitted = ImageOps.contain(im, (page_w - 2 * margin, page_h - 2 * margin), Image.Resampling.LANCZOS)
            page.paste(fitted, ((page_w - fitted.width) // 2, (page_h - fitted.height) // 2))
        page.save(output_path, "JPEG", quality=92, dpi=(200, 200))
        return output_path
    except Exception as e:
        print(f"[!] Error creating A4 sheet: {e}")
        return image_path


def render_pdf_page_preview(pdf_path: str, page_index: int, color_mode: str = "color", dpi: int = 100, orientation: str = "portrait") -> bytes:
    if _fitz is None:
        raise RuntimeError("PDF renderer not available")
    doc = _fitz.open(pdf_path)
    try:
        if getattr(doc, "needs_pass", False):
            raise ValueError("encrypted")
        if page_index < 0 or page_index >= len(doc):
            raise IndexError("page out of range")
        pix = doc[page_index].get_pixmap(dpi=dpi, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if orientation == "landscape" and img.height > img.width:
            img = img.rotate(90, expand=True)
    finally:
        doc.close()
    return _to_jpeg(_apply_color_mode(img, color_mode))


# ── N-up PDF (2-in-1, 4-in-1) ───────────────────────────────────────────────
_nup_lock = threading.Lock()


def create_nup_pdf(input_pdf_path: str, output_pdf_path: str, nup: int = 2) -> Tuple[str, int]:
    """
    Transforms a PDF into N-up format (2 pages per sheet or 4 pages per sheet).
    Returns (output_pdf_path, total_new_sheets).
    """
    total_orig_pages = 1
    try:
        reader = PdfReader(input_pdf_path)
        if reader.is_encrypted:
            reader.decrypt("")
        writer = PdfWriter()
        total_orig_pages = len(reader.pages)

        if nup == 2:
            out_w, out_h = 842.0, 595.0  # landscape A4
            slot_w = (out_w / 2.0) - 20.0
            slot_h = out_h - 20.0
            for sheet_idx in range(math.ceil(total_orig_pages / 2)):
                sheet = writer.add_blank_page(width=out_w, height=out_h)
                for slot in range(2):
                    idx = sheet_idx * 2 + slot
                    if idx >= total_orig_pages:
                        continue
                    p = reader.pages[idx]
                    pw, ph = float(p.mediabox.width), float(p.mediabox.height)
                    scale = min(slot_w / pw, slot_h / ph)
                    tx = slot * (out_w / 2.0) + 10.0 + (slot_w - pw * scale) / 2.0
                    ty = 10.0 + (slot_h - ph * scale) / 2.0
                    sheet.merge_transformed_page(p, Transformation().scale(scale).translate(tx, ty))

        elif nup == 4:
            out_w, out_h = 595.0, 842.0  # portrait A4
            slot_w = (out_w / 2.0) - 15.0
            slot_h = (out_h / 2.0) - 15.0
            slots = [
                (10.0, (out_h / 2.0) + 5.0),
                ((out_w / 2.0) + 5.0, (out_h / 2.0) + 5.0),
                (10.0, 10.0),
                ((out_w / 2.0) + 5.0, 10.0),
            ]
            for sheet_idx in range(math.ceil(total_orig_pages / 4)):
                sheet = writer.add_blank_page(width=out_w, height=out_h)
                for s_i, (sx, sy) in enumerate(slots):
                    idx = sheet_idx * 4 + s_i
                    if idx >= total_orig_pages:
                        continue
                    p = reader.pages[idx]
                    pw, ph = float(p.mediabox.width), float(p.mediabox.height)
                    scale = min(slot_w / pw, slot_h / ph)
                    tx = sx + (slot_w - pw * scale) / 2.0
                    ty = sy + (slot_h - ph * scale) / 2.0
                    sheet.merge_transformed_page(p, Transformation().scale(scale).translate(tx, ty))
        else:
            return input_pdf_path, total_orig_pages

        with open(output_pdf_path, "wb") as f:
            writer.write(f)
        return output_pdf_path, len(writer.pages)
    except Exception as e:
        print(f"[!] Error in create_nup_pdf: {e}")
        return input_pdf_path, total_orig_pages


def get_or_create_nup_preview(input_pdf_path: str, cache_path: str, nup: int) -> str:
    """Creates the N-up PDF once and re-uses it for every preview page request."""
    with _nup_lock:
        if not os.path.exists(cache_path):
            tmp = cache_path + ".tmp"
            out, _ = create_nup_pdf(input_pdf_path, tmp, nup)
            if out == tmp and os.path.exists(tmp):
                os.replace(tmp, cache_path)
            else:
                return input_pdf_path
    return cache_path


# ── File housekeeping ───────────────────────────────────────────────────────
def secure_delete_file(file_path: str):
    """Safely removes a file from local disk and Cloudflare R2 immediately after print completion."""
    try:
        if file_path and os.path.isfile(file_path):
            os.remove(file_path)
            print(f"[+] Securely deleted file: {file_path}")
    except Exception as e:
        print(f"[!] Failed to delete file {file_path}: {e}")
    try:
        import storage
        storage.delete_local_and_r2(file_path)
    except Exception:
        pass


def cleanup_old_files(directory: str, max_age_seconds: float, prefix: Optional[str] = None) -> int:
    """Deletes regular files older than max_age_seconds (optionally only those starting with prefix)."""
    removed = 0
    now = time.time()
    try:
        import storage as _storage
    except Exception:
        _storage = None
    try:
        for name in os.listdir(directory):
            if name.startswith("."):
                continue
            if prefix and not name.startswith(prefix):
                continue
            path = os.path.join(directory, name)
            if os.path.isfile(path) and now - os.path.getmtime(path) > max_age_seconds:
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
                if _storage:
                    try:
                        _storage.delete_local_and_r2(path)
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    return removed
