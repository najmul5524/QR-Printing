import os
import io
import uuid
import json
import secrets
from datetime import datetime
from typing import Dict, List, Optional, Set

from fastapi import (
    FastAPI, Request, Response, Form, File, UploadFile, 
    WebSocket, WebSocketDisconnect, HTTPException, Depends, status
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database as db
import utils

# Ensure directories exist
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# Initialize database
db.init_db()

app = FastAPI(title="QR Se Print - Auto Print Platform", version="1.0.0")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# ── Connection Manager for WebSockets ──
class ConnectionManager:
    def __init__(self):
        # shop_id -> list of admin WebSockets
        self.admin_connections: Dict[int, List[WebSocket]] = {}
        # shop_id -> list of agent WebSockets
        self.agent_connections: Dict[int, List[WebSocket]] = {}
        # order_id -> list of customer WebSockets
        self.order_connections: Dict[str, List[WebSocket]] = {}

    async def connect_admin(self, shop_id: int, websocket: WebSocket):
        await websocket.accept()
        if shop_id not in self.admin_connections:
            self.admin_connections[shop_id] = []
        self.admin_connections[shop_id].append(websocket)

    def disconnect_admin(self, shop_id: int, websocket: WebSocket):
        if shop_id in self.admin_connections and websocket in self.admin_connections[shop_id]:
            self.admin_connections[shop_id].remove(websocket)

    async def connect_agent(self, shop_id: int, websocket: WebSocket):
        await websocket.accept()
        if shop_id not in self.agent_connections:
            self.agent_connections[shop_id] = []
        self.agent_connections[shop_id].append(websocket)

    def disconnect_agent(self, shop_id: int, websocket: WebSocket):
        if shop_id in self.agent_connections and websocket in self.agent_connections[shop_id]:
            self.agent_connections[shop_id].remove(websocket)

    async def connect_order(self, order_id: str, websocket: WebSocket):
        await websocket.accept()
        if order_id not in self.order_connections:
            self.order_connections[order_id] = []
        self.order_connections[order_id].append(websocket)

    def disconnect_order(self, order_id: str, websocket: WebSocket):
        if order_id in self.order_connections and websocket in self.order_connections[order_id]:
            self.order_connections[order_id].remove(websocket)

    async def notify_admin(self, shop_id: int, message: dict):
        if shop_id in self.admin_connections:
            dead = []
            for ws in self.admin_connections[shop_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.admin_connections[shop_id].remove(ws)

    async def notify_order(self, order_id: str, message: dict):
        if order_id in self.order_connections:
            dead = []
            for ws in self.order_connections[order_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.order_connections[order_id].remove(ws)

    async def dispatch_print_job_to_agent(self, shop_id: int, job_data: dict) -> bool:
        """Dispatches job directly to local shop agent"""
        if shop_id in self.agent_connections and len(self.agent_connections[shop_id]) > 0:
            for ws in self.agent_connections[shop_id]:
                try:
                    await ws.send_json({"event": "PRINT_JOB", "data": job_data})
                    return True
                except Exception as e:
                    print(f"[!] Error sending job to agent: {e}")
        return False

manager = ConnectionManager()

# Helper for current shop session (cookie based)
def get_current_shop(request: Request) -> Optional[dict]:
    shop_id_cookie = request.cookies.get("shop_session")
    if shop_id_cookie and shop_id_cookie.isdigit():
        shop = db.get_shop_by_id(int(shop_id_cookie))
        if shop:
            return dict(shop)
    return None

# ── LANDING & AUTH ROUTES ──

@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    shop = get_current_shop(request)
    return templates.TemplateResponse(request=request, name="index.html", context={"shop": shop})

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin_login.html", context={"mode": "register"})

@app.post("/register")
async def register_shop(
    request: Request,
    shop_name: str = Form(...),
    owner_name: str = Form(...),
    phone: str = Form(...),
    password: str = Form(...),
    shop_code: str = Form(...)
):
    clean_code = shop_code.strip().lower()
    if db.get_shop_by_code(clean_code):
        return templates.TemplateResponse(request=request, name="admin_login.html", context={
            "mode": "register", 
            "error": "এই শপ কোডটি ইতিমধ্যে ব্যবহৃত হয়েছে। অন্য কোড দিন।"
        })
    
    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM shops WHERE phone = ?", (phone.strip(),))
    if cursor.fetchone():
        conn.close()
        return templates.TemplateResponse(request=request, name="admin_login.html", context={
            "mode": "register", 
            "error": "এই মোবাইল নম্বরটি ইতিমধ্যে নিবন্ধিত রয়েছে।"
        })
    
    api_key = "qsp_" + secrets.token_hex(16)
    now = datetime.now().isoformat()
    cursor.execute("""
    INSERT INTO shops (
        shop_code, name, owner_name, phone, password, api_key, 
        bkash_number, nagad_number, allow_counter_cash,
        rate_bw_single, rate_bw_double, rate_color_single, rate_color_double, rate_photo_4x6,
        created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 3.0, 5.0, 10.0, 18.0, 30.0, ?)
    """, (clean_code, shop_name.strip(), owner_name.strip(), phone.strip(), password, api_key, phone.strip(), phone.strip(), now))
    conn.commit()
    shop_id = cursor.lastrowid
    conn.close()
    
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="shop_session", value=str(shop_id), max_age=86400 * 30)
    return response

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin_login.html", context={"mode": "login"})

@app.post("/login")
async def login_shop(
    request: Request,
    phone: str = Form(...),
    password: str = Form(...)
):
    shop = db.authenticate_shop(phone.strip(), password)
    if not shop:
        return templates.TemplateResponse(request=request, name="admin_login.html", context={
            "mode": "login", 
            "error": "মোবাইল নম্বর অথবা পাসওয়ার্ড ভুল হয়েছে।"
        })
    
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="shop_session", value=str(shop["id"]), max_age=86400 * 30)
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="shop_session")
    return response

