import base64
import csv
import html as _html
import io
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageOps

try:
    import pymupdf as _fitz  # type: ignore
except Exception:
    try:
        import fitz as _fitz  # type: ignore
    except Exception:
        _fitz = None

WORD_EXTENSIONS = {"docx", "doc", "rtf", "txt"}
EXCEL_EXTENSIONS = {"xlsx", "xls", "csv"}
OFFICE_EXTENSIONS = WORD_EXTENSIONS | EXCEL_EXTENSIONS

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _clear_nup_caches_for(pdf_path: str) -> None:
    folder = os.path.dirname(pdf_path)
    base = os.path.basename(pdf_path)
    for nup in (2, 4):
        cpath = os.path.join(folder, f"pv_nup{nup}_{base}")
        if os.path.isfile(cpath):
            try:
                os.remove(cpath)
            except OSError:
                pass


def render_pdf_page_pil(pdf_path: str, page_num: int = 1, dpi: int = 200) -> Image.Image:
    """Renders a 1-based page of a PDF file to a PIL RGB Image."""
    if _fitz is None:
        raise RuntimeError("PDF renderer not available")
    doc = _fitz.open(pdf_path)
    try:
        if len(doc) == 0:
            raise ValueError("Empty PDF")
        idx = max(0, min(len(doc) - 1, int(page_num or 1) - 1))
        pix = doc[idx].get_pixmap(dpi=dpi, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


def replace_pdf_page_pil(pdf_path: str, page_num: int, img: Image.Image) -> None:
    """Replaces a single 1-based page inside an existing PDF with the given PIL Image."""
    if _fitz is None:
        raise RuntimeError("PDF renderer not available")
    img = ImageOps.exif_transpose(img).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=93)
    img_bytes = buf.getvalue()

    doc = _fitz.open(pdf_path)
    try:
        idx = max(0, min(len(doc) - 1, int(page_num or 1) - 1))
        old_rect = doc[idx].rect
        orig_w, orig_h = float(old_rect.width) or 595.0, float(old_rect.height) or 842.0
        # Match page orientation to cropped image orientation if strongly landscape vs portrait
        if (img.width > img.height * 1.15 and orig_h > orig_w) or (img.height > img.width * 1.15 and orig_w > orig_h):
            pw, ph = orig_h, orig_w
        else:
            pw, ph = orig_w, orig_h

        doc.delete_page(idx)
        new_page = doc.new_page(pno=idx, width=pw, height=ph)
        margin = 14.0
        avail_w = max(50.0, pw - margin * 2)
        avail_h = max(50.0, ph - margin * 2)
        scale = min(avail_w / max(1, img.width), avail_h / max(1, img.height))
        dw = img.width * scale
        dh = img.height * scale
        x0 = (pw - dw) * 0.5
        y0 = (ph - dh) * 0.5
        target_rect = _fitz.Rect(x0, y0, x0 + dw, y0 + dh)
        new_page.insert_image(target_rect, stream=img_bytes)

        tmp_path = pdf_path + ".page.tmp"
        doc.save(tmp_path, garbage=2, deflate=True)
    finally:
        doc.close()

    os.replace(tmp_path, pdf_path)
    _clear_nup_caches_for(pdf_path)


def apply_camscan_to_all_pdf_pages(orig_pdf_path: str, dest_pdf_path: str, mode: str, strength: str = "normal") -> None:
    """Applies CamScanner processing to all pages of a PDF (up to 40 pages)."""
    import image_fx
    if _fitz is None:
        raise RuntimeError("PDF renderer not available")
    src_doc = _fitz.open(orig_pdf_path)
    out_doc = _fitz.open()
    try:
        total = min(len(src_doc), 40)
        for idx in range(total):
            page = src_doc[idx]
            rect = page.rect
            pw, ph = float(rect.width) or 595.0, float(rect.height) or 842.0
            pix = page.get_pixmap(dpi=180, alpha=False)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if mode != "original":
                im = image_fx.camscanner_process(im, mode=mode, strength=strength)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90)
            new_p = out_doc.new_page(width=pw, height=ph)
            new_p.insert_image(_fitz.Rect(0, 0, pw, ph), stream=buf.getvalue())
        tmp_path = dest_pdf_path + ".allscan.tmp"
        out_doc.save(tmp_path, garbage=2, deflate=True)
    finally:
        src_doc.close()
        out_doc.close()

    os.replace(tmp_path, dest_pdf_path)
    _clear_nup_caches_for(dest_pdf_path)


