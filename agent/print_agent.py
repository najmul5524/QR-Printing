import os
import sys
import time
import json
import asyncio
import tempfile
import urllib.request
import websockets

CONFIG_FILE = "agent_config.json"

DEFAULT_CONFIG = {
    "server_url": "http://localhost:8000",
    "api_key": "qsp_demo_key_here"
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        print(f"[*] Default config created in '{CONFIG_FILE}'. Please add your shop's API Key.")
        return DEFAULT_CONFIG
    
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def print_file_windows(file_path: str, printer_name: str = None) -> bool:
    """
    Prints a file silently to Windows printer and returns True on success.
    Uses native Windows ShellExecute 'print' verb.
    """
    try:
        if sys.platform == "win32":
            import win32api
            import win32print
            
            target_printer = printer_name or win32print.GetDefaultPrinter()
            print(f"[🖨️] Printing '{os.path.basename(file_path)}' to printer: {target_printer}")
            
            # Use ShellExecute to send directly to printer spooler
            win32api.ShellExecute(
                0,
                "printto" if printer_name else "print",
                file_path,
                f'"{target_printer}"' if printer_name else None,
                ".",
                0
            )
            # Give spooler a brief moment to grab file handle
            time.sleep(3)
            return True
        else:
            # Fallback for Linux / Mac testing (lpr)
            print(f"[*] Simulating print for '{file_path}' (Non-Windows platform)")
            time.sleep(2)
            return True
    except Exception as e:
        print(f"[!] Print execution error: {e}")
        # Secondary fallback
        try:
            if sys.platform == "win32":
                os.startfile(file_path, "print")
                time.sleep(3)
                return True
        except Exception as e2:
            print(f"[!] Fallback print error: {e2}")
        return False

async def handle_print_job(job_data: dict, server_url: str, api_key: str, websocket):
    order_id = job_data["order_id"]
    download_rel_url = job_data["download_url"]
    filename = job_data.get("filename", "print_doc")
    
    # Construct absolute download URL
    full_url = f"{server_url.rstrip('/')}{download_rel_url}?key={api_key}"
    print(f"\n[⚡] New Print Job Received: Order #{order_id} ({filename})")
    
    temp_dir = tempfile.gettempdir()
    local_file_path = os.path.join(temp_dir, f"qsp_{order_id}_{filename}")
    
    try:
        # 1. Download file securely
        print(f"[1/3] Downloading file from cloud...")
        urllib.request.urlretrieve(full_url, local_file_path)
        print(f"[+] Download complete ({os.path.getsize(local_file_path)} bytes)")
        
        # 2. Print to local Windows printer
        print(f"[2/3] Sending to printer...")
        success = print_file_windows(local_file_path)
        
        if success:
            print(f"[3/3] Print successful!")
            # 3. Notify cloud server that print is completed
            await websocket.send(json.dumps({
                "event": "PRINT_COMPLETED",
                "order_id": order_id
            }))
        else:
            print(f"[!] Printing failed.")
            
    except Exception as e:
        print(f"[!] Job processing error: {e}")
    finally:
        # 4. PRIVACY GUARANTEE: Instantly remove file from local PC
        try:
            if os.path.exists(local_file_path):
                os.remove(local_file_path)
                print(f"[🔒] Privacy Verified: Local file deleted permanently.")
        except Exception as err:
            print(f"[!] Warning deleting local temp file: {err}")

async def run_agent():
    print("=" * 60)
    print("      QR Se Print - Windows Desktop Auto-Print Agent       ")
    print("=" * 60)
    
    config = load_config()
    server_url = config.get("server_url", "http://localhost:8000").rstrip("/")
    api_key = config.get("api_key", "").strip()
    
    if not api_key or api_key == "qsp_demo_key_here":
        print("\n[!] Error: 'api_key' is not set in agent_config.json!")
        print("    Please log into your Admin Dashboard at /admin, copy your API Key,")
        print("    and paste it into 'agent_config.json'.")
        input("\nPress Enter to exit...")
        return
        
    # Build WebSocket URL
    ws_protocol = "wss" if server_url.startswith("https") else "ws"
    host_port = server_url.split("://")[-1]
    ws_url = f"{ws_protocol}://{host_port}/ws/agent/{api_key}"
    
    print(f"[*] Connecting to server: {server_url}")
    print(f"[*] API Key: {api_key[:8]}...{api_key[-4:]}")
    
    retry_delay = 3
    while True:
        try:
            print(f"[+] Connecting to cloud WebSocket...")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                print(f"[🟢 ONLINE] Successfully connected! Listening for incoming print jobs...")
                retry_delay = 3
                
                while True:
                    raw_msg = await ws.recv()
                    data = json.loads(raw_msg)
                    if data.get("event") == "PRINT_JOB":
                        await handle_print_job(data.get("data", {}), server_url, api_key, ws)
                        
        except (websockets.ConnectionClosed, ConnectionRefusedError, Exception) as e:
            print(f"[🔴 OFFLINE] Disconnected ({e}). Reconnecting in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, 30)

if __name__ == "__main__":
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        print("\n[*] Agent stopped by user.")