# ── ADMIN DASHBOARD ──

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    shop = get_current_shop(request)
    if not shop:
        return RedirectResponse(url="/login")
    
    orders = db.get_shop_orders(shop["id"])
    stats = db.get_shop_stats(shop["id"])
    is_agent_online = len(manager.agent_connections.get(shop["id"], [])) > 0
    
    # Base URL for customer link
    host = request.headers.get("host", "localhost:8000")
    proto = "https" if "render.com" in host or request.headers.get("x-forwarded-proto") == "https" else "http"
    customer_url = f"{proto}://{host}/p/{shop['shop_code']}"
    
    return templates.TemplateResponse(request=request, name="admin_dashboard.html", context={
        "shop": shop,
        "orders": orders,
        "stats": stats,
        "is_agent_online": is_agent_online,
        "customer_url": customer_url
    })

@app.post("/admin/settings")
async def update_settings(
    request: Request,
    name: str = Form(...),
    owner_name: str = Form(...),
    bkash_number: str = Form(""),
    nagad_number: str = Form(""),
    allow_counter_cash: Optional[int] = Form(0),
    rate_bw_single: float = Form(3.0),
    rate_bw_double: float = Form(5.0),
    rate_color_single: float = Form(10.0),
    rate_color_double: float = Form(18.0),
    rate_photo_4x6: float = Form(30.0)
):
    shop = get_current_shop(request)
    if not shop:
        return RedirectResponse(url="/login")
    
    db.update_shop_settings(shop["id"], {
        "name": name.strip(),
        "owner_name": owner_name.strip(),
        "bkash_number": bkash_number.strip(),
        "nagad_number": nagad_number.strip(),
        "allow_counter_cash": 1 if allow_counter_cash else 0,
        "rate_bw_single": rate_bw_single,
        "rate_bw_double": rate_bw_double,
        "rate_color_single": rate_color_single,
        "rate_color_double": rate_color_double,
        "rate_photo_4x6": rate_photo_4x6
    })
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/admin/qr-standee", response_class=HTMLResponse)
async def qr_standee(request: Request):
    shop = get_current_shop(request)
    if not shop:
        return RedirectResponse(url="/login")
    
    host = request.headers.get("host", "localhost:8000")
    proto = "https" if "render.com" in host or request.headers.get("x-forwarded-proto") == "https" else "http"
    customer_url = f"{proto}://{host}/p/{shop['shop_code']}"
    
    return templates.TemplateResponse(request=request, name="qr_standee.html", context={
        "shop": shop,
        "customer_url": customer_url
    })