# ── DOCX / DOC / RTF / TXT Parser ───────────────────────────────────────────

def _parse_docx_to_html(path: str) -> str:
    """Extracts paragraphs, headings, formatting, tables, and embedded images from a .docx file into clean HTML."""
    parts: List[str] = []
    try:
        with zipfile.ZipFile(path, "r") as zf:
            rels_map: Dict[str, str] = {}
            if "word/_rels/document.xml.rels" in zf.namelist():
                try:
                    rel_root = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
                    for rel in rel_root:
                        rid = rel.attrib.get("Id", "")
                        target = rel.attrib.get("Target", "")
                        if rid and target:
                            rels_map[rid] = target
                except Exception:
                    pass

            xml_bytes = zf.read("word/document.xml")
            root = ET.fromstring(xml_bytes)
            body = root.find(f"{W_NS}body")
            if body is None:
                body = root

            def render_paragraph(p_el: ET.Element) -> str:
                align = ""
                style_val = ""
                pPr = p_el.find(f"{W_NS}pPr")
                if pPr is not None:
                    jc = pPr.find(f"{W_NS}jc")
                    if jc is not None:
                        val = jc.attrib.get(f"{W_NS}val", "")
                        if val in ("center", "right", "both"):
                            align = "justify" if val == "both" else val
                    pStyle = pPr.find(f"{W_NS}pStyle")
                    if pStyle is not None:
                        style_val = pStyle.attrib.get(f"{W_NS}val", "").lower()

                runs_html: List[str] = []
                for child in p_el.iter():
                    if child.tag == f"{W_NS}r":
                        rPr = child.find(f"{W_NS}rPr")
                        is_b = rPr is not None and rPr.find(f"{W_NS}b") is not None
                        is_i = rPr is not None and rPr.find(f"{W_NS}i") is not None
                        is_u = rPr is not None and rPr.find(f"{W_NS}u") is not None

                        texts: List[str] = []
                        for sub in child:
                            if sub.tag == f"{W_NS}t" and sub.text:
                                texts.append(_html.escape(sub.text))
                            elif sub.tag == f"{W_NS}tab":
                                texts.append("&nbsp;&nbsp;&nbsp;&nbsp;")
                            elif sub.tag in (f"{W_NS}br", f"{W_NS}cr"):
                                texts.append("<br>")
                        for blip in child.iter(f"{A_NS}blip"):
                            embed_id = blip.attrib.get(f"{R_NS}embed", "")
                            target = rels_map.get(embed_id, "")
                            if target:
                                media_path = "word/" + target.lstrip("/")
                                if media_path in zf.namelist():
                                    try:
                                        raw_img = zf.read(media_path)
                                        if len(raw_img) < 2 * 1024 * 1024:
                                            b64 = base64.b64encode(raw_img).decode("ascii")
                                            ext_m = media_path.rsplit(".", 1)[-1].lower()
                                            mime = "image/png" if ext_m == "png" else "image/jpeg"
                                            texts.append(f'<img src="data:{mime};base64,{b64}" style="max-width:260px; max-height:220px; display:block; margin:4px 0;">')
                                    except Exception:
                                        pass

                        chunk = "".join(texts)
                        if not chunk:
                            continue
                        if is_u:
                            chunk = f"<u>{chunk}</u>"
                        if is_i:
                            chunk = f"<i>{chunk}</i>"
                        if is_b:
                            chunk = f"<b>{chunk}</b>"
                        runs_html.append(chunk)

                inner = "".join(runs_html).strip()
                style_attr = f' style="text-align:{align}; margin: 4px 0;"' if align else ' style="margin: 4px 0;"'
                if not inner:
                    return "<p style='margin:4px 0;'>&nbsp;</p>"
                if "heading1" in style_val or style_val == "title":
                    return f"<h2{style_attr}>{inner}</h2>"
                if "heading2" in style_val or "heading3" in style_val:
                    return f"<h3{style_attr}>{inner}</h3>"
                return f"<p{style_attr}>{inner}</p>"

            for elem in body:
                if elem.tag == f"{W_NS}p":
                    parts.append(render_paragraph(elem))
                elif elem.tag == f"{W_NS}tbl":
                    t_rows: List[str] = []
                    for tr in elem.findall(f"{W_NS}tr"):
                        cells: List[str] = []
                        for tc in tr.findall(f"{W_NS}tc"):
                            cell_ps = [render_paragraph(p) for p in tc.findall(f"{W_NS}p")]
                            cell_inner = "".join(cell_ps) or "&nbsp;"
                            cells.append(f'<td style="border:1px solid #64748b; padding:5px 7px; vertical-align:top;">{cell_inner}</td>')
                        if cells:
                            t_rows.append("<tr>" + "".join(cells) + "</tr>")
                    if t_rows:
                        parts.append(
                            '<table border="1" cellspacing="0" cellpadding="5" style="border-collapse:collapse; width:100%; margin:8px 0; font-size:10.5pt;">'
                            + "".join(t_rows)
                            + "</table>"
                        )
    except Exception as e:
        parts.append(f"<p>ডকুমেন্ট লোড হয়েছে ({_html.escape(str(e))})</p>")

    return "\n".join(parts) if parts else "<p>&nbsp;</p>"


