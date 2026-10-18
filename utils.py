import os
import io
import math
import qrcode
from PIL import Image, ImageOps, ImageDraw
from pypdf import PdfReader, PdfWriter, Transformation
from typing import Tuple, Optional

def get_file_page_count(file_path: str, file_type: str) -> int:
    """Accurately calculates total printable pages for PDF or images."""
    if file_type == "pdf":
        try:
            reader = PdfReader(file_path)
            return len(reader.pages)
        except Exception as e:
            print(f"[!] Error reading PDF page count: {e}")
            return 1
    elif file_type in ["image", "jpg", "jpeg", "png", "webp"]:
        return 1
    return 1

def generate_shop_qr(shop_url: str) -> bytes:
    """Generates a high-quality QR code PNG for the shop URL."""
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

def create_4x6_passport_sheet(image_path: str, output_path: str, count: int = 6) -> str:
    """
    Creates a 4x6 inch (1200x1800 px at 300 DPI) photo sheet with 
    passport size photos (typically 1.5x2 inches) with cut lines.
    """
    try:
        sheet_w, sheet_h = 1800, 1200
        sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
        photo_w, photo_h = 450, 600
        
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            img = ImageOps.fit(img, (photo_w, photo_h), Image.Resampling.LANCZOS)
            bordered = ImageOps.expand(img, border=2, fill="#e5e7eb")
            
            start_x = 180
            start_y = 60
            gap_x = 80
            gap_y = 60
            
            for i in range(count):
                row = i // 3
                col = i % 3
                x = start_x + col * (photo_w + gap_x)
                y = start_y + row * (photo_h + gap_y)
                sheet.paste(bordered, (x, y))
                
        sheet.save(output_path, "JPEG", quality=95)
        return output_path
    except Exception as e:
        print(f"[!] Error creating passport sheet: {e}")
        return image_path

def create_nid_card_sheet(front_img_path: str, back_img_path: str, output_path: str, layout: str = "vertical") -> str:
    """
    Combines Front and Back of Bangladeshi NID / Smart Card onto a single A4 page (300 DPI)
    Standard NID size: 85.6mm x 54mm (approx 1010 x 638 px at 300 DPI).
    Places Front on top, Back on bottom with cutting border and dashed lines.
    """
    try:
        # A4 at 300 DPI: 2480 x 3508 pixels
        page_w, page_h = 2480, 3508
        sheet = Image.new("RGB", (page_w, page_h), "white")
        draw = ImageDraw.Draw(sheet)
        
        nid_w, nid_h = 1011, 638
        
        # Load & resize front image
        with Image.open(front_img_path) as f_img:
            f_img = ImageOps.exif_transpose(f_img)
            front_resized = ImageOps.fit(f_img, (nid_w, nid_h), Image.Resampling.LANCZOS)
            front_bordered = ImageOps.expand(front_resized, border=2, fill="#9ca3af")
            
        # Load & resize back image
        with Image.open(back_img_path) as b_img:
            b_img = ImageOps.exif_transpose(b_img)
            back_resized = ImageOps.fit(b_img, (nid_w, nid_h), Image.Resampling.LANCZOS)
            back_bordered = ImageOps.expand(back_resized, border=2, fill="#9ca3af")
            
        if layout == "horizontal":
            # Side by side layout
            gap = 140
            total_w = nid_w * 2 + gap
            start_x = (page_w - total_w) // 2
            start_y = 600
            
            sheet.paste(front_bordered, (start_x, start_y))
            sheet.paste(back_bordered, (start_x + nid_w + gap, start_y))
            
            # Draw cutting guides
            draw.text((start_x, start_y - 50), "FRONT (সামনে)", fill="#4b5563")
            draw.text((start_x + nid_w + gap, start_y - 50), "BACK (পেছনে)", fill="#4b5563")
        else:
            # Standard vertical layout (top & bottom - most common for photocopying in BD)
            gap = 180
            start_x = (page_w - nid_w) // 2
            start_y = 600
            
            sheet.paste(front_bordered, (start_x, start_y))
            sheet.paste(back_bordered, (start_x, start_y + nid_h + gap))
            
            # Label & guidelines
            draw.text((start_x, start_y - 50), "NID FRONT (সামনের পিঠ)", fill="#4b5563")
            draw.text((start_x, start_y + nid_h + gap - 50), "NID BACK (পেছনের পিঠ)", fill="#4b5563")
            
        sheet.save(output_path, "JPEG", quality=95)
        return output_path
    except Exception as e:
        print(f"[!] Error creating NID sheet: {e}")
        return front_img_path