@app.get("/admin/download-config")
async def download_agent_config(request: Request):
    """Generates and downloads a pre-configured agent_config.json for the logged in shop."""
    shop = get_current_shop(request)
    if not shop:
        return RedirectResponse(url="/login")
        
    host = request.headers.get("host", "localhost:8000")
    proto = "https" if "render.com" in host or request.headers.get("x-forwarded-proto") == "https" else "http"
    server_url = f"{proto}://{host}"
    
    cfg_data = json.dumps({
        "server_url": server_url,
        "api_key": shop["api_key"]
    }, indent=4)
    
    return Response(
        content=cfg_data,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=agent_config.json"}
    )

@app.post("/api/agent/login")
async def agent_login(request: Request):
    """Allows Windows agent to authenticate directly using phone & password and retrieve active API Key."""
    data = await request.json()
    phone = data.get("phone", "").strip()
    password = data.get("password", "").strip()
    
    shop = db.authenticate_shop(phone, password)
    if not shop:
        raise HTTPException(status_code=401, detail="ভুল মোবাইল নম্বর অথবা পাসওয়ার্ড!")
        
    return {
        "success": True,
        "api_key": shop["api_key"],
        "shop_name": shop["name"],
        "shop_code": shop["shop_code"]
    }

@app.post("/admin/order/{order_id}/approve")
async def approve_order(order_id: str, request: Request):
    shop = get_current_shop(request)
    if not shop:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    order = db.get_order(order_id)
    if not order or order["shop_id"] != shop["id"]:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Mark as paid and ready to print
    db.update_order_status(order_id, "paid")
    
    # Notify customer live
    await manager.notify_order(order_id, {
        "event": "STATUS_CHANGE",
        "status": "paid",
        "message": "পেমেন্ট নিশ্চিত হয়েছে! ফাইলটি এখন প্রিন্টারে পাঠানো হচ্ছে..."
    })
    
    # Dispatch to local shop Windows agent
    job_dispatched = await manager.dispatch_print_job_to_agent(shop["id"], {
        "order_id": order["id"],
        "download_url": f"/download/{order['id']}",
        "filename": order["original_filename"],
        "file_type": order["file_type"],
        "color_mode": order["color_mode"],
        "copies": order["copies"],
        "duplex": order["duplex"],
        "paper_size": order["paper_size"],
        "page_range": order["page_range"]
    })
    
    if job_dispatched:
        db.update_order_status(order_id, "printing")
        await manager.notify_order(order_id, {
            "event": "STATUS_CHANGE",
            "status": "printing",
            "message": "স্বয়ংক্রিয়ভাবে প্রিন্ট হচ্ছে..."
        })
    
    # Notify admin UI to refresh
    await manager.notify_admin(shop["id"], {"event": "ORDER_UPDATED", "order_id": order_id})
    return {"success": True, "dispatched": job_dispatched}

@app.post("/admin/order/{order_id}/cancel")
async def cancel_order(order_id: str, request: Request):
    shop = get_current_shop(request)
    if not shop:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    order = db.get_order(order_id)
    if not order or order["shop_id"] != shop["id"]:
        raise HTTPException(status_code=404, detail="Order not found")
        
    db.update_order_status(order_id, "cancelled")
    utils.secure_delete_file(order["file_path"])
    
    await manager.notify_order(order_id, {
        "event": "STATUS_CHANGE",
        "status": "cancelled",
        "message": "অর্ডারটি বাতিল করা হয়েছে।"
    })
    await manager.notify_admin(shop["id"], {"event": "ORDER_UPDATED", "order_id": order_id})
    return {"success": True}

# ── CUSTOMER FLOW (/p/{shop_code}) ──