def _parse_legacy_text_or_doc(path: str, ext: str) -> str:
    """Extracts clean readable paragraphs from .txt, .rtf, or legacy .doc files."""
    with open(path, "rb") as f:
        raw = f.read()

    if ext == "txt":
        for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                text = ""
        lines = [_html.escape(line) or "&nbsp;" for line in text.splitlines()]
        return "\n".join(f"<p style='margin:3px 0;'>{ln}</p>" for ln in lines[:2000])

    if ext == "rtf":
        text = raw.decode("latin-1", errors="ignore")
        text = re.sub(r"\\par\b", "\n", text)
        text = re.sub(r"\\[a-zA-Z]+\d*\s?", "", text)
        text = re.sub(r"[{}]", "", text)
        lines = [_html.escape(ln.strip()) for ln in text.splitlines() if ln.strip()]
        return "\n".join(f"<p style='margin:4px 0;'>{ln}</p>" for ln in lines[:1500]) or "<p>&nbsp;</p>"

    # Legacy binary .doc: extract UTF-16LE and ASCII printable runs
    chunks: List[str] = []
    try:
        u16 = raw.decode("utf-16le", errors="ignore")
        for m in re.findall(r"[\u0980-\u09FF\x20-\x7E\r\n\t]{12,}", u16):
            cleaned = m.strip()
            if len(cleaned) >= 12 and not cleaned.startswith(("Root Entry", "WordDocument", "Microsoft")):
                chunks.append(cleaned)
    except Exception:
        pass
    if not chunks:
        asc = raw.decode("latin-1", errors="ignore")
        for m in re.findall(r"[\x20-\x7E\r\n\t]{16,}", asc):
            cleaned = m.strip()
            if len(cleaned) >= 16 and "Microsoft" not in cleaned and "Normal.dot" not in cleaned:
                chunks.append(cleaned)

    out_ps: List[str] = []
    for ch in chunks[:400]:
        for ln in ch.splitlines():
            ln_s = ln.strip()
            if ln_s:
                out_ps.append(f"<p style='margin:4px 0;'>{_html.escape(ln_s)}</p>")
    return "\n".join(out_ps) if out_ps else "<p>ডকুমেন্টের লেখা এখানে লিখুন বা সম্পাদনা করুন...</p>"