def create_nup_pdf(input_pdf_path: str, output_pdf_path: str, nup: int = 2) -> Tuple[str, int]:
    """
    Transforms a PDF into N-up format (2 pages per sheet or 4 pages per sheet).
    Returns (output_pdf_path, total_new_sheets).
    """
    try:
        reader = PdfReader(input_pdf_path)
        writer = PdfWriter()
        total_orig_pages = len(reader.pages)
        
        if nup == 2:
            # 2 in 1: Output is Landscape A4 (842 x 595 points)
            out_w, out_h = 842.0, 595.0
            slot_w = (out_w / 2.0) - 20.0
            slot_h = out_h - 20.0
            
            num_sheets = math.ceil(total_orig_pages / 2)
            for sheet_idx in range(num_sheets):
                sheet = writer.add_blank_page(width=out_w, height=out_h)
                
                # Slot 1 (Left)
                idx1 = sheet_idx * 2
                if idx1 < total_orig_pages:
                    p1 = reader.pages[idx1]
                    pw, ph = float(p1.mediabox.width), float(p1.mediabox.height)
                    scale = min(slot_w / pw, slot_h / ph)
                    tx = 10.0 + (slot_w - pw * scale) / 2.0
                    ty = 10.0 + (slot_h - ph * scale) / 2.0
                    sheet.merge_transformed_page(p1, Transformation().scale(scale).translate(tx, ty))
                    
                # Slot 2 (Right)
                idx2 = idx1 + 1
                if idx2 < total_orig_pages:
                    p2 = reader.pages[idx2]
                    pw, ph = float(p2.mediabox.width), float(p2.mediabox.height)
                    scale = min(slot_w / pw, slot_h / ph)
                    tx = (out_w / 2.0) + 10.0 + (slot_w - pw * scale) / 2.0
                    ty = 10.0 + (slot_h - ph * scale) / 2.0
                    sheet.merge_transformed_page(p2, Transformation().scale(scale).translate(tx, ty))
                    
        elif nup == 4:
            # 4 in 1: Output is Portrait A4 (595 x 842 points)
            out_w, out_h = 595.0, 842.0
            slot_w = (out_w / 2.0) - 15.0
            slot_h = (out_h / 2.0) - 15.0
            
            num_sheets = math.ceil(total_orig_pages / 4)
            for sheet_idx in range(num_sheets):
                sheet = writer.add_blank_page(width=out_w, height=out_h)
                
                # 4 slots: (Top-Left, Top-Right, Bottom-Left, Bottom-Right)
                slots = [
                    (10.0, (out_h / 2.0) + 5.0),                # Top-Left
                    ((out_w / 2.0) + 5.0, (out_h / 2.0) + 5.0), # Top-Right
                    (10.0, 10.0),                               # Bottom-Left
                    ((out_w / 2.0) + 5.0, 10.0)                 # Bottom-Right
                ]
                
                for s_i, (sx, sy) in enumerate(slots):
                    p_idx = sheet_idx * 4 + s_i
                    if p_idx < total_orig_pages:
                        p = reader.pages[p_idx]
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
        return input_pdf_path, 1

def secure_delete_file(file_path: str):
    """Safely removes a file from the server immediately after print completion."""
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            print(f"[+] Securely deleted file: {file_path}")
    except Exception as e:
        print(f"[!] Failed to delete file {file_path}: {e}")