@app.get("/p/{shop_code}", response_class=HTMLResponse)
async def customer_portal(shop_code: str, request: Request):
    shop = db.get_shop_by_code(shop_code.strip().lower())
    if not shop:
        return HTMLResponse("<h2>দুঃখিত! এই শপ কোডের কোনো দোকান খুঁজে পাওয়া যায়নি।</h2>", status_code=404)
    
    return templates.TemplateResponse(request=request, name="customer_print.html", context={
        "shop": shop
    })

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Uploads file temporarily, parses page count and returns file info."""
    ext = file.filename.split(".")[-1].lower() if "." in file.filename else ""
    if ext not in ["pdf", "jpg", "jpeg", "png", "webp"]:
        raise HTTPException(status_code=400, detail="শুধুমাত্র PDF অথবা ছবি (JPG, PNG) আপলোড করা যাবে।")
    
    original_filename = os.path.basename(file.filename or "upload")
    file_id = f"{uuid.uuid4().hex[:10]}_{original_filename}"
    save_path = os.path.join(UPLOAD_DIR, file_id)
    
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="ফাইলের সর্বোচ্চ আকার ৫০ MB হতে পারবে।")
    if not content:
        raise HTTPException(status_code=400, detail="খালি ফাইল আপলোড করা যাবে না।")
    with open(save_path, "wb") as f:
        f.write(content)
        
    file_type = "pdf" if ext == "pdf" else "image"
    pages = utils.get_file_page_count(save_path, file_type)
    
    return {
        "file_id": file_id,
        "original_filename": original_filename,
        "file_type": file_type,
        "page_count": pages,
        "file_size": len(content),
        "preview_url": f"/api/preview/{file_id}"
    }

@app.post("/api/upload-nid")
async def upload_nid_card(front: UploadFile = File(...), back: UploadFile = File(...)):
    """Uploads Front and Back of NID card and combines them onto a single A4 sheet with cutting guidelines."""
    front_ext = front.filename.split(".")[-1].lower() if "." in front.filename else "jpg"
    back_ext = back.filename.split(".")[-1].lower() if "." in back.filename else "jpg"
    if front_ext not in ALLOWED_IMAGE_EXTENSIONS or back_ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="NID-এর জন্য JPG, PNG অথবা WEBP ছবি দিন।")
    
    front_id = f"nid_f_{uuid.uuid4().hex[:8]}.{front_ext}"
    back_id = f"nid_b_{uuid.uuid4().hex[:8]}.{back_ext}"
    front_path = os.path.join(UPLOAD_DIR, front_id)
    back_path = os.path.join(UPLOAD_DIR, back_id)
    
    front_content = await front.read()
    back_content = await back.read()
    if not front_content or not back_content:
        raise HTTPException(status_code=400, detail="NID-এর দুই পিঠের ছবিই দিতে হবে।")
    if len(front_content) > MAX_UPLOAD_BYTES or len(back_content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="প্রতিটি ছবির সর্বোচ্চ আকার ৫০ MB হতে পারবে।")
    with open(front_path, "wb") as f:
        f.write(front_content)
    with open(back_path, "wb") as f:
        f.write(back_content)
        
    combined_id = f"nid_sheet_{uuid.uuid4().hex[:8]}.jpg"
    combined_path = os.path.join(UPLOAD_DIR, combined_id)
    
    try:
        utils.create_nid_card_sheet(front_path, back_path, combined_path, layout="vertical")
    except Exception as exc:
        utils.secure_delete_file(front_path)
        utils.secure_delete_file(back_path)
        raise HTTPException(status_code=422, detail=f"NID ছবি প্রসেস করা যায়নি: {exc}")
    
    utils.secure_delete_file(front_path)
    utils.secure_delete_file(back_path)
    
    return {
        "file_id": combined_id,
        "original_filename": "NID_Card_Front_Back.jpg",
        "file_type": "image",
        "page_count": 1,
        "is_nid": True,
        "preview_url": f"/api/preview/{combined_id}"
    }

@app.get("/api/transfer/{file_id}")
async def transfer_file(file_id: str):
    """Returns an uploaded file for direct transfer/download between devices."""
    safe_filename = os.path.basename(file_id)
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="ফাইলটি পাওয়া যায়নি বা ইতিমধ্যে মুছে গেছে।")
    return FileResponse(
        file_path,
        filename=safe_filename,
        headers={"Cache-Control": "no-store"}
    )

@app.get("/api/preview/{file_id}")
async def preview_file(file_id: str):
    """Provides safe preview of uploaded file or generated NID card sheet."""
    safe_filename = os.path.basename(file_id)
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    ext = safe_filename.split(".")[-1].lower() if "." in safe_filename else ""
    if ext == "pdf":
        media_type = "application/pdf"
    elif ext in ["jpg", "jpeg"]:
        media_type = "image/jpeg"
    elif ext == "png":
        media_type = "image/png"
    elif ext == "webp":
        media_type = "image/webp"
    else:
        media_type = "application/octet-stream"
        
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename={safe_filename}"}
    )

@app.post("/api/order/create")
async def customer_create_order(request: Request):
    data = await request.json()
    shop = db.get_shop_by_id(data["shop_id"])
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
        
    file_id = data["file_id"]
    file_path = os.path.join(UPLOAD_DIR, file_id)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="Uploaded file not found")
        
    order_id = f"QSP-{secrets.token_hex(3).upper()}"
    
    copies = int(data.get("copies", 1))
    page_count = int(data.get("page_count", 1))
    color_mode = data.get("color_mode", "bw") # "bw" or "color"
    duplex = data.get("duplex", "single")     # "single" or "double"
    paper_size = data.get("paper_size", "A4") # "A4", "4x6", or "NID"
    pages_per_sheet = int(data.get("pages_per_sheet", 1)) # 1, 2, or 4
    
    # Handle N-up multi-page per sheet (2-in-1, 4-in-1) for PDFs
    if pages_per_sheet in [2, 4] and data.get("file_type") == "pdf":
        nup_file_id = f"nup{pages_per_sheet}_{file_id}"
        nup_path = os.path.join(UPLOAD_DIR, nup_file_id)
        nup_path, new_sheet_count = utils.create_nup_pdf(file_path, nup_path, nup=pages_per_sheet)
        utils.secure_delete_file(file_path)
        file_path = nup_path
        page_count = new_sheet_count
    
    if paper_size == "4x6":
        # 4x6 Photo Passport Sheet
        unit_price = shop["rate_photo_4x6"]
        total_price = unit_price * copies
        
        # Transform image to 4x6 sheet if it's an image
        if data.get("file_type") == "image":
            sheet_path = os.path.join(UPLOAD_DIR, f"sheet_{file_id}.jpg")
            utils.create_4x6_passport_sheet(file_path, sheet_path, count=6)
            utils.secure_delete_file(file_path)
            file_path = sheet_path
    elif paper_size == "NID":
        # NID Card sheet on A4
        rate = shop["rate_color_single"] if color_mode == "color" else shop["rate_bw_single"]
        total_price = rate * copies
    else:
        if color_mode == "bw":
            rate = shop["rate_bw_double"] if duplex == "double" else shop["rate_bw_single"]
        else:
            rate = shop["rate_color_double"] if duplex == "double" else shop["rate_color_single"]
            
        total_price = rate * page_count * copies

    order_data = {
        "id": order_id,
        "shop_id": shop["id"],
        "customer_name": data.get("customer_name", "Walk-in"),
        "customer_phone": data.get("customer_phone", ""),
        "file_path": file_path,
        "original_filename": data.get("original_filename", "document"),
        "file_type": data.get("file_type", "pdf"),
        "page_count": page_count,
        "copies": copies,
        "color_mode": color_mode,
        "paper_size": paper_size,
        "duplex": duplex,
        "page_range": data.get("page_range", "all"),
        "total_price": round(total_price, 2),
        "payment_method": data.get("payment_method", "counter_cash"),
        "trx_id": data.get("trx_id", ""),
        "status": "pending_payment"
    }
    
    db.create_order(order_data)
    
    # Broadcast new order to Admin dashboard
    await manager.notify_admin(shop["id"], {
        "event": "NEW_ORDER",
        "order": order_data
    })
    
    return {"success": True, "order_id": order_id, "total_price": round(total_price, 2)}

@app.post("/api/order/{order_id}/pay")
async def customer_submit_payment(order_id: str, request: Request):
    data = await request.json()
    order = db.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
        
    payment_method = data.get("payment_method", "counter_cash")
    trx_id = data.get("trx_id", "")
    
    conn = db.get_db()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE orders SET payment_method = ?, trx_id = ? WHERE id = ?
    """, (payment_method, trx_id, order_id))
    conn.commit()
    conn.close()
    
    msg = "কাউন্টারে ক্যাশ পেমেন্ট রিকোয়েস্ট পাঠানো হয়েছে।" if payment_method == "counter_cash" else f"পেমেন্ট তথ্য ({payment_method.upper()} - {trx_id}) জমা দেওয়া হয়েছে।"
    
    # Notify Admin Dashboard
    await manager.notify_admin(order["shop_id"], {
        "event": "PAYMENT_SUBMITTED",
        "order_id": order_id,
        "payment_method": payment_method,
        "trx_id": trx_id
    })
    
    return {"success": True, "message": msg}

