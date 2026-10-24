import sqlite3
import os
import secrets
from datetime import datetime
from typing import Optional, Dict, Any, List

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "qrprint.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Shops Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS shops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        owner_name TEXT,
        phone TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        api_key TEXT UNIQUE NOT NULL,
        bkash_number TEXT,
        nagad_number TEXT,
        allow_counter_cash INTEGER DEFAULT 1,
        rate_bw_single REAL DEFAULT 3.0,
        rate_bw_double REAL DEFAULT 5.0,
        rate_color_single REAL DEFAULT 10.0,
        rate_color_double REAL DEFAULT 18.0,
        rate_photo_4x6 REAL DEFAULT 30.0,
        rate_photo_a4 REAL DEFAULT 40.0,
        is_active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    );
    """)
    
    # 2. Orders Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY,
        shop_id INTEGER NOT NULL,
        customer_name TEXT,
        customer_phone TEXT,
        file_path TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        file_type TEXT NOT NULL,
        page_count INTEGER DEFAULT 1,
        copies INTEGER DEFAULT 1,
        color_mode TEXT DEFAULT 'bw',
        paper_size TEXT DEFAULT 'A4',
        duplex TEXT DEFAULT 'single',
        page_range TEXT DEFAULT 'all',
        total_price REAL NOT NULL,
        payment_method TEXT DEFAULT 'counter_cash',
        trx_id TEXT,
        status TEXT DEFAULT 'pending_payment',
        created_at TEXT NOT NULL,
        printed_at TEXT,
        FOREIGN KEY (shop_id) REFERENCES shops (id)
    );
    """)
    
    # 3. Printers Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS printers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        printer_name TEXT NOT NULL,
        printer_type TEXT DEFAULT 'bw',
        is_online INTEGER DEFAULT 0,
        last_seen TEXT,
        FOREIGN KEY (shop_id) REFERENCES shops (id)
    );
    """)
    
    # Migration: add printer_target & orientation to orders table if not present
    order_cols = [r[1] for r in cursor.execute("PRAGMA table_info(orders)").fetchall()]
    if "printer_target" not in order_cols:
        cursor.execute("ALTER TABLE orders ADD COLUMN printer_target TEXT DEFAULT ''")
    if "orientation" not in order_cols:
        cursor.execute("ALTER TABLE orders ADD COLUMN orientation TEXT DEFAULT 'portrait'")

    # Migration: shops table extensions
    cols = [r[1] for r in cursor.execute("PRAGMA table_info(shops)").fetchall()]
    if "rate_photo_a4" not in cols:
        cursor.execute("ALTER TABLE shops ADD COLUMN rate_photo_a4 REAL DEFAULT 40.0")
    if "remove_bg_api_key" not in cols:
        cursor.execute("ALTER TABLE shops ADD COLUMN remove_bg_api_key TEXT DEFAULT ''")

    # 4. File Transfers Table (customer -> shop inbox, shop -> customer share links)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transfers (
        id TEXT PRIMARY KEY,
        shop_id INTEGER NOT NULL,
        direction TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        file_size INTEGER DEFAULT 0,
        sender_name TEXT,
        note TEXT,
        one_time INTEGER DEFAULT 0,
        download_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops (id)
    );
    """)

    # 5. QR transfer rooms (phone <-> PC, no login needed)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transfer_rooms (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS room_files (
        id TEXT PRIMARY KEY,
        room_id TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        file_size INTEGER DEFAULT 0,
        uploader TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (room_id) REFERENCES transfer_rooms (id)
    );
    """)

    conn.commit()
    
    # Seed a demo shop if no shops exist
    cursor.execute("SELECT COUNT(*) as count FROM shops")
    if cursor.fetchone()["count"] == 0:
        demo_api_key = "qsp_demo_satata_101"
        now = datetime.now().isoformat()
        cursor.execute("""
        INSERT INTO shops (
            shop_code, name, owner_name, phone, password, api_key, 
            bkash_number, nagad_number, allow_counter_cash,
            rate_bw_single, rate_bw_double, rate_color_single, rate_color_double, rate_photo_4x6,
            created_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """, (
            "demo101", "সততা ফটোকপি ও প্রিন্ট জোন", "রফিকুল ইসলাম", "01700000000", "123456", demo_api_key,
            "01700000000", "01700000000", 1,
            3.0, 5.0, 10.0, 18.0, 30.0,
            now
        ))
        conn.commit()
        print(f"[*] Demo shop created! Code: demo101, Pass: 123456, API Key: {demo_api_key}")
        
    conn.close()

# DB Helpers
def get_shop_by_code(shop_code: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM shops WHERE shop_code = ? AND is_active = 1", (shop_code,))
    shop = cursor.fetchone()
    conn.close()
    return shop

def get_shop_by_id(shop_id: int) -> Optional[sqlite3.Row]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM shops WHERE id = ?", (shop_id,))
    shop = cursor.fetchone()
    conn.close()
    return shop

def get_shop_by_api_key(api_key: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM shops WHERE api_key = ? AND is_active = 1", (api_key,))
    shop = cursor.fetchone()
    if not shop and api_key == "qsp_demo_satata_101":
        # Always fallback to demo shop if master demo key is used
        cursor.execute("SELECT * FROM shops WHERE shop_code = 'demo101' AND is_active = 1")
        shop = cursor.fetchone()
    conn.close()
    return shop

def authenticate_shop(phone: str, password: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM shops WHERE phone = ? AND password = ? AND is_active = 1", (phone, password))
    shop = cursor.fetchone()
    conn.close()
    return shop

def update_shop_settings(shop_id: int, data: Dict[str, Any]):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE shops SET 
        name = ?,
        owner_name = ?,
        bkash_number = ?,
        nagad_number = ?,
        allow_counter_cash = ?,
        rate_bw_single = ?,
        rate_bw_double = ?,
        rate_color_single = ?,
        rate_color_double = ?,
        rate_photo_4x6 = ?,
        rate_photo_a4 = ?,
        remove_bg_api_key = ?
    WHERE id = ?
    """, (
        data.get("name"),
        data.get("owner_name"),
        data.get("bkash_number"),
        data.get("nagad_number"),
        data.get("allow_counter_cash", 1),
        data.get("rate_bw_single", 3.0),
        data.get("rate_bw_double", 5.0),
        data.get("rate_color_single", 10.0),
        data.get("rate_color_double", 18.0),
        data.get("rate_photo_4x6", 30.0),
        data.get("rate_photo_a4", 40.0),
        data.get("remove_bg_api_key", ""),
        shop_id
    ))
    conn.commit()
    conn.close()

