import os
import re
import io
import uuid
import json
import hmac
import hashlib
import asyncio
import secrets
import mimetypes
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

import aiofiles
from fastapi import (
    FastAPI, Request, Response, Form, File, UploadFile, Body,
    WebSocket, WebSocketDisconnect, HTTPException, Depends, status
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

import database as db
import studio
import utils

# Ensure directories exist
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
TRANSFER_DIR = os.path.join(UPLOAD_DIR, "transfers")
ROOM_DIR = os.path.join(UPLOAD_DIR, "rooms")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
DATA_DIR = os.path.join(BASE_DIR, "data")

for _d in (UPLOAD_DIR, TRANSFER_DIR, ROOM_DIR, STATIC_DIR, TEMPLATES_DIR, DATA_DIR):
    os.makedirs(_d, exist_ok=True)

# Limits
MAX_UPLOAD_BYTES = 50 * 1024 * 1024          # print files & transfers: 50 MB
TRANSFER_TTL_HOURS = 24                      # transfer files auto-expire after 24 h
TRANSFER_MAX_PER_DIRECTION = 40              # max pending files per shop (spam / disk protection)
ROOM_TTL_HOURS = 12                          # QR transfer sessions live for 12 h
ROOM_MAX_FILES = 30
ROOM_MAX_TOTAL_BYTES = 300 * 1024 * 1024     # per session
ORDER_FILE_MAX_AGE_HOURS = 72                # abandoned print uploads are removed after 72 h
PREVIEW_CACHE_MAX_AGE_HOURS = 2

# Initialize database
db.init_db()


# ── Session cookie signing (so nobody can fake a shop login by editing the cookie) ──
def _load_secret() -> bytes:
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key.encode()
    path = os.path.join(DATA_DIR, ".secret")
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read().strip()
        key = secrets.token_hex(32).encode()
        with open(path, "wb") as f:
            f.write(key)
        return key
    except Exception:
        return secrets.token_hex(32).encode()


SECRET_KEY = _load_secret()


def sign_value(value: str) -> str:
    sig = hmac.new(SECRET_KEY, value.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{value}.{sig}"


def unsign_value(cookie: Optional[str]) -> Optional[str]:
    if not cookie or "." not in cookie:
        return None
    value, sig = cookie.rsplit(".", 1)
    expected = hmac.new(SECRET_KEY, value.encode(), hashlib.sha256).hexdigest()[:32]
    return value if hmac.compare_digest(sig, expected) else None


def base_url(request: Request) -> str:
    host = request.headers.get("host", "localhost:8000")
    proto = request.headers.get("x-forwarded-proto") or ("https" if "onrender.com" in host else request.url.scheme)
    return f"{proto}://{host}"


def set_session(response: Response, request: Request, shop_id: int):
    response.set_cookie(
        key="shop_session", value=sign_value(str(shop_id)), max_age=86400 * 30,
        httponly=True, samesite="lax", secure=base_url(request).startswith("https")
    )


# ── Background housekeeping ──
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_cleanup():
    """Removes expired transfers and abandoned temp / upload files."""
    for t in db.get_expired_transfers(_now_iso()):
        utils.secure_delete_file(t["stored_path"])
        db.delete_transfer_row(t["id"])
    for r in db.get_expired_rooms(_now_iso()):
        for f in db.list_room_files(r["id"]):
            utils.secure_delete_file(f["stored_path"])
        db.delete_room(r["id"])
    utils.cleanup_old_files(ROOM_DIR, 3600, prefix="zip_")
    utils.cleanup_old_files(UPLOAD_DIR, PREVIEW_CACHE_MAX_AGE_HOURS * 3600, prefix="pv_")
    utils.cleanup_old_files(UPLOAD_DIR, ORDER_FILE_MAX_AGE_HOURS * 3600)


async def _cleanup_loop():
    while True:
        try:
            await asyncio.to_thread(run_cleanup)
        except Exception as e:
            print(f"[!] Cleanup error: {e}")
        await asyncio.sleep(1800)


@asynccontextmanager
async def lifespan(app_: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


app = FastAPI(title="QR Se Print - Auto Print Platform", version="1.1.0", lifespan=lifespan)

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
        # room_id -> list of transfer-room WebSockets
        self.room_connections: Dict[str, List[WebSocket]] = {}

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

    async def connect_room(self, room_id: str, websocket: WebSocket):
        await websocket.accept()
        self.room_connections.setdefault(room_id, []).append(websocket)

    def disconnect_room(self, room_id: str, websocket: WebSocket):
        if room_id in self.room_connections and websocket in self.room_connections[room_id]:
            self.room_connections[room_id].remove(websocket)

    async def notify_room(self, room_id: str, message: dict):
        for ws in list(self.room_connections.get(room_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect_room(room_id, ws)

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
    shop_id_cookie = unsign_value(request.cookies.get("shop_session"))
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
    set_session(response, request, shop_id)
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
    set_session(response, request, shop["id"])
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
    customer_url = f"{base_url(request)}/p/{shop['shop_code']}"
    
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
    rate_photo_4x6: float = Form(30.0),
    rate_photo_a4: float = Form(40.0)
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
        "rate_photo_4x6": rate_photo_4x6,
        "rate_photo_a4": rate_photo_a4
    })
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/admin/qr-standee", response_class=HTMLResponse)
async def qr_standee(request: Request):
    shop = get_current_shop(request)
    if not shop:
        return RedirectResponse(url="/login")
    
    customer_url = f"{base_url(request)}/p/{shop['shop_code']}"
    
    return templates.TemplateResponse(request=request, name="qr_standee.html", context={
        "shop": shop,
        "customer_url": customer_url
    })

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
        "page_range": order["page_range"],
        "printer_name": order["printer_target"] if "printer_target" in order.keys() and order["printer_target"] else ""
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

@app.post("/admin/order/{order_id}/update")
async def edit_order_details(order_id: str, request: Request):
    shop = get_current_shop(request)
    if not shop:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    order = db.get_order(order_id)
    if not order or order["shop_id"] != shop["id"]:
        raise HTTPException(status_code=404, detail="Order not found")
    
    data = await request.json()
    updates = {}
    if "copies" in data:
        updates["copies"] = max(1, min(100, int(data.get("copies", 1))))
    if "color_mode" in data and data["color_mode"] in ("bw", "color"):
        updates["color_mode"] = data["color_mode"]
    if "paper_size" in data and data["paper_size"] in ("A4", "4x6", "NID", "PHOTO"):
        updates["paper_size"] = data["paper_size"]
    if "duplex" in data and data["duplex"] in ("single", "double"):
        updates["duplex"] = data["duplex"]
    if "page_range" in data:
        updates["page_range"] = str(data["page_range"])[:50]
    if "printer_target" in data:
        updates["printer_target"] = str(data["printer_target"])[:100]
    if "total_price" in data:
        try:
            updates["total_price"] = round(float(data["total_price"]), 2)
        except (ValueError, TypeError):
            pass

    db.update_order_details(order_id, updates)
    await manager.notify_admin(shop["id"], {"event": "ORDER_UPDATED", "order_id": order_id})
    return {"success": True, "updated": updates}

@app.get("/admin/order/{order_id}/file")
async def admin_download_order_file(order_id: str, request: Request):
    shop = get_current_shop(request)
    if not shop:
        raise HTTPException(status_code=401, detail="Unauthorized")
    order = db.get_order(order_id)
    if not order or order["shop_id"] != shop["id"]:
        raise HTTPException(status_code=404, detail="Order not found")
    if not os.path.exists(order["file_path"]):
        raise HTTPException(status_code=404, detail="File has been deleted or not found")
    return FileResponse(
        order["file_path"],
        filename=order["original_filename"],
        content_disposition_type="attachment"
    )

# ── CUSTOMER FLOW (/p/{shop_code}) ──

@app.get("/p/{shop_code}", response_class=HTMLResponse)
async def customer_portal(shop_code: str, request: Request):
    shop = db.get_shop_by_code(shop_code.strip().lower())
    if not shop:
        return HTMLResponse("<h2>দুঃখিত! এই শপ কোডের কোনো দোকান খুঁজে পাওয়া যায়নি।</h2>", status_code=404)
    
    return templates.TemplateResponse(request=request, name="customer_print.html", context={
        "shop": shop
    })

# ── Upload helpers ──
async def save_upload_limited(file: UploadFile, dest: str, max_bytes: int = MAX_UPLOAD_BYTES) -> int:
    """Streams an upload to disk and aborts if it exceeds max_bytes."""
    size = 0
    try:
        async with aiofiles.open(dest, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"ফাইলের সাইজ সর্বোচ্চ {max_bytes // (1024 * 1024)} MB হতে পারবে।")
                await out.write(chunk)
    except BaseException:
        utils.secure_delete_file(dest)
        raise
    return size


def resolve_upload(file_id: str) -> str:
    """Maps a client supplied file id to a path INSIDE the upload folder (no path traversal)."""
    safe = os.path.basename(str(file_id or ""))
    path = os.path.join(UPLOAD_DIR, safe)
    if not safe or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return path


NID_TOKEN_RE = re.compile(r"^[0-9a-f]{12}$")


def nid_paths(token: str):
    if not NID_TOKEN_RE.match(token or ""):
        raise HTTPException(status_code=404, detail="NID files not found")
    front = os.path.join(UPLOAD_DIR, f"nid_{token}_f.jpg")
    back = os.path.join(UPLOAD_DIR, f"nid_{token}_b.jpg")
    if not (os.path.isfile(front) and os.path.isfile(back)):
        raise HTTPException(status_code=404, detail="NID files not found")
    return front, back


def clean_choice(value, allowed, default):
    return value if value in allowed else default


def clean_rotation(value) -> int:
    try:
        r = int(value) % 360
    except (TypeError, ValueError):
        return 0
    return r if r in (0, 90, 180, 270) else 0


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Uploads file temporarily, validates it, parses page count and returns file info."""
    ext = utils.get_ext(file.filename or "")
    if ext not in ["pdf", "jpg", "jpeg", "png", "webp"]:
        raise HTTPException(status_code=400, detail="শুধুমাত্র PDF অথবা ছবি (JPG, PNG) আপলোড করা যাবে।")

    fid = uuid.uuid4().hex[:10]
    raw_path = os.path.join(UPLOAD_DIR, f"raw_{fid}.{ext}")
    size = await save_upload_limited(file, raw_path)

    if ext == "pdf":
        file_id = f"{fid}.pdf"
        save_path = os.path.join(UPLOAD_DIR, file_id)
        os.replace(raw_path, save_path)
        ok, msg = utils.is_valid_pdf(save_path)
        if not ok:
            utils.secure_delete_file(save_path)
            raise HTTPException(status_code=400, detail=msg)
        file_type = "pdf"
    else:
        file_id = f"{fid}.jpg"
        save_path = os.path.join(UPLOAD_DIR, file_id)
        try:
            await asyncio.to_thread(utils.normalize_image, raw_path, save_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            utils.secure_delete_file(raw_path)
        file_type = "image"

    pages = utils.get_file_page_count(save_path, file_type)
    return {
        "file_id": file_id,
        "original_filename": utils.safe_filename(file.filename, "document"),
        "file_type": file_type,
        "page_count": pages,
        "file_size": size,
        "preview_url": f"/api/preview/{file_id}"
    }


@app.post("/api/upload-nid")
async def upload_nid_card(front: UploadFile = File(...), back: UploadFile = File(...)):
    """Stores the two NID / ID card photos (front + back). The A4 sheet is rendered on demand."""
    token = uuid.uuid4().hex[:12]
    front_path = os.path.join(UPLOAD_DIR, f"nid_{token}_f.jpg")
    back_path = os.path.join(UPLOAD_DIR, f"nid_{token}_b.jpg")

    try:
        for upload, dest in ((front, front_path), (back, back_path)):
            ext = utils.get_ext(upload.filename or "") or "jpg"
            if ext not in ["jpg", "jpeg", "png", "webp", "heic"]:
                raise HTTPException(status_code=400, detail="NID এর জন্য শুধু ছবি (JPG, PNG) দিন।")
            raw = os.path.join(UPLOAD_DIR, f"raw_nid_{token}_{os.path.basename(dest)}")
            await save_upload_limited(upload, raw, 20 * 1024 * 1024)
            try:
                await asyncio.to_thread(utils.normalize_image, raw, dest)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            finally:
                utils.secure_delete_file(raw)
    except BaseException:
        utils.secure_delete_file(front_path)
        utils.secure_delete_file(back_path)
        raise

    return {
        "file_id": f"nid_{token}",
        "nid_token": token,
        "original_filename": "NID_Card_Front_Back.pdf",
        "file_type": "image",
        "page_count": 1,
        "is_nid": True,
        "preview_url": f"/api/nid-preview/{token}"
    }


@app.get("/api/nid-preview/{token}")
def nid_preview(token: str, color: str = "color", fit: str = "cover", rf: int = 0, rb: int = 0, w: int = 900):
    """Exact print preview of the A4 sheet that will be printed (front + back at real NID size)."""
    front, back = nid_paths(token)
    data = utils.nid_preview_jpeg(
        front, back, width=max(400, min(w, 1400)),
        color_mode=clean_choice(color, ("color", "bw"), "color"),
        fit_mode=clean_choice(fit, ("cover", "contain"), "cover"),
        rot_front=clean_rotation(rf), rot_back=clean_rotation(rb)
    )
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/preview/{file_id}")
async def preview_file(file_id: str):
    """Provides safe preview of the raw uploaded file."""
    file_path = resolve_upload(file_id)
    ext = utils.get_ext(file_path)
    media_type = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                  "png": "image/png", "webp": "image/webp"}.get(ext, "application/octet-stream")
    return FileResponse(file_path, media_type=media_type,
                        headers={"Content-Disposition": f"inline; filename={os.path.basename(file_path)}"})


@app.get("/api/preview-info/{file_id}")
def preview_info(file_id: str, nup: int = 1):
    """How many printed sheets the customer will get and whether page images can be rendered."""
    path = resolve_upload(file_id)
    if utils.get_ext(path) == "pdf":
        pages = utils.get_file_page_count(path, "pdf")
        sheets = -(-pages // nup) if nup in (2, 4) else pages
        return {"can_render": utils.PDF_RENDER_AVAILABLE, "sheets": sheets, "pages": pages}
    return {"can_render": True, "sheets": 1, "pages": 1}


@app.get("/api/preview-page/{file_id}")
def preview_page(file_id: str, page: int = 1, paper_size: str = "A4", nup: int = 1, color: str = "color"):
    """Renders ONE printed sheet exactly as it will come out of the printer."""
    path = resolve_upload(file_id)
    color = clean_choice(color, ("color", "bw"), "color")

    if utils.get_ext(path) == "pdf":
        if not utils.PDF_RENDER_AVAILABLE:
            raise HTTPException(status_code=501, detail="PDF page rendering not available on this server")
        source = path
        if nup in (2, 4):
            cache = os.path.join(UPLOAD_DIR, f"pv_nup{nup}_{os.path.basename(path)}")
            source = utils.get_or_create_nup_preview(path, cache, nup)
        try:
            data = utils.render_pdf_page_preview(source, page - 1, color)
        except IndexError:
            raise HTTPException(status_code=404, detail="Page not found")
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Preview failed: {e}")
    else:
        data = utils.render_image_preview(path, clean_choice(paper_size, ("A4", "4x6"), "A4"), color)

    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=120"})


@app.post("/api/studio-preview")
def studio_preview(payload: dict = Body(...)):
    """Live preview of the photo sheet (passport / stamp / custom sizes in rows)."""
    src = resolve_upload(payload.get("file_id", ""))
    if utils.get_ext(src) not in ("jpg", "jpeg", "png"):
        raise HTTPException(status_code=400, detail="Only images can be used in the photo studio")
    params = studio.sanitize_params(payload.get("params"))
    sheet, info = studio.render_sheet(src, params, dpi=100)
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=85)
    return Response(content=buf.getvalue(), media_type="image/jpeg",
                    headers={"X-Studio-Info": json.dumps(info), "Cache-Control": "no-store"})


@app.post("/api/image/crop")
async def crop_image_endpoint(payload: dict = Body(...)):
    """Crops an uploaded image by normalized box [x, y, width, height] (0..1)."""
    file_id = os.path.basename(str(payload.get("file_id", "")))
    src = resolve_upload(file_id)
    if utils.get_ext(src) not in ("jpg", "jpeg", "png", "webp"):
        raise HTTPException(status_code=400, detail="Only images can be cropped")

    box = payload.get("box", {})
    x = max(0.0, min(1.0, float(box.get("x", 0))))
    y = max(0.0, min(1.0, float(box.get("y", 0))))
    w = max(0.05, min(1.0 - x, float(box.get("w", 1))))
    h = max(0.05, min(1.0 - y, float(box.get("h", 1))))

    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        iw, ih = im.size
        crop_box = (int(x * iw), int(y * ih), int((x + w) * iw), int((y + h) * ih))
        cropped = im.crop(crop_box)
        cropped.save(src, "JPEG", quality=95)

    return {"success": True, "file_id": file_id}


@app.post("/api/image/camscan")
async def camscan_image_endpoint(payload: dict = Body(...)):
    """Applies CamScanner document flattening & clarity enhancement to an uploaded document/photo."""
    file_id = os.path.basename(str(payload.get("file_id", "")))
    src = resolve_upload(file_id)
    if utils.get_ext(src) not in ("jpg", "jpeg", "png", "webp"):
        raise HTTPException(status_code=400, detail="Only images can be scanned/enhanced")

    mode = str(payload.get("mode", "magic_color")).strip()
    import image_fx
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        enhanced = image_fx.camscanner_process(im, mode=mode)
        enhanced.save(src, "JPEG", quality=95)

    return {"success": True, "file_id": file_id}


@app.post("/api/file/rotate")
async def rotate_file_endpoint(payload: dict = Body(...)):
    """Rotates an image or PDF file by 90 degrees clockwise."""
    file_id = os.path.basename(str(payload.get("file_id", "")))
    src = resolve_upload(file_id)
    ext = utils.get_ext(src)

    if ext in ("jpg", "jpeg", "png", "webp"):
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            rotated = im.rotate(-90, expand=True)
            rotated.save(src, "JPEG" if ext in ("jpg", "jpeg") else "PNG", quality=95)
    elif ext == "pdf":
        import pypdf
        reader = pypdf.PdfReader(src)
        writer = pypdf.PdfWriter()
        for p in reader.pages:
            p.rotate(90)
            writer.add_page(p)
        temp_out = src + ".rot.tmp"
        with open(temp_out, "wb") as f_out:
            writer.write(f_out)
        os.replace(temp_out, src)
    else:
        raise HTTPException(status_code=400, detail="Cannot rotate this file format")

    return {"success": True, "file_id": file_id}


@app.post("/api/order/create")
async def customer_create_order(request: Request):
    data = await request.json()
    shop = db.get_shop_by_id(int(data.get("shop_id", 0) or 0))
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    file_id = os.path.basename(str(data.get("file_id", "")))
    if not file_id:
        raise HTTPException(status_code=400, detail="Uploaded file not found")

    try:
        copies = max(1, min(int(data.get("copies", 1)), 100))
    except (TypeError, ValueError):
        copies = 1
    color_mode = clean_choice(data.get("color_mode"), ("bw", "color"), "bw")
    duplex = clean_choice(data.get("duplex"), ("single", "double"), "single")
    paper_size = clean_choice(data.get("paper_size"), ("A4", "4x6", "NID", "PHOTO"), "A4")
    orientation = clean_choice(data.get("orientation"), ("portrait", "landscape"), "portrait")
    payment_method = clean_choice(data.get("payment_method"), ("counter_cash", "bkash", "nagad"), "counter_cash")
    try:
        pages_per_sheet = int(data.get("pages_per_sheet", 1))
    except (TypeError, ValueError):
        pages_per_sheet = 1
    if pages_per_sheet not in (1, 2, 4):
        pages_per_sheet = 1

    order_id = f"QSP-{secrets.token_hex(3).upper()}"
    original_filename = utils.safe_filename(data.get("original_filename"), "document")

    if paper_size == "NID":
        # Build the final A4 PDF: front + back at the exact 85.6 x 54 mm size
        token = file_id[4:] if file_id.startswith("nid_") else ""
        front, back = nid_paths(token)
        file_path = os.path.join(UPLOAD_DIR, f"nid_print_{token}.pdf")
        await asyncio.to_thread(
            utils.save_nid_sheet, front, back, file_path,
            color_mode=color_mode,
            fit_mode=clean_choice(data.get("nid_fit"), ("cover", "contain"), "cover"),
            rot_front=clean_rotation(data.get("nid_rot_front")),
            rot_back=clean_rotation(data.get("nid_rot_back")),
        )
        utils.secure_delete_file(front)
        utils.secure_delete_file(back)
        file_type, page_count = "pdf", 1
        original_filename = "NID_Card_Front_Back.pdf"
        rate = shop["rate_color_single"] if color_mode == "color" else shop["rate_bw_single"]
        total_price = rate * copies
        duplex = "single"
    elif paper_size == "PHOTO":
        # Photo Studio: passport / stamp / custom photos in rows on A4 or 4x6 paper
        src = resolve_upload(file_id)
        if utils.get_ext(src) not in ("jpg", "jpeg", "png"):
            raise HTTPException(status_code=400, detail="ফটো স্টুডিওতে শুধু ছবি ব্যবহার করা যাবে।")
        sp = studio.sanitize_params(data.get("studio"))
        file_path = os.path.join(UPLOAD_DIR, f"studio_{os.path.splitext(file_id)[0]}.pdf")

        def _build():
            sheet, _info = studio.render_sheet(src, sp, dpi=300)
            studio.save_sheet_pdf(sheet, file_path, 300)

        await asyncio.to_thread(_build)
        utils.secure_delete_file(src)
        file_type, page_count = "pdf", 1
        color_mode = "bw" if sp["bw"] else "color"
        duplex = "single"
        original_filename = f"Photo_Sheet_{sp['paper']}.pdf"
        if sp["paper"] == "4x6":
            rate = shop["rate_photo_4x6"]
        else:
            rate = shop["rate_bw_single"] if sp["bw"] else shop["rate_photo_a4"]
        total_price = rate * copies
    else:
        file_path = resolve_upload(file_id)
        file_type = "pdf" if utils.get_ext(file_path) == "pdf" else "image"
        page_count = utils.get_file_page_count(file_path, file_type)   # never trust the browser's number

        if paper_size == "4x6":
            if file_type != "image":
                raise HTTPException(status_code=400, detail="৪×৬ পাসপোর্ট শিট শুধু ছবির জন্য।")
            sheet_path = os.path.join(UPLOAD_DIR, f"sheet_{os.path.splitext(file_id)[0]}.jpg")
            await asyncio.to_thread(utils.create_4x6_passport_sheet, file_path, sheet_path, 6, color_mode)
            if sheet_path != file_path:
                utils.secure_delete_file(file_path)
            file_path = sheet_path
            original_filename = os.path.splitext(original_filename)[0] + "_4x6.jpg"
            page_count = 1
            total_price = shop["rate_photo_4x6"] * copies
        else:
            if pages_per_sheet in (2, 4) and file_type == "pdf":
                nup_path = os.path.join(UPLOAD_DIR, f"nup{pages_per_sheet}_{file_id}")
                nup_path, new_sheet_count = await asyncio.to_thread(utils.create_nup_pdf, file_path, nup_path, pages_per_sheet)
                if nup_path != file_path:
                    utils.secure_delete_file(file_path)
                file_path = nup_path
                page_count = new_sheet_count

            if color_mode == "bw":
                rate = shop["rate_bw_double"] if duplex == "double" else shop["rate_bw_single"]
            else:
                rate = shop["rate_color_double"] if duplex == "double" else shop["rate_color_single"]
            total_price = rate * page_count * copies

    order_data = {
        "id": order_id,
        "shop_id": shop["id"],
        "customer_name": utils.safe_filename(data.get("customer_name"), "Walk-in")[:60],
        "customer_phone": str(data.get("customer_phone", ""))[:20],
        "file_path": file_path,
        "original_filename": original_filename,
        "file_type": file_type,
        "page_count": page_count,
        "copies": copies,
        "color_mode": color_mode,
        "paper_size": paper_size,
        "duplex": duplex,
        "page_range": "all",
        "total_price": round(total_price, 2),
        "payment_method": payment_method,
        "trx_id": re.sub(r"[^A-Za-z0-9]", "", str(data.get("trx_id", "")))[:30],
        "status": "pending_payment"
    }

    db.create_order(order_data)

    # Broadcast new order to Admin dashboard (never expose the server file path)
    public_order = {k: v for k, v in order_data.items() if k != "file_path"}
    await manager.notify_admin(shop["id"], {"event": "NEW_ORDER", "order": public_order})

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
        
    dl_name = order["original_filename"]
    stored_ext = os.path.splitext(order["file_path"])[1].lower()
    if stored_ext and os.path.splitext(dl_name)[1].lower() != stored_ext:
        dl_name = os.path.splitext(dl_name)[0] + stored_ext
    return FileResponse(order["file_path"], filename=dl_name)

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

# ── FILE TRANSFER (customer <-> shop) ──

def _transfer_dict(t, request: Request) -> dict:
    d = {
        "id": t["id"],
        "direction": t["direction"],
        "filename": t["original_filename"],
        "size": t["file_size"],
        "size_text": utils.human_size(t["file_size"]),
        "sender_name": t["sender_name"] or "",
        "note": t["note"] or "",
        "one_time": bool(t["one_time"]),
        "download_count": t["download_count"],
        "created_at": t["created_at"],
        "expires_at": t["expires_at"],
    }
    if t["direction"] == "to_customer":
        d["url"] = f"{base_url(request)}/t/{t['id']}"
        d["qr_url"] = f"/t/{t['id']}/qr.png"
    return d


def _new_transfer_id() -> str:
    while True:
        tid = secrets.token_hex(4).upper()
        if not db.get_transfer(tid):
            return tid


def _transfer_expired(t) -> bool:
    return t["expires_at"] < _now_iso()


async def _store_transfer(shop_id: int, direction: str, file: UploadFile, sender_name: str, note: str, one_time: bool):
    filename = utils.safe_filename(file.filename, "file")
    ext = utils.get_ext(filename)
    if ext in utils.BLOCKED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="নিরাপত্তার কারণে এই ধরনের ফাইল (exe, bat, apk ইত্যাদি) পাঠানো যাবে না।")
    if db.count_transfers(shop_id, direction) >= TRANSFER_MAX_PER_DIRECTION:
        raise HTTPException(status_code=429, detail="ফাইল বক্স এখন পূর্ণ। কিছুক্ষণ পর আবার চেষ্টা করুন।")

    tid = _new_transfer_id()
    stored_path = os.path.join(TRANSFER_DIR, f"{tid}.dat")
    size = await save_upload_limited(file, stored_path, MAX_UPLOAD_BYTES)
    if size == 0:
        utils.secure_delete_file(stored_path)
        raise HTTPException(status_code=400, detail="ফাঁকা ফাইল পাঠানো যাবে না।")

    now = datetime.now(timezone.utc)
    record = {
        "id": tid, "shop_id": shop_id, "direction": direction,
        "original_filename": filename, "stored_path": stored_path, "file_size": size,
        "sender_name": utils.safe_filename(sender_name, "")[:60],
        "note": re.sub(r"[<>]", "", note or "")[:300],
        "one_time": one_time,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(hours=TRANSFER_TTL_HOURS)).isoformat(timespec="seconds"),
    }
    db.create_transfer(record)
    return db.get_transfer(tid)


@app.post("/api/transfer/send/{shop_code}")
async def customer_send_file(
    shop_code: str, request: Request,
    file: UploadFile = File(...),
    sender_name: str = Form(""),
    note: str = Form("")
):
    """Customer -> Shop: any file (docs, zip, photos...) lands in the shopkeeper's inbox."""
    shop = db.get_shop_by_code(shop_code.strip().lower())
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    t = await _store_transfer(shop["id"], "to_shop", file, sender_name, note, False)
    await manager.notify_admin(shop["id"], {"event": "NEW_TRANSFER", "transfer": _transfer_dict(t, request)})
    return {"success": True, "transfer_id": t["id"], "expires_hours": TRANSFER_TTL_HOURS}


@app.get("/admin/transfers")
async def admin_list_transfers(request: Request):
    shop = get_current_shop(request)
    if not shop:
        raise HTTPException(status_code=401, detail="Unauthorized")
    rows = [t for t in db.list_shop_transfers(shop["id"]) if not _transfer_expired(t)]
    return {"transfers": [_transfer_dict(t, request) for t in rows]}


@app.post("/admin/transfer/upload")
async def admin_upload_transfer(
    request: Request,
    file: UploadFile = File(...),
    note: str = Form(""),
    one_time: Optional[int] = Form(0)
):
    """Shop -> Customer: creates a share link + QR code the customer can open on their phone."""
    shop = get_current_shop(request)
    if not shop:
        raise HTTPException(status_code=401, detail="Unauthorized")
    t = await _store_transfer(shop["id"], "to_customer", file, shop["name"], note, bool(one_time))
    return {"success": True, "transfer": _transfer_dict(t, request)}


@app.get("/admin/transfer/{transfer_id}/download")
async def admin_download_transfer(transfer_id: str, request: Request):
    shop = get_current_shop(request)
    if not shop:
        raise HTTPException(status_code=401, detail="Unauthorized")
    t = db.get_transfer(transfer_id.upper())
    if not t or t["shop_id"] != shop["id"] or not os.path.isfile(t["stored_path"]):
        raise HTTPException(status_code=404, detail="File not found or expired")
    db.increment_transfer_download(t["id"])
    return FileResponse(
        t["stored_path"], filename=t["original_filename"],
        media_type=mimetypes.guess_type(t["original_filename"])[0] or "application/octet-stream"
    )


@app.post("/admin/transfer/{transfer_id}/delete")
async def admin_delete_transfer(transfer_id: str, request: Request):
    shop = get_current_shop(request)
    if not shop:
        raise HTTPException(status_code=401, detail="Unauthorized")
    t = db.get_transfer(transfer_id.upper())
    if not t or t["shop_id"] != shop["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    utils.secure_delete_file(t["stored_path"])
    db.delete_transfer_row(t["id"])
    return {"success": True}


def _public_transfer_or_404(transfer_id: str):
    t = db.get_transfer(transfer_id.strip().upper())
    if not t or t["direction"] != "to_customer" or _transfer_expired(t) or not os.path.isfile(t["stored_path"]):
        raise HTTPException(status_code=404, detail="Link expired or file not found")
    return t


@app.get("/t/{transfer_id}", response_class=HTMLResponse)
async def public_transfer_page(transfer_id: str, request: Request):
    try:
        t = _public_transfer_or_404(transfer_id)
    except HTTPException:
        return HTMLResponse(
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<body style='font-family:sans-serif;text-align:center;padding:48px 16px'>"
            "<h2>দুঃখিত! লিংকটির মেয়াদ শেষ হয়েছে বা ফাইলটি পাওয়া যায়নি।</h2>"
            "<p>দোকানদারের কাছ থেকে নতুন লিংক চেয়ে নিন।</p></body>", status_code=404)
    shop = db.get_shop_by_id(t["shop_id"])
    return templates.TemplateResponse(request=request, name="transfer_download.html", context={
        "t": _transfer_dict(t, request), "shop": shop
    })


@app.get("/t/{transfer_id}/download")
async def public_transfer_download(transfer_id: str):
    t = _public_transfer_or_404(transfer_id)
    db.increment_transfer_download(t["id"])
    background = None
    if t["one_time"]:
        def _burn():
            utils.secure_delete_file(t["stored_path"])
            db.delete_transfer_row(t["id"])
        background = BackgroundTask(_burn)
    return FileResponse(
        t["stored_path"], filename=t["original_filename"],
        media_type=mimetypes.guess_type(t["original_filename"])[0] or "application/octet-stream",
        background=background
    )


@app.get("/t/{transfer_id}/qr.png")
async def public_transfer_qr(transfer_id: str, request: Request):
    t = _public_transfer_or_404(transfer_id)
    png = utils.generate_shop_qr(f"{base_url(request)}/t/{t['id']}")
    return Response(content=png, media_type="image/png")


# ── QR FILE TRANSFER ROOMS (phone <-> PC, any file type, no login) ──
ROOM_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_room_create_log: Dict[str, List[float]] = {}


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown"))


def _new_room_id() -> str:
    while True:
        rid = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(8))
        if not db.get_room(rid):
            return rid


def _room_or_404(room_id: str):
    room = db.get_room((room_id or "").strip().upper())
    if not room or room["expires_at"] < _now_iso():
        raise HTTPException(status_code=404, detail="Room expired or not found")
    return room


def _room_files_payload(room) -> dict:
    files = db.list_room_files(room["id"])
    return {
        "room_id": room["id"],
        "expires_at": room["expires_at"],
        "max_files": ROOM_MAX_FILES,
        "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "files": [{
            "id": f["id"], "name": f["original_filename"], "size": f["file_size"],
            "size_text": utils.human_size(f["file_size"]), "uploader": f["uploader"] or "",
            "created_at": f["created_at"],
        } for f in files],
    }


@app.api_route("/ping", methods=["GET", "HEAD"])
async def ping():
    """Tiny keep-alive endpoint for cron-job.org / UptimeRobot (a few bytes, no database)."""
    return Response(content="ok", media_type="text/plain", headers={"Cache-Control": "no-store"})


@app.get("/api/storage-status")
def storage_status():
    """Diagnostic endpoint to verify whether Cloudflare R2 is configured and reachable."""
    import storage
    configured = storage.is_r2_configured()
    bucket = storage.R2_BUCKET_NAME if configured else None
    account_prefix = (storage.R2_ACCOUNT_ID[:6] + "...") if storage.R2_ACCOUNT_ID else None
    client_ok = False
    error = None
    if configured:
        try:
            client = storage.get_r2_client()
            if client:
                client.head_bucket(Bucket=storage.R2_BUCKET_NAME)
                client_ok = True
        except Exception as e:
            error = str(e)
    return {
        "r2_configured": configured,
        "bucket_name": bucket,
        "account_prefix": account_prefix,
        "connected": client_ok,
        "error": error
    }


@app.get("/transfer", response_class=HTMLResponse)
async def transfer_landing(request: Request):
    return templates.TemplateResponse(request=request, name="transfer_room.html", context={"room": None})


@app.post("/transfer/new")
async def transfer_new_room(request: Request):
    ip = _client_ip(request)
    now = datetime.now().timestamp()
    recent = [t for t in _room_create_log.get(ip, []) if now - t < 3600]
    if len(recent) >= 20:
        raise HTTPException(status_code=429, detail="অনেক বেশি সেশন তৈরি হয়েছে, কিছুক্ষণ পর চেষ্টা করুন।")
    recent.append(now)
    _room_create_log[ip] = recent

    rid = _new_room_id()
    created = datetime.now(timezone.utc)
    db.create_room(rid, created.isoformat(timespec="seconds"),
                   (created + timedelta(hours=ROOM_TTL_HOURS)).isoformat(timespec="seconds"))
    return RedirectResponse(url=f"/r/{rid}", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/r/{room_id}", response_class=HTMLResponse)
async def transfer_room_page(room_id: str, request: Request):
    try:
        room = _room_or_404(room_id)
    except HTTPException:
        return HTMLResponse(
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<body style='font-family:sans-serif;text-align:center;padding:48px 16px'>"
            "<h2>এই ট্রান্সফার সেশনের মেয়াদ শেষ হয়েছে বা কোডটি ভুল।</h2>"
            "<p><a href='/transfer' style='color:#db2777;font-weight:bold'>নতুন সেশন শুরু করুন</a></p></body>",
            status_code=404)
    return templates.TemplateResponse(request=request, name="transfer_room.html", context={
        "room": {"id": room["id"], "url": f"{base_url(request)}/r/{room['id']}", "expires_at": room["expires_at"]}
    })


@app.get("/r/{room_id}/qr.png")
async def transfer_room_qr(room_id: str, request: Request):
    room = _room_or_404(room_id)
    return Response(content=utils.generate_shop_qr(f"{base_url(request)}/r/{room['id']}"), media_type="image/png")


@app.get("/api/room/{room_id}/files")
async def room_files(room_id: str):
    return _room_files_payload(_room_or_404(room_id))


@app.post("/api/room/{room_id}/upload")
async def room_upload(room_id: str, file: UploadFile = File(...), device: str = Form("")):
    room = _room_or_404(room_id)
    existing = db.list_room_files(room["id"])
    if len(existing) >= ROOM_MAX_FILES:
        raise HTTPException(status_code=429, detail=f"একটি সেশনে সর্বোচ্চ {ROOM_MAX_FILES}টি ফাইল রাখা যায়। কিছু ফাইল মুছুন।")

    fid = secrets.token_hex(6)
    stored = os.path.join(ROOM_DIR, f"{fid}.dat")
    size = await save_upload_limited(file, stored, MAX_UPLOAD_BYTES)
    if size == 0:
        utils.secure_delete_file(stored)
        raise HTTPException(status_code=400, detail="ফাঁকা ফাইল পাঠানো যাবে না।")
    if sum(f["file_size"] for f in existing) + size > ROOM_MAX_TOTAL_BYTES:
        utils.secure_delete_file(stored)
        raise HTTPException(status_code=413, detail=f"সেশনের মোট সাইজ {ROOM_MAX_TOTAL_BYTES // (1024 * 1024)} MB এর বেশি হতে পারবে না।")

    db.add_room_file({
        "id": fid, "room_id": room["id"],
        "original_filename": utils.safe_filename(file.filename, "file"),
        "stored_path": stored, "file_size": size,
        "uploader": utils.safe_filename(device, "")[:30],
        "created_at": _now_iso(),
    })
    await manager.notify_room(room["id"], {"event": "ROOM_UPDATED"})
    return {"success": True, "file_id": fid}


def _room_file_or_404(room_id: str, file_id: str):
    room = _room_or_404(room_id)
    f = db.get_room_file(file_id)
    if not f or f["room_id"] != room["id"] or not os.path.isfile(f["stored_path"]):
        raise HTTPException(status_code=404, detail="File not found")
    return room, f


@app.get("/api/room/{room_id}/file/{file_id}")
async def room_download(room_id: str, file_id: str):
    _, f = _room_file_or_404(room_id, file_id)
    # always as attachment – an uploaded .html can never run inside our site
    return FileResponse(f["stored_path"], filename=f["original_filename"], media_type="application/octet-stream")


@app.post("/api/room/{room_id}/file/{file_id}/delete")
async def room_delete_file(room_id: str, file_id: str):
    room, f = _room_file_or_404(room_id, file_id)
    utils.secure_delete_file(f["stored_path"])
    db.delete_room_file(f["id"])
    await manager.notify_room(room["id"], {"event": "ROOM_UPDATED"})
    return {"success": True}


@app.get("/api/room/{room_id}/zip")
async def room_zip(room_id: str):
    import zipfile
    room = _room_or_404(room_id)
    files = [f for f in db.list_room_files(room["id"]) if os.path.isfile(f["stored_path"])]
    if not files:
        raise HTTPException(status_code=404, detail="No files")
    tmp = os.path.join(ROOM_DIR, f"zip_{secrets.token_hex(6)}.tmp")
    used = set()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for f in files:
            name, n = f["original_filename"], 1
            while name in used:
                stem, dot, ext = f["original_filename"].rpartition(".")
                name = f"{stem or ext} ({n}){dot}{ext if stem else ''}"
                n += 1
            used.add(name)
            z.write(f["stored_path"], name)
    return FileResponse(tmp, filename=f"files_{room['id']}.zip", media_type="application/zip",
                        background=BackgroundTask(utils.secure_delete_file, tmp))


@app.post("/api/room/{room_id}/close")
async def room_close(room_id: str):
    room = _room_or_404(room_id)
    for f in db.list_room_files(room["id"]):
        utils.secure_delete_file(f["stored_path"])
    db.delete_room(room["id"])
    await manager.notify_room(room["id"], {"event": "ROOM_CLOSED"})
    return {"success": True}


@app.websocket("/ws/room/{room_id}")
async def websocket_room(websocket: WebSocket, room_id: str):
    rid = (room_id or "").strip().upper()
    room = db.get_room(rid)
    if not room or room["expires_at"] < _now_iso():
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await manager.connect_room(rid, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                # WebRTC Signaling relay (P2P direct file transfer between phone and PC)
                if msg.get("type") in ("webrtc_offer", "webrtc_answer", "webrtc_candidate", "webrtc_join"):
                    for ws in list(manager.room_connections.get(rid, [])):
                        if ws != websocket:
                            try:
                                await ws.send_text(raw)
                            except Exception:
                                pass
            except Exception:
                pass
    except WebSocketDisconnect:
        manager.disconnect_room(rid, websocket)
    except Exception:
        manager.disconnect_room(rid, websocket)


# ── QR CODE GENERATOR ROUTE ──

@app.get("/qr/{shop_code}.png")
async def get_shop_qr_png(shop_code: str, request: Request):
    shop = db.get_shop_by_code(shop_code.strip().lower())
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
        
    target_url = f"{base_url(request)}/p/{shop['shop_code']}"
    
    qr_bytes = utils.generate_shop_qr(target_url)
    return Response(content=qr_bytes, media_type="image/png")

# ── RESUME MAKER ROUTE ──

@app.get("/resume", response_class=HTMLResponse)
async def resume_maker(request: Request):
    return templates.TemplateResponse(request=request, name="resume_maker.html", context={})

# ── WEBSOCKETS ──

@app.websocket("/ws/admin/{shop_id}")
async def websocket_admin(websocket: WebSocket, shop_id: int):
    if unsign_value(websocket.cookies.get("shop_session")) != str(shop_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
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
                order = db.get_order(order_id)
                if not order or order["shop_id"] != shop_id:
                    continue
                db.update_order_status(order_id, "completed", printed_at=datetime.now().isoformat())
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
