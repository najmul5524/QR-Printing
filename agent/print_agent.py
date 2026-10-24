import os
import sys
import time
import json
import asyncio
import tempfile
import urllib.request
import ssl
import re
import shutil
import subprocess
import websockets

# Ensure UTF-8 output on Windows consoles to prevent cp1252 UnicodeEncodeError
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(AGENT_DIR, "agent_config.json")

# NOTE: the API key is NOT stored here any more – the agent asks for it every time it starts.
DEFAULT_CONFIG = {
    "server_url": "https://qr-printing.onrender.com",
    "printer_name": "",
    "sumatra_path": ""
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return DEFAULT_CONFIG
    
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONFIG

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"[!] Config save error: {e}")

def find_sumatra(config: dict):
    """
    SumatraPDF (free, portable) prints PDFs/images with NO scaling and supports copies.
    This is the best way to get the NID card printed at the exact 85.6 x 54 mm size.
    Put SumatraPDF.exe inside the agent folder, or set "sumatra_path" in agent_config.json.
    """
    candidates = [
        config.get("sumatra_path", ""),
        os.path.join(AGENT_DIR, "SumatraPDF.exe"),
        os.path.join(AGENT_DIR, "SumatraPDF-portable.exe"),
        r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
        r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
        shutil.which("SumatraPDF") or "",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def print_with_sumatra(sumatra: str, file_path: str, copies: int, printer_name: str = "", orientation: str = "portrait") -> bool:
    settings_items = ["noscale", f"{max(1, copies)}x"]
    if orientation == "landscape":
        settings_items.append("landscape")
    elif orientation == "portrait":
        settings_items.append("portrait")
    settings = ",".join(settings_items)
    cmd = [sumatra]
    cmd += ["-print-to", printer_name] if printer_name else ["-print-to-default"]
    cmd += ["-print-settings", settings, "-silent", file_path]
    print(f"[PRINT] SumatraPDF ({orientation}, no scaling, {copies} copy): {os.path.basename(file_path)}")
    result = subprocess.run(cmd, timeout=180)
    return result.returncode == 0


def print_file_windows(file_path: str, copies: int = 1, config: dict = None) -> bool:
    """
    Prints a file silently on a Windows printer.
    1) SumatraPDF if available (exact size, copies handled natively)
    2) win32api / os.startfile shell "print" verb (copies handled by repeating the job)
    """
    config = config or {}
    printer_name = (config.get("printer_name") or "").strip()
    copies = max(1, int(copies or 1))
    try:
        if sys.platform != "win32":
            print(f"[*] Simulating {copies} print(s) for '{file_path}' (Non-Windows platform)")
            time.sleep(1)
            return True

        sumatra = find_sumatra(config)
        orientation = (config.get("orientation") or "portrait").strip()
        if sumatra and file_path.lower().endswith((".pdf", ".jpg", ".jpeg", ".png")):
            return print_with_sumatra(sumatra, file_path, copies, printer_name, orientation)

        for n in range(copies):
            try:
                import win32api
                import win32print

                target_printer = printer_name or win32print.GetDefaultPrinter()
                print(f"[PRINT] ({n + 1}/{copies}) {target_printer}: {os.path.basename(file_path)}")
                win32api.ShellExecute(
                    0,
                    "printto" if printer_name else "print",
                    file_path,
                    f'"{target_printer}"' if printer_name else None,
                    ".",
                    0
                )
            except ImportError:
                print(f"[PRINT] ({n + 1}/{copies}) default printer: {os.path.basename(file_path)}")
                os.startfile(file_path, "print")
            time.sleep(4)
        return True
    except Exception as e:
        print(f"[!] Print error: {e}")
        return False

async def handle_print_job(job_data: dict, server_url: str, api_key: str, websocket, config: dict = None):
    order_id = job_data["order_id"]
    download_rel_url = job_data["download_url"]
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", job_data.get("filename", "print_doc")) or "print_doc"
    copies = int(job_data.get("copies", 1) or 1)
    target_printer = (job_data.get("printer_name") or "").strip()
    
    full_url = f"{server_url.rstrip('/')}{download_rel_url}?key={api_key}"
    print(f"\n========================================================")
    print(f"[+] NEW PRINT ORDER: Order #{order_id} ({filename}) x{copies} {f'-> Printer: {target_printer}' if target_printer else ''}")
    print(f"========================================================")
    
    temp_dir = tempfile.gettempdir()
    local_file_path = os.path.join(temp_dir, f"qsp_{order_id}_{filename}")
    
    try:
        print(f"[1/3] Downloading file from cloud...")
        urllib.request.urlretrieve(full_url, local_file_path)
        print(f"[✓] Download complete ({os.path.getsize(local_file_path)} bytes)")
        
        print(f"[2/3] Sending to printer...")
        # Use job-specific printer if designated by admin, else fallback to config
        effective_config = dict(config or {})
        effective_config["orientation"] = job_data.get("orientation", "portrait")
        if target_printer:
            effective_config["printer_name"] = target_printer
        success = await asyncio.to_thread(print_file_windows, local_file_path, copies, effective_config)
        
        if success:
            print(f"[✓] Print successful!")
            await websocket.send(json.dumps({
                "event": "PRINT_COMPLETED",
                "order_id": order_id
            }))
        else:
            print(f"[!] Print failed.")
            
    except Exception as e:
        print(f"[!] Processing error: {e}")
    finally:
        try:
            if os.path.exists(local_file_path):
                os.remove(local_file_path)
                print(f"[PRIVACY SAFE] Local temp file permanently deleted.")
        except Exception as err:
            print(f"[!] File delete error: {err}")

def ask_api_key(server_url: str, retry: bool = False) -> str:
    """Asks for the shop API key (every start). Nothing is saved to disk."""
    print(f"\n[*] Target Server: {server_url}")
    print("    Open your Admin Dashboard (/admin) and copy your Shop API Key.")
    while True:
        key = input(">> Enter or paste your Shop API Key and press Enter: " if not retry
                    else ">> Enter the correct Shop API Key: ").strip().strip('"').strip("'")
        if key:
            return key
        print("[!] API Key cannot be empty. Please try again.")


async def run_agent():
    print("=" * 60)
    print("      QR Se Print - Windows Desktop Auto-Print Agent       ")
    print("=" * 60)
    
    config = load_config()
    server_url = config.get("server_url", "https://qr-printing.onrender.com").rstrip("/")

    # Old versions saved the key inside agent_config.json – remove it so it is never reused silently.
    if config.pop("api_key", None):
        save_config(config)
        print("[i] Old saved API key was removed from agent_config.json (you will enter it each time now).")

    api_key = ask_api_key(server_url)

    ws_protocol = "wss" if server_url.startswith("https") else "ws"
    host_port = server_url.split("://")[-1]
    
    # SSL context for Render
    ssl_context = None
    if ws_protocol == "wss":
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
    print(f"[*] Server URL: {server_url}")
    print(f"[*] API Key   : {api_key[:8]}...{api_key[-4:] if len(api_key)>12 else ''}")
    print(f"[*] Connecting to server...")
    
    retry_delay = 3
    while True:
        ws_url = f"{ws_protocol}://{host_port}/ws/agent/{api_key}"
        try:
            async with websockets.connect(ws_url, ssl=ssl_context, ping_interval=20, ping_timeout=20) as ws:
                print(f"\n[🟢 ONLINE] Connected successfully to {server_url}!")
                print(f"[*] Ready and waiting for incoming print jobs...\n")
                retry_delay = 3
                
                while True:
                    raw_msg = await ws.recv()
                    data = json.loads(raw_msg)
                    if data.get("event") == "PRINT_JOB":
                        await handle_print_job(data.get("data", {}), server_url, api_key, ws, config)
                        
        except Exception as e:
            err_msg = str(e)
            if "403" in err_msg or "1008" in err_msg or "rejected" in err_msg:
                print("\n[ERROR] Server rejected this API Key!")
                api_key = ask_api_key(server_url, retry=True)
                print("[OK] Reconnecting...")
                continue
            elif "ConnectionRefused" in err_msg or "WinError 10061" in err_msg:
                print(f"\r[OFFLINE] Server not reachable. Retrying in {retry_delay}s...", end="")
            else:
                print(f"\r[OFFLINE] Connection issue ({err_msg}). Retrying in {retry_delay}s...", end="")
                
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay + 2, 20)

if __name__ == "__main__":
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        print("\n[*] Agent stopped by user.")