def create_order(order_data: Dict[str, Any]) -> str:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO orders (
        id, shop_id, customer_name, customer_phone, file_path, original_filename,
        file_type, page_count, copies, color_mode, paper_size, duplex,
        page_range, orientation, printer_target, total_price, payment_method, trx_id, status, created_at
    ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    )
    """, (
        order_data["id"],
        order_data["shop_id"],
        order_data.get("customer_name", ""),
        order_data.get("customer_phone", ""),
        order_data["file_path"],
        order_data["original_filename"],
        order_data["file_type"],
        order_data.get("page_count", 1),
        order_data.get("copies", 1),
        order_data.get("color_mode", "bw"),
        order_data.get("paper_size", "A4"),
        order_data.get("duplex", "single"),
        order_data.get("page_range", "all"),
        order_data.get("orientation", "portrait"),
        order_data.get("printer_target", ""),
        order_data["total_price"],
        order_data.get("payment_method", "counter_cash"),
        order_data.get("trx_id", ""),
        order_data.get("status", "pending_payment"),
        order_data.get("created_at", datetime.now().isoformat())
    ))
    conn.commit()
    conn.close()
    return order_data["id"]

def get_order(order_id: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    conn.close()
    return order

def update_order_details(order_id: str, updates: Dict[str, Any]):
    """Allows shop admin to edit print parameters (copies, color, paper_size, page_range, printer_target, total_price, etc.)"""
    allowed_keys = {
        "copies", "color_mode", "paper_size", "duplex", "page_range",
        "orientation", "total_price", "printer_target", "customer_name"
    }
    filtered = {k: v for k, v in updates.items() if k in allowed_keys}
    if not filtered:
        return
    set_clause = ", ".join([f"{k} = ?" for k in filtered.keys()])
    values = list(filtered.values()) + [order_id]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE orders SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()

def update_order_status(order_id: str, status: str, printed_at: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    if printed_at:
        cursor.execute("UPDATE orders SET status = ?, printed_at = ? WHERE id = ?", (status, printed_at, order_id))
    else:
        cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()

def get_shop_orders(shop_id: int, limit: int = 50) -> List[sqlite3.Row]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE shop_id = ? ORDER BY created_at DESC LIMIT ?", (shop_id, limit))
    orders = cursor.fetchall()
    conn.close()
    return orders

def get_shop_stats(shop_id: int) -> Dict[str, Any]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total_orders, COALESCE(SUM(total_price), 0) as total_revenue FROM orders WHERE shop_id = ? AND status IN ('paid', 'printing', 'completed')", (shop_id,))
    totals = cursor.fetchone()
    
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) as today_orders, COALESCE(SUM(total_price), 0) as today_revenue FROM orders WHERE shop_id = ? AND status IN ('paid', 'printing', 'completed') AND created_at LIKE ?", (shop_id, f"{today}%"))
    today_stats = cursor.fetchone()
    
    conn.close()
    return {
        "total_orders": totals["total_orders"],
        "total_revenue": totals["total_revenue"],
        "today_orders": today_stats["today_orders"],
        "today_revenue": today_stats["today_revenue"]
    }


# ── File transfer helpers ──
def create_transfer(t: Dict[str, Any]) -> str:
    conn = get_db()
    conn.execute("""
    INSERT INTO transfers (
        id, shop_id, direction, original_filename, stored_path, file_size,
        sender_name, note, one_time, created_at, expires_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        t["id"], t["shop_id"], t["direction"], t["original_filename"], t["stored_path"],
        t.get("file_size", 0), t.get("sender_name", ""), t.get("note", ""),
        1 if t.get("one_time") else 0, t["created_at"], t["expires_at"]
    ))
    conn.commit()
    conn.close()
    return t["id"]