# ── XLSX / XLS / CSV Parser ─────────────────────────────────────────────────

def _col_letters_to_index(col_str: str) -> int:
    idx = 0
    for ch in col_str:
        if "A" <= ch <= "Z":
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return max(0, idx - 1)


def _parse_xlsx_zip_fallback(path: str) -> List[Dict[str, Any]]:
    """Pure-stdlib XML parser for .xlsx workbooks in case openpyxl fails or is unavailable."""
    sheets_out: List[Dict[str, Any]] = []
    with zipfile.ZipFile(path, "r") as zf:
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in ss_root.findall(f"{S_NS}si"):
                t_parts = [t.text or "" for t in si.iter(f"{S_NS}t")]
                shared_strings.append("".join(t_parts))

        sheet_files = sorted(
            [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
        )
        for s_idx, sf in enumerate(sheet_files):
            s_root = ET.fromstring(zf.read(sf))
            grid: Dict[Tuple[int, int], str] = {}
            max_r, max_c = 0, 0
            for row_el in s_root.iter(f"{S_NS}row"):
                r_num = int(row_el.attrib.get("r", "1") or 1) - 1
                for c_el in row_el.findall(f"{S_NS}c"):
                    ref = c_el.attrib.get("r", "A1")
                    col_part = "".join(ch for ch in ref if ch.isalpha())
                    c_num = _col_letters_to_index(col_part or "A")
                    ctype = c_el.attrib.get("t", "")
                    v_el = c_el.find(f"{S_NS}v")
                    is_el = c_el.find(f"{S_NS}is")
                    val = ""
                    if ctype == "s" and v_el is not None and v_el.text:
                        s_i = int(v_el.text)
                        if 0 <= s_i < len(shared_strings):
                            val = shared_strings[s_i]
                    elif ctype == "inlineStr" and is_el is not None:
                        val = "".join(t.text or "" for t in is_el.iter(f"{S_NS}t"))
                    elif v_el is not None and v_el.text is not None:
                        val = v_el.text
                    if val != "":
                        grid[(r_num, c_num)] = str(val)
                        max_r = max(max_r, r_num + 1)
                        max_c = max(max_c, c_num + 1)

            max_r = min(max_r, 500)
            max_c = min(max(max_c, 1), 40)
            rows: List[List[str]] = []
            for r in range(max_r):
                rows.append([grid.get((r, c), "") for c in range(max_c)])
            if rows:
                sheets_out.append({"name": f"Sheet {s_idx + 1}", "rows": rows})
    return sheets_out


def _parse_excel_to_sheets(path: str, ext: str) -> List[Dict[str, Any]]:
    """Parses .xlsx, .xls, or .csv into [{"name": ..., "rows": [[...], ...]}]."""
    if ext == "csv":
        with open(path, "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                text = ""
        reader = csv.reader(io.StringIO(text))
        rows = [[str(cell) for cell in row[:40]] for row in list(reader)[:500]]
        return [{"name": "CSV Data", "rows": rows or [[""]]}]

    if ext == "xlsx":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True)
            sheets_out: List[Dict[str, Any]] = []
            for name in wb.sheetnames:
                ws = wb[name]
                raw_rows: List[List[str]] = []
                max_non_empty_col = 0
                for r_idx, row in enumerate(ws.iter_rows(values_only=True)):
                    if r_idx >= 500:
                        break
                    str_row = ["" if v is None else str(v) for v in row[:40]]
                    for c_i, val in enumerate(str_row):
                        if val.strip() != "":
                            max_non_empty_col = max(max_non_empty_col, c_i + 1)
                    raw_rows.append(str_row)
                # Trim trailing empty rows and columns
                while raw_rows and all(c.strip() == "" for c in raw_rows[-1]):
                    raw_rows.pop()
                if max_non_empty_col > 0 and raw_rows:
                    trimmed = [r[:max_non_empty_col] for r in raw_rows]
                    sheets_out.append({"name": name, "rows": trimmed})
            if sheets_out:
                return sheets_out
        except Exception:
            pass
        try:
            fallback = _parse_xlsx_zip_fallback(path)
            if fallback:
                return fallback
        except Exception:
            pass

    # .xls fallback (checks if HTML/TSV disguised as .xls, or xlrd if installed)
    try:
        import xlrd  # type: ignore
        book = xlrd.open_workbook(path)
        sheets_out = []
        for s in book.sheets():
            rows = []
            for r in range(min(s.nrows, 500)):
                rows.append([str(s.cell_value(r, c)) for c in range(min(s.ncols, 40))])
            if rows:
                sheets_out.append({"name": s.name, "rows": rows})
        if sheets_out:
            return sheets_out
    except Exception:
        pass

    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="ignore")
    if "\t" in text:
        rows = [[c.strip() for c in ln.split("\t")[:30]] for ln in text.splitlines()[:400] if ln.strip()]
        if rows:
            return [{"name": "Sheet 1", "rows": rows}]

    return [{"name": "Sheet 1", "rows": [["ক্রমিক", "বিবরণ", "পরিমাণ", "মূল্য"], ["১", "", "", ""]]}]


