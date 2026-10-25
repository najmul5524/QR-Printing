import base64
import csv
import html as _html
import io
import json
import os
import re
import shutil
import subprocess
import sys
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
PPT_EXTENSIONS = {"pptx", "ppt"}
OFFICE_EXTENSIONS = WORD_EXTENSIONS | EXCEL_EXTENSIONS | PPT_EXTENSIONS

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
WP_NS = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
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


# ── Tier 1: Native Office / LibreOffice Exact PDF Export (When Installed) ──

def _try_native_office_to_pdf(raw_path: str, ext: str, pdf_out_path: str) -> bool:
    """
    Attempts 100% pixel-exact conversion using LibreOffice (Linux/Windows) or Microsoft Office COM (Windows).
    Returns True if pdf_out_path was successfully created and is a valid non-empty PDF.
    """
    abs_in = os.path.abspath(raw_path)
    abs_out = os.path.abspath(pdf_out_path)
    out_dir = os.path.dirname(abs_out)

    # 1. Try LibreOffice / soffice if available
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir, abs_in],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=35,
                check=False,
            )
            expected_pdf = os.path.join(out_dir, os.path.splitext(os.path.basename(abs_in))[0] + ".pdf")
            if os.path.isfile(expected_pdf) and os.path.getsize(expected_pdf) > 200:
                os.replace(expected_pdf, abs_out)
                return True
        except Exception:
            pass

    # 2. On Windows, try Microsoft Office COM automation if installed
    if sys.platform == "win32" and ext in ("doc", "docx", "rtf", "xls", "xlsx", "ppt", "pptx"):
        ps_in = abs_in.replace("'", "''")
        ps_out = abs_out.replace("'", "''")
        if ext in ("doc", "docx", "rtf"):
            ps_script = (
                f"$w = New-Object -ComObject Word.Application; $w.Visible = $false; $w.DisplayAlerts = 0; "
                f"try {{ $d = $w.Documents.Open('{ps_in}', $false, $true); $d.ExportAsFixedFormat('{ps_out}', 17); $d.Close(0) }} "
                f"finally {{ $w.Quit() }}"
            )
        elif ext in ("xls", "xlsx"):
            ps_script = (
                f"$e = New-Object -ComObject Excel.Application; $e.Visible = $false; $e.DisplayAlerts = $false; "
                f"try {{ $wb = $e.Workbooks.Open('{ps_in}', 0, $true); $wb.ExportAsFixedFormat(0, '{ps_out}'); $wb.Close($false) }} "
                f"finally {{ $e.Quit() }}"
            )
        else:
            ps_script = (
                f"$p = New-Object -ComObject PowerPoint.Application; "
                f"try {{ $pr = $p.Presentations.Open('{ps_in}', -1, 0, 0); $pr.SaveAs('{ps_out}', 32); $pr.Close() }} "
                f"finally {{ $p.Quit() }}"
            )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
            if os.path.isfile(abs_out) and os.path.getsize(abs_out) > 200:
                return True
        except Exception:
            pass

    return False


# ── Tier 2: High-Fidelity DOCX / DOC / RTF / TXT Parser (Preserves Tables, Borders, Colors, Widths) ──

