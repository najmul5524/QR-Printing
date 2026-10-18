import os
import io
import qrcode
from PIL import Image, ImageOps
from pypdf import PdfReader
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
    elif file_type in ["image", "jpg", "jpeg", "png"]:
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
        # Standard 4x6 photo sheet at 300 DPI = 1200 x 1800 px (or 1800 x 1200 landscape)
        sheet_w, sheet_h = 1800, 1200
        sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
        
        # Passport size approx 1.5x2 inch at 300 DPI = 450 x 600 px
        photo_w, photo_h = 450, 600
        
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img) # auto rotate correctly
            img = ImageOps.fit(img, (photo_w, photo_h), Image.Resampling.LANCZOS)
            
            # Add a 1px faint border around photo for easy cutting
            bordered = ImageOps.expand(img, border=2, fill="#e5e7eb")
            
            # Position for 6 photos in a 3x2 grid
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

def secure_delete_file(file_path: str):
    """Safely removes a file from the server immediately after print completion."""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"[+] Securely deleted file: {file_path}")
    except Exception as e:
        print(f"[!] Failed to delete file {file_path}: {e}")