def sheets_to_html(sheets: List[Dict[str, Any]], title: str = "") -> str:
    """Converts spreadsheet rows into a clean, print-ready HTML table with full borders."""
    blocks: List[str] = []
    for s_idx, sheet in enumerate(sheets):
        s_name = _html.escape(str(sheet.get("name") or f"Sheet {s_idx + 1}"))
        rows = sheet.get("rows") or []
        if not rows:
            continue
        if len(sheets) > 1:
            blocks.append(f'<h3 style="margin:10px 0 4px 0; color:#1e293b; font-size:11pt;">📊 {s_name}</h3>')
        t_rows: List[str] = []
        for r_i, row in enumerate(rows):
            cells: List[str] = []
            is_header = (r_i == 0 and len(rows) > 1)
            bg_style = "background-color:#f1f5f9; font-weight:bold;" if is_header else ""
            for cell in row:
                val = _html.escape(str(cell if cell is not None else "")) or "&nbsp;"
                cells.append(
                    f'<td style="border:1px solid #475569; padding:4px 6px; {bg_style}">{val}</td>'
                )
            t_rows.append("<tr>" + "".join(cells) + "</tr>")
        blocks.append(
            '<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse; width:100%; font-size:9.5pt; margin-bottom:12px;">'
            + "".join(t_rows)
            + "</table>"
        )
    return "\n".join(blocks) if blocks else "<p>&nbsp;</p>"