def get_transfer(transfer_id: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    row = conn.execute("SELECT * FROM transfers WHERE id = ?", (transfer_id,)).fetchone()
    conn.close()
    return row


def list_shop_transfers(shop_id: int, limit: int = 100) -> List[sqlite3.Row]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM transfers WHERE shop_id = ? ORDER BY created_at DESC LIMIT ?",
        (shop_id, limit)
    ).fetchall()
    conn.close()
    return rows


def count_transfers(shop_id: int, direction: str) -> int:
    conn = get_db()
    n = conn.execute(
        "SELECT COUNT(*) FROM transfers WHERE shop_id = ? AND direction = ?", (shop_id, direction)
    ).fetchone()[0]
    conn.close()
    return n


def increment_transfer_download(transfer_id: str):
    conn = get_db()
    conn.execute("UPDATE transfers SET download_count = download_count + 1 WHERE id = ?", (transfer_id,))
    conn.commit()
    conn.close()


def delete_transfer_row(transfer_id: str):
    conn = get_db()
    conn.execute("DELETE FROM transfers WHERE id = ?", (transfer_id,))
    conn.commit()
    conn.close()


def get_expired_transfers(now_iso: str) -> List[sqlite3.Row]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM transfers WHERE expires_at < ?", (now_iso,)).fetchall()
    conn.close()
    return rows


# ── QR transfer rooms ──
def create_room(room_id: str, created_at: str, expires_at: str):
    conn = get_db()
    conn.execute("INSERT INTO transfer_rooms (id, created_at, expires_at) VALUES (?, ?, ?)", (room_id, created_at, expires_at))
    conn.commit()
    conn.close()


def get_room(room_id: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    row = conn.execute("SELECT * FROM transfer_rooms WHERE id = ?", (room_id,)).fetchone()
    conn.close()
    return row


def delete_room(room_id: str):
    conn = get_db()
    conn.execute("DELETE FROM room_files WHERE room_id = ?", (room_id,))
    conn.execute("DELETE FROM transfer_rooms WHERE id = ?", (room_id,))
    conn.commit()
    conn.close()


def get_expired_rooms(now_iso: str) -> List[sqlite3.Row]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM transfer_rooms WHERE expires_at < ?", (now_iso,)).fetchall()
    conn.close()
    return rows


def add_room_file(f: Dict[str, Any]):
    conn = get_db()
    conn.execute("""
    INSERT INTO room_files (id, room_id, original_filename, stored_path, file_size, uploader, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (f["id"], f["room_id"], f["original_filename"], f["stored_path"], f["file_size"], f.get("uploader", ""), f["created_at"]))
    conn.commit()
    conn.close()


def list_room_files(room_id: str) -> List[sqlite3.Row]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM room_files WHERE room_id = ? ORDER BY created_at DESC", (room_id,)).fetchall()
    conn.close()
    return rows


def get_room_file(file_id: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    row = conn.execute("SELECT * FROM room_files WHERE id = ?", (file_id,)).fetchone()
    conn.close()
    return row


def delete_room_file(file_id: str):
    conn = get_db()
    conn.execute("DELETE FROM room_files WHERE id = ?", (file_id,))
    conn.commit()
    conn.close()
