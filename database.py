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
    
    conn.commit()
    
    # Seed a demo shop if no shops exist
    cursor.execute("SELECT COUNT(*) as count FROM shops")
    if cursor.fetchone()["count"] == 0:
        demo_api_key = "qsp_" + secrets.token_hex(12)
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
        rate_photo_4x6 = ?
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
        page_range, total_price, payment_method, trx_id, status, created_at
    ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