def render_html_to_pdf(html_body: str, pdf_out_path: str, orientation: str = "portrait") -> int:
    """
    Renders rich HTML (Word paragraphs, headings, Excel tables, Bengali + English Unicode)
    into a clean multi-page A4 PDF using PyMuPDF Story. Returns total page count.
    """
    if _fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is required for Office document rendering")

    full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{
    font-family: sans-serif;
    font-size: 10.5pt;
    line-height: 1.45;
    color: #0f172a;
  }}
  h1, h2, h3 {{
    color: #0f172a;
    margin-top: 8px;
    margin-bottom: 6px;
  }}
  p {{
    margin-top: 3px;
    margin-bottom: 4px;
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin-top: 6px;
    margin-bottom: 10px;
  }}
  td, th {{
    border: 1px solid #475569;
    padding: 4px 6px;
  }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    story = _fitz.Story(html=full_html)
    buf = io.BytesIO()
    writer = _fitz.DocumentWriter(buf)
    base_rect = _fitz.paper_rect("a4")
    if orientation == "landscape":
        mediabox = _fitz.Rect(0, 0, base_rect.height, base_rect.width)
    else:
        mediabox = base_rect
    margin = 34
    where = mediabox + (margin, margin, -margin, -margin)

    more = 1
    page_guard = 0
    while more and page_guard < 120:
        page_guard += 1
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()

    pdf_bytes = buf.getvalue()
    tmp_path = pdf_out_path + ".office.tmp"
    with open(tmp_path, "wb") as f:
        f.write(pdf_bytes)
    os.replace(tmp_path, pdf_out_path)
    _clear_nup_caches_for(pdf_out_path)

    with _fitz.open(pdf_out_path) as doc:
        return max(1, len(doc))


def convert_office_file_to_pdf_and_state(
    raw_path: str,
    ext: str,
    orig_filename: str,
    pdf_out_path: str,
    state_json_path: str
) -> Dict[str, Any]:
    """
    Converts an uploaded Word (.docx, .doc, .rtf, .txt) or Excel (.xlsx, .xls, .csv) file
    into an editable JSON state file AND a printable multi-page A4 PDF.
    """
    ext = ext.lower().lstrip(".")
    if ext in EXCEL_EXTENSIONS:
        doc_kind = "excel"
        sheets = _parse_excel_to_sheets(raw_path, ext)
        html_body = sheets_to_html(sheets, title=orig_filename)
        max_cols = max((len(r) for s in sheets for r in s.get("rows", [])), default=4)
        orientation = "landscape" if max_cols >= 8 else "portrait"
    else:
        doc_kind = "word"
        sheets = None
        if ext == "docx":
            html_body = _parse_docx_to_html(raw_path)
        else:
            html_body = _parse_legacy_text_or_doc(raw_path, ext)
        orientation = "portrait"

    state = {
        "doc_kind": doc_kind,
        "source_ext": ext,
        "title": orig_filename,
        "orientation": orientation,
        "html": html_body,
        "sheets": sheets,
    }
    with open(state_json_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

    # For .docx with complex native layout, try PyMuPDF native docx->pdf first if it produces valid non-empty pages;
    # otherwise (or when edited later) render via Story HTML->PDF.
    used_native = False
    if ext == "docx" and _fitz is not None:
        try:
            doc = _fitz.open(raw_path, filetype="docx")
            pdf_bytes = doc.convert_to_pdf()
            doc.close()
            pdf_doc = _fitz.open("pdf", pdf_bytes)
            if len(pdf_doc) > 0 and any(pdf_doc[i].get_text().strip() for i in range(len(pdf_doc))):
                with open(pdf_out_path, "wb") as f_out:
                    f_out.write(pdf_bytes)
                pages = len(pdf_doc)
                pdf_doc.close()
                used_native = True
            else:
                pdf_doc.close()
        except Exception:
            used_native = False

    if not used_native:
        pages = render_html_to_pdf(html_body, pdf_out_path, orientation=orientation)

    return {
        "doc_kind": doc_kind,
        "page_count": pages,
        "orientation": orientation,
    }


def extract_pdf_editable_state(pdf_path: str, orig_filename: str = "Document.pdf") -> Dict[str, Any]:
    """If a user wants to edit text from a regular PDF, extracts its paragraphs into an editable Word-like HTML state."""
    if _fitz is None:
        return {"doc_kind": "word", "title": orig_filename, "orientation": "portrait", "html": "<p>&nbsp;</p>", "sheets": None}
    ps: List[str] = []
    with _fitz.open(pdf_path) as doc:
        for p_idx in range(min(len(doc), 30)):
            txt = doc[p_idx].get_text("text").strip()
            if not txt:
                continue
            if p_idx > 0:
                ps.append(f'<hr><p style="color:#64748b; font-size:9pt;">— পৃষ্ঠা {p_idx + 1} —</p>')
            for para in txt.split("\n\n"):
                lines = [_html.escape(ln.strip()) for ln in para.splitlines() if ln.strip()]
                if lines:
                    ps.append("<p style='margin:4px 0;'>" + "<br>".join(lines) + "</p>")
    return {
        "doc_kind": "word",
        "title": orig_filename,
        "orientation": "portrait",
        "html": "\n".join(ps) if ps else "<p>নতুন লেখা যোগ করুন...</p>",
        "sheets": None,
    }