def _clean_hex_color(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    v = val.strip().lstrip("#")
    if v.lower() == "auto" or len(v) != 6:
        return None
    if all(c in "0123456789abcdefABCDEF" for c in v):
        return f"#{v}"
    return None


def _parse_docx_to_html_and_meta(path: str) -> Tuple[str, str]:
    """
    Extracts paragraphs, headings, font sizes, colors, shading, tables (with exact gridCol widths,
    colspan, rowspan, cell background colors, and borders), and embedded images from a .docx file.
    Returns (html_body, orientation).
    """
    parts: List[str] = []
    orientation = "portrait"
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

            # Check section page orientation (<w:pgSz w:orient="landscape"/>)
            for pgSz in body.iter(f"{W_NS}pgSz"):
                orient_attr = pgSz.attrib.get(f"{W_NS}orient", "").lower()
                w_attr = int(pgSz.attrib.get(f"{W_NS}w", "11906") or 11906)
                h_attr = int(pgSz.attrib.get(f"{W_NS}h", "16838") or 16838)
                if orient_attr == "landscape" or w_attr > h_attr:
                    orientation = "landscape"

            def render_paragraph(p_el: ET.Element, in_table: bool = False) -> str:
                align = ""
                style_val = ""
                is_list_item = False
                p_bg = None
                pPr = p_el.find(f"{W_NS}pPr")
                if pPr is not None:
                    jc = pPr.find(f"{W_NS}jc")
                    if jc is not None:
                        val = jc.attrib.get(f"{W_NS}val", "")
                        if val in ("center", "right", "both", "distribute"):
                            align = "justify" if val in ("both", "distribute") else val
                    pStyle = pPr.find(f"{W_NS}pStyle")
                    if pStyle is not None:
                        style_val = pStyle.attrib.get(f"{W_NS}val", "").lower()
                    if pPr.find(f"{W_NS}numPr") is not None:
                        is_list_item = True
                    shd = pPr.find(f"{W_NS}shd")
                    if shd is not None:
                        p_bg = _clean_hex_color(shd.attrib.get(f"{W_NS}fill"))

                runs_html: List[str] = []
                for child in p_el:
                    # Handle hyperlinks and direct runs
                    run_nodes = [child] if child.tag == f"{W_NS}r" else list(child.iter(f"{W_NS}r"))
                    for r_node in run_nodes:
                        rPr = r_node.find(f"{W_NS}rPr")
                        is_b = rPr is not None and (rPr.find(f"{W_NS}b") is not None or rPr.find(f"{W_NS}bCs") is not None)
                        is_i = rPr is not None and (rPr.find(f"{W_NS}i") is not None or rPr.find(f"{W_NS}iCs") is not None)
                        is_u = rPr is not None and rPr.find(f"{W_NS}u") is not None
                        r_color = None
                        r_size_pt = None
                        if rPr is not None:
                            col_el = rPr.find(f"{W_NS}color")
                            if col_el is not None:
                                r_color = _clean_hex_color(col_el.attrib.get(f"{W_NS}val"))
                            sz_el = rPr.find(f"{W_NS}sz")
                            if sz_el is not None:
                                try:
                                    half_pts = int(sz_el.attrib.get(f"{W_NS}val", "0") or 0)
                                    if 10 <= half_pts <= 144:
                                        r_size_pt = half_pts / 2.0
                                except Exception:
                                    pass

                        texts: List[str] = []
                        for sub in r_node:
                            if sub.tag == f"{W_NS}t" and sub.text:
                                texts.append(_html.escape(sub.text))
                            elif sub.tag == f"{W_NS}tab":
                                texts.append("&nbsp;&nbsp;&nbsp;&nbsp;")
                            elif sub.tag in (f"{W_NS}br", f"{W_NS}cr"):
                                texts.append("<br>")

                        for blip in r_node.iter(f"{A_NS}blip"):
                            embed_id = blip.attrib.get(f"{R_NS}embed", "")
                            target = rels_map.get(embed_id, "")
                            if target:
                                media_path = "word/" + target.lstrip("/")
                                if media_path in zf.namelist():
                                    try:
                                        raw_img = zf.read(media_path)
                                        if len(raw_img) < 4 * 1024 * 1024:
                                            b64 = base64.b64encode(raw_img).decode("ascii")
                                            ext_m = media_path.rsplit(".", 1)[-1].lower()
                                            mime = "image/png" if ext_m == "png" else "image/jpeg"
                                            # Determine image size from wp:extent if present (1 pt = 12700 EMUs)
                                            img_w_style = "max-width:100%; max-height:260px;"
                                            for ext_el in r_node.iter(f"{WP_NS}extent"):
                                                cx_emu = int(ext_el.attrib.get("cx", "0") or 0)
                                                cy_emu = int(ext_el.attrib.get("cy", "0") or 0)
                                                if cx_emu > 0 and cy_emu > 0:
                                                    w_pt = min(480, max(24, int(cx_emu / 12700)))
                                                    h_pt = min(620, max(24, int(cy_emu / 12700)))
                                                    img_w_style = f"width:{w_pt}pt; height:{h_pt}pt;"
                                                    break
                                            texts.append(f'<img src="data:{mime};base64,{b64}" style="{img_w_style} display:inline-block; margin:2px 0;">')
                                    except Exception:
                                        pass

                        chunk = "".join(texts)
                        if not chunk:
                            continue
                        span_styles: List[str] = []
                        if r_color:
                            span_styles.append(f"color:{r_color}")
                        if r_size_pt:
                            span_styles.append(f"font-size:{r_size_pt:.1f}pt")
                        if span_styles:
                            chunk = f'<span style="{";".join(span_styles)}">{chunk}</span>'
                        if is_u:
                            chunk = f"<u>{chunk}</u>"
                        if is_i:
                            chunk = f"<i>{chunk}</i>"
                        if is_b:
                            chunk = f"<b>{chunk}</b>"
                        runs_html.append(chunk)

                inner = "".join(runs_html).strip()
                if is_list_item and inner:
                    inner = "• " + inner

                p_styles = [f"margin:{'2px 0' if in_table else '4px 0'}"]
                if align:
                    p_styles.append(f"text-align:{align}")
                if p_bg:
                    p_styles.append(f"background-color:{p_bg}; padding:2px 4px")
                style_attr = f' style="{"; ".join(p_styles)};"'

                if not inner:
                    return "" if in_table else "<p style='margin:3px 0;'>&nbsp;</p>"
                if "heading1" in style_val or style_val == "title":
                    return f"<h2{style_attr}>{inner}</h2>"
                if "heading2" in style_val or "heading3" in style_val:
                    return f"<h3{style_attr}>{inner}</h3>"
                return f"<p{style_attr}>{inner}</p>"

            def render_table(tbl_el: ET.Element) -> str:
                # Extract column widths from w:tblGrid/w:gridCol
                col_widths: List[int] = []
                tblGrid = tbl_el.find(f"{W_NS}tblGrid")
                if tblGrid is not None:
                    for gc in tblGrid.findall(f"{W_NS}gridCol"):
                        try:
                            col_widths.append(max(100, int(gc.attrib.get(f"{W_NS}w", "1000") or 1000)))
                        except Exception:
                            col_widths.append(1000)
                total_grid_w = sum(col_widths) or 1

                t_rows: List[str] = []
                for tr in tbl_el.findall(f"{W_NS}tr"):
                    cells: List[str] = []
                    col_cursor = 0
                    for tc in tr.findall(f"{W_NS}tc"):
                        tcPr = tc.find(f"{W_NS}tcPr")
                        colspan = 1
                        cell_bg = None
                        v_align = "top"
                        if tcPr is not None:
                            gs = tcPr.find(f"{W_NS}gridSpan")
                            if gs is not None:
                                try:
                                    colspan = max(1, int(gs.attrib.get(f"{W_NS}val", "1") or 1))
                                except Exception:
                                    colspan = 1
                            vMerge = tcPr.find(f"{W_NS}vMerge")
                            if vMerge is not None and vMerge.attrib.get(f"{W_NS}val", "") == "":
                                # Continuation of vertical merge: render empty cell or skip
                               pass
                            shd = tcPr.find(f"{W_NS}shd")
                            if shd is not None:
                                cell_bg = _clean_hex_color(shd.attrib.get(f"{W_NS}fill"))
                            vAl = tcPr.find(f"{W_NS}vAlign")
                            if vAl is not None:
                                vv = vAl.attrib.get(f"{W_NS}val", "").lower()
                                if vv in ("center", "bottom"):
                                    v_align = "middle" if vv == "center" else "bottom"

                        # Compute percentage width of this cell from col_widths
                        w_pct_str = ""
                        if col_widths and col_cursor < len(col_widths):
                            span_w = sum(col_widths[col_cursor : col_cursor + colspan])
                            pct = max(4.0, min(96.0, (span_w / float(total_grid_w)) * 100.0))
                            w_pct_str = f"width:{pct:.1f}%;"
                        col_cursor += colspan

                        cell_blocks: List[str] = []
                        for sub_el in tc:
                            if sub_el.tag == f"{W_NS}p":
                                p_h = render_paragraph(sub_el, in_table=True)
                                if p_h:
                                    cell_blocks.append(p_h)
                            elif sub_el.tag == f"{W_NS}tbl":
                                cell_blocks.append(render_table(sub_el))

                        cell_inner = "".join(cell_blocks) or "&nbsp;"
                        c_styles = [
                            "border:1px solid #334155",
                            "padding:4px 6px",
                            f"vertical-align:{v_align}",
                        ]
                        if w_pct_str:
                            c_styles.append(w_pct_str)
                        if cell_bg:
                            c_styles.append(f"background-color:{cell_bg}")
                        span_attr = f' colspan="{colspan}"' if colspan > 1 else ""
                        cells.append(f'<td{span_attr} style="{"; ".join(c_styles)};">{cell_inner}</td>')
                    if cells:
                        t_rows.append("<tr>" + "".join(cells) + "</tr>")

                if not t_rows:
                    return ""
                return (
                    '<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse; width:100%; margin:6px 0; font-size:10pt;">'
                    + "".join(t_rows)
                    + "</table>"
                )

            for elem in body:
                if elem.tag == f"{W_NS}p":
                    parts.append(render_paragraph(elem, in_table=False))
                elif elem.tag == f"{W_NS}tbl":
                    tbl_html = render_table(elem)
                    if tbl_html:
                        parts.append(tbl_html)
    except Exception as e:
        parts.append(f"<p>ডকুমেন্ট লোড হয়েছে ({_html.escape(str(e))})</p>")

    return ("\n".join(parts) if parts else "<p>&nbsp;</p>"), orientation


# ── PowerPoint (.pptx / .ppt) Coordinate-Accurate Slide Renderer ──

def _render_pptx_to_pdf_and_html(raw_path: str, pdf_out_path: str) -> Tuple[str, int]:
    """
    Renders a .pptx presentation directly onto landscape slide pages in PyMuPDF,
    preserving exact shape/text/table/image positions, colors, and slide dimensions,
    and also returns an HTML summary for the editor.
    """
    if _fitz is None:
        raise RuntimeError("PyMuPDF is required")

    slide_html_blocks: List[str] = []
    out_doc = _fitz.open()

    try:
        with zipfile.ZipFile(raw_path, "r") as zf:
            # 1. Read slide size from ppt/presentation.xml (in EMUs; 1 pt = 12700 EMUs)
            sld_w_pt, sld_h_pt = 720.0, 405.0  # Default 16:9 widescreen
            if "ppt/presentation.xml" in zf.namelist():
                try:
                    p_root = ET.fromstring(zf.read("ppt/presentation.xml"))
                    sldSz = p_root.find(f"{P_NS}sldSz")
                    if sldSz is not None:
                        cx = int(sldSz.attrib.get("cx", "9144000") or 9144000)
                        cy = int(sldSz.attrib.get("cy", "5143500") or 5143500)
                        sld_w_pt = max(300.0, cx / 12700.0)
                        sld_h_pt = max(200.0, cy / 12700.0)
                except Exception:
                    pass

            slide_files = sorted(
                [n for n in zf.namelist() if re.match(r"^ppt/slides/slide\d+\.xml$", n)],
                key=lambda s: int(re.search(r"slide(\d+)\.xml", s).group(1))  # type: ignore
            )

            for s_idx, sf in enumerate(slide_files):
                s_num = s_idx + 1
                rels_path = f"ppt/slides/_rels/{os.path.basename(sf)}.rels"
                rels_map: Dict[str, str] = {}
                if rels_path in zf.namelist():
                    try:
                        r_root = ET.fromstring(zf.read(rels_path))
                        for rel in r_root:
                            rid = rel.attrib.get("Id", "")
                            target = rel.attrib.get("Target", "")
                            if rid and target:
                                rels_map[rid] = target
                    except Exception:
                        pass

                s_root = ET.fromstring(zf.read(sf))
                page = out_doc.new_page(width=sld_w_pt, height=sld_h_pt)
                # Subtle slide border
                page.draw_rect(_fitz.Rect(1, 1, sld_w_pt - 1, sld_h_pt - 1), color=(0.82, 0.85, 0.88), width=1)

                slide_texts_html: List[str] = [f"<h3>🎞️ Slide {s_num}</h3>"]

                # Helper to extract xfrm box (x, y, w, h) in points
                def get_xfrm_rect(el: ET.Element) -> Optional[_fitz.Rect]:
                    for xfrm in el.iter(f"{A_NS}xfrm"):
                        off = xfrm.find(f"{A_NS}off")
                        ext = xfrm.find(f"{A_NS}ext")
                        if off is not None and ext is not None:
                            try:
                                x = int(off.attrib.get("x", "0") or 0) / 12700.0
                                y = int(off.attrib.get("y", "0") or 0) / 12700.0
                                w = int(ext.attrib.get("cx", "0") or 0) / 12700.0
                                h = int(ext.attrib.get("cy", "0") or 0) / 12700.0
                                if w > 2 and h > 2:
                                    return _fitz.Rect(x, y, x + w, y + h)
                            except Exception:
                                pass
                    return None

                spTree = s_root.find(f".//{P_NS}spTree")
                if spTree is None:
                    spTree = s_root

                for child in spTree:
                    # 1. Pictures (<p:pic>)
                    if child.tag == f"{P_NS}pic":
                        rect = get_xfrm_rect(child)
                        for blip in child.iter(f"{A_NS}blip"):
                            rid = blip.attrib.get(f"{R_NS}embed", "")
                            target = rels_map.get(rid, "")
                            if target:
                                norm_target = os.path.normpath(os.path.join("ppt/slides", target)).replace("\\", "/")
                                if norm_target in zf.namelist():
                                    try:
                                        img_bytes = zf.read(norm_target)
                                        target_r = rect or _fitz.Rect(40, 40, sld_w_pt - 40, sld_h_pt - 40)
                                        page.insert_image(target_r, stream=img_bytes)
                                    except Exception:
                                        pass

                    # 2. Shapes / Textboxes (<p:sp>)
                    elif child.tag == f"{P_NS}sp":
                        rect = get_xfrm_rect(child) or _fitz.Rect(36, 36, sld_w_pt - 36, sld_h_pt - 36)
                        # Check shape solidFill
                        spPr = child.find(f"{P_NS}spPr")
                        if spPr is not None:
                            sf_el = spPr.find(f"{A_NS}solidFill")
                            if sf_el is not None:
                                srgb = sf_el.find(f"{A_NS}srgbClr")
                                if srgb is not None:
                                    hx = srgb.attrib.get("val", "")
                                    if len(hx) == 6:
                                        try:
                                            r_c = int(hx[0:2], 16) / 255.0
                                            g_c = int(hx[2:4], 16) / 255.0
                                            b_c = int(hx[4:6], 16) / 255.0
                                            page.draw_rect(rect, color=None, fill=(r_c, g_c, b_c))
                                        except Exception:
                                            pass

                        p_htmls: List[str] = []
                        for a_p in child.iter(f"{A_NS}p"):
                            align = "left"
                            pPr = a_p.find(f"{A_NS}pPr")
                            if pPr is not None:
                                algn = pPr.attrib.get("algn", "")
                                if algn == "ctr":
                                    align = "center"
                                elif algn == "r":
                                    align = "right"
                            r_chunks: List[str] = []
                            for a_r in a_p.findall(f"{A_NS}r"):
                                rPr = a_r.find(f"{A_NS}rPr")
                                a_t = a_r.find(f"{A_NS}t")
                                if a_t is None or not a_t.text:
                                    continue
                                txt = _html.escape(a_t.text)
                                sz_pt = 14.0
                                is_b = False
                                col_hex = "#0f172a"
                                if rPr is not None:
                                    if rPr.attrib.get("b") == "1":
                                        is_b = True
                                    try:
                                        sz_val = int(rPr.attrib.get("sz", "0") or 0)
                                        if sz_val >= 600:
                                            sz_pt = min(44.0, sz_val / 100.0)
                                    except Exception:
                                        pass
                                    srgb = rPr.find(f".//{A_NS}srgbClr")
                                    if srgb is not None and len(srgb.attrib.get("val", "")) == 6:
                                        col_hex = "#" + srgb.attrib["val"]
                                chunk = f'<span style="font-size:{sz_pt:.1f}pt; color:{col_hex};">{txt}</span>'
                                if is_b:
                                    chunk = f"<b>{chunk}</b>"
                                r_chunks.append(chunk)
                            if r_chunks:
                                line_html = "".join(r_chunks)
                                p_htmls.append(f'<p style="margin:2px 0; text-align:{align};">{line_html}</p>')
                                slide_texts_html.append(f'<p style="margin:2px 0;">{line_html}</p>')

                        if p_htmls:
                            box_html = "<div style='font-family:sans-serif;'>" + "".join(p_htmls) + "</div>"
                            try:
                                page.insert_htmlbox(rect, box_html)
                            except Exception:
                                pass

                    # 3. Tables inside slide (<p:graphicFrame> containing <a:tbl>)
                    elif child.tag == f"{P_NS}graphicFrame":
                        rect = get_xfrm_rect(child) or _fitz.Rect(36, 80, sld_w_pt - 36, sld_h_pt - 40)
                        for a_tbl in child.iter(f"{A_NS}tbl"):
                            t_rows: List[str] = []
                            for a_tr in a_tbl.findall(f"{A_NS}tr"):
                                cells: List[str] = []
                                for a_tc in a_tr.findall(f"{A_NS}tc"):
                                    cell_txts = [
                                        _html.escape(t.text)
                                        for t in a_tc.iter(f"{A_NS}t")
                                        if t.text
                                    ]
                                    c_inner = " ".join(cell_txts) or "&nbsp;"
                                    cells.append(f'<td style="border:1px solid #334155; padding:4px 6px;">{c_inner}</td>')
                                if cells:
                                    t_rows.append("<tr>" + "".join(cells) + "</tr>")
                            if t_rows:
                                tbl_h = (
                                    '<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse; width:100%; font-size:11pt;">'
                                    + "".join(t_rows)
                                    + "</table>"
                                )
                                slide_texts_html.append(tbl_h)
                                try:
                                    page.insert_htmlbox(rect, tbl_h)
                                except Exception:
                                    pass

                slide_html_blocks.append("\n".join(slide_texts_html))

        if len(out_doc) == 0:
            out_doc.new_page(width=720, height=405)
        tmp_path = pdf_out_path + ".pptx.tmp"
        out_doc.save(tmp_path, garbage=2, deflate=True)
        total_pages = len(out_doc)
    finally:
        out_doc.close()

    os.replace(tmp_path, pdf_out_path)
    _clear_nup_caches_for(pdf_out_path)
    return ("\n<hr>\n".join(slide_html_blocks) if slide_html_blocks else "<p>&nbsp;</p>"), total_pages


def _parse_legacy_text_or_doc(path: str, ext: str) -> str:
    """Extracts clean readable paragraphs from .txt, .rtf, or legacy .doc/.ppt files."""
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

    chunks: List[str] = []
    try:
        u16 = raw.decode("utf-16le", errors="ignore")
        for m in re.findall(r"[\u0980-\u09FF\x20-\x7E\r\n\t]{12,}", u16):
            cleaned = m.strip()
            if len(cleaned) >= 12 and not cleaned.startswith(("Root Entry", "WordDocument", "Microsoft", "PowerPoint")):
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
    return "\n".join(out_ps) if out_ps else "<p>&nbsp;</p>"


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
                    f'<td style="border:1px solid #334155; padding:4px 6px; {bg_style}">{val}</td>'
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
    Renders rich HTML (Word paragraphs, headings, bordered tables, Excel grids, Bengali + English Unicode)
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
    line-height: 1.42;
    color: #0f172a;
  }}
  h1, h2, h3 {{
    color: #0f172a;
    margin-top: 6px;
    margin-bottom: 5px;
  }}
  p {{
    margin-top: 2px;
    margin-bottom: 3px;
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin-top: 6px;
    margin-bottom: 8px;
  }}
  td, th {{
    border: 1px solid #334155;
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
    margin = 32
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
    Converts an uploaded Word (.docx, .doc, .rtf, .txt), Excel (.xlsx, .xls, .csv), or PowerPoint (.pptx, .ppt)
    file into a high-fidelity printable PDF (preserving original tables, borders, slides, and layout)
    AND saves an editable JSON state file.
    """
    ext = ext.lower().lstrip(".")

    # 1. Try 100% native Office / LibreOffice PDF export first if available on the host OS
    native_ok = _try_native_office_to_pdf(raw_path, ext, pdf_out_path)

    if ext in EXCEL_EXTENSIONS:
        doc_kind = "excel"
        sheets = _parse_excel_to_sheets(raw_path, ext)
        html_body = sheets_to_html(sheets, title=orig_filename)
        max_cols = max((len(r) for s in sheets for r in s.get("rows", [])), default=4)
        orientation = "landscape" if max_cols >= 8 else "portrait"
        if not native_ok:
            pages = render_html_to_pdf(html_body, pdf_out_path, orientation=orientation)
        else:
            with _fitz.open(pdf_out_path) as d:
                pages = max(1, len(d))

    elif ext in PPT_EXTENSIONS:
        doc_kind = "ppt"
        sheets = None
        orientation = "landscape"
        if ext == "pptx":
            if not native_ok:
                html_body, pages = _render_pptx_to_pdf_and_html(raw_path, pdf_out_path)
            else:
                # Still extract slide text/tables for the editor state
                tmp_pdf = pdf_out_path + ".slides_meta.tmp"
                try:
                    html_body, _ = _render_pptx_to_pdf_and_html(raw_path, tmp_pdf)
                finally:
                    if os.path.isfile(tmp_pdf):
                        try:
                            os.remove(tmp_pdf)
                        except OSError:
                            pass
                with _fitz.open(pdf_out_path) as d:
                    pages = max(1, len(d))
        else:
            html_body = _parse_legacy_text_or_doc(raw_path, ext)
            if not native_ok:
                pages = render_html_to_pdf(html_body, pdf_out_path, orientation="landscape")
            else:
                with _fitz.open(pdf_out_path) as d:
                    pages = max(1, len(d))

    else:
        doc_kind = "word"
        sheets = None
        if ext == "docx":
            html_body, orientation = _parse_docx_to_html_and_meta(raw_path)
        else:
            html_body = _parse_legacy_text_or_doc(raw_path, ext)
            orientation = "portrait"

        # IMPORTANT: Do NOT use _fitz.open(raw_path, filetype="docx") because MuPDF's built-in docx loader
        # strips <w:tbl> tables into plain text. Instead, use native Office/LibreOffice or our full-table Story renderer!
        if not native_ok:
            pages = render_html_to_pdf(html_body, pdf_out_path, orientation=orientation)
        else:
            with _fitz.open(pdf_out_path) as d:
                pages = max(1, len(d))

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