# ── AGENT (WINDOWS DESKTOP) ENDPOINTS ──

@app.get("/download/{order_id}")
async def download_print_file(order_id: str, key: str):
    """Allows authenticated shop agent to download the file for printing."""
    shop = db.get_shop_by_api_key(key)
    if not shop:
        raise HTTPException(status_code=401, detail="Invalid API Key")
        
    order = db.get_order(order_id)
    if not order or order["shop_id"] != shop["id"]:
        raise HTTPException(status_code=404, detail="File not found")
        
    if not os.path.exists(order["file_path"]):
        raise HTTPException(status_code=404, detail="File has already been securely removed")
        
    return FileResponse(order["file_path"], filename=order["original_filename"])

@app.post("/api/agent/order/{order_id}/printed")
async def agent_mark_printed(order_id: str, request: Request):
    data = await request.json()
    api_key = data.get("api_key")
    shop = db.get_shop_by_api_key(api_key)
    if not shop:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    order = db.get_order(order_id)
    if not order or order["shop_id"] != shop["id"]:
        raise HTTPException(status_code=404, detail="Order not found")
        
    now = datetime.now().isoformat()
    db.update_order_status(order_id, "completed", printed_at=now)
    
    # Auto delete file from server immediately
    utils.secure_delete_file(order["file_path"])
    
    # Notify customer live that print is done & file is deleted
    await manager.notify_order(order_id, {
        "event": "STATUS_CHANGE",
        "status": "completed",
        "message": "প্রিন্ট সম্পন্ন হয়েছে! আপনার ফাইলটি সার্ভার থেকে স্বয়ংক্রিয়ভাবে মুছে দেওয়া হয়েছে।"
    })
    
    # Notify admin
    await manager.notify_admin(shop["id"], {
        "event": "ORDER_UPDATED",
        "order_id": order_id,
        "status": "completed"
    })
    
    return {"success": True}

# ── QR CODE GENERATOR ROUTE ──

@app.get("/qr/{shop_code}.png")
async def get_shop_qr_png(shop_code: str, request: Request):
    shop = db.get_shop_by_code(shop_code.strip().lower())
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
        
    host = request.headers.get("host", "localhost:8000")
    proto = "https" if "render.com" in host or request.headers.get("x-forwarded-proto") == "https" else "http"
    target_url = f"{proto}://{host}/p/{shop['shop_code']}"
    
    qr_bytes = utils.generate_shop_qr(target_url)
    return Response(content=qr_bytes, media_type="image/png")

# ── RESUME MAKER ROUTE ──

@app.get("/resume", response_class=HTMLResponse)
async def resume_maker(request: Request):
    return templates.TemplateResponse(request=request, name="resume_maker.html", context={})

# ── WEBSOCKETS ──

@app.websocket("/ws/admin/{shop_id}")
async def websocket_admin(websocket: WebSocket, shop_id: int):
    await manager.connect_admin(shop_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_admin(shop_id, websocket)

@app.websocket("/ws/agent/{api_key}")
async def websocket_agent(websocket: WebSocket, api_key: str):
    shop = db.get_shop_by_api_key(api_key)
    if not shop:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
        
    shop_id = shop["id"]
    await manager.connect_agent(shop_id, websocket)
    await manager.notify_admin(shop_id, {"event": "AGENT_STATUS", "online": True})
    print(f"[+] Agent connected for shop {shop['name']} ({shop['shop_code']})")
    
    try:
        while True:
            data = await websocket.receive_json()
            event = data.get("event")
            if event == "PRINT_COMPLETED":
                order_id = data.get("order_id")
                db.update_order_status(order_id, "completed", printed_at=datetime.now().isoformat())
                order = db.get_order(order_id)
                if order:
                    utils.secure_delete_file(order["file_path"])
                await manager.notify_order(order_id, {
                    "event": "STATUS_CHANGE",
                    "status": "completed",
                    "message": "প্রিন্ট সম্পন্ন হয়েছে! ফাইলটি মুছে ফেলা হয়েছে।"
                })
                await manager.notify_admin(shop_id, {"event": "ORDER_UPDATED", "order_id": order_id})
    except WebSocketDisconnect:
        manager.disconnect_agent(shop_id, websocket)
        await manager.notify_admin(shop_id, {"event": "AGENT_STATUS", "online": False})
        print(f"[-] Agent disconnected for shop {shop['name']}")

@app.websocket("/ws/order/{order_id}")
async def websocket_order(websocket: WebSocket, order_id: str):
    await manager.connect_order(order_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_order(order_id, websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
