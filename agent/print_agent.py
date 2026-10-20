import os
import sys
import time
import json
import asyncio
import tempfile
import urllib.request
import ssl
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

DEFAULT_CONFIG = {
    "server_url": "https://qr-se-print.onrender.com",
    "api_key": ""
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

def print_file_windows(file_path: str, printer_name: str = None) -> bool:
    """
    Prints a file silently to Windows printer.
    Uses win32api/win32print if available, or native os.startfile.
    """
    try:
        if sys.platform == "win32":
            try:
                import win32api
                import win32print
                
                target_printer = printer_name or win32print.GetDefaultPrinter()
                print(f"[PRINT] Sending to local printer: {target_printer} ({os.path.basename(file_path)})")
                
                win32api.ShellExecute(
                    0,
                    "printto" if printer_name else "print",
                    file_path,
                    f'"{target_printer}"' if printer_name else None,
                    ".",
                    0
                )
                time.sleep(3)
                return True
            except ImportError:
                print(f"[PRINT] Sending to default printer (os.startfile): {os.path.basename(file_path)}")
                os.startfile(file_path, "print")
                time.sleep(3)
                return True
        else:
            print(f"[*] Simulating print for '{file_path}' (Non-Windows platform)")
            time.sleep(2)
            return True
    except Exception as e:
        print(f"[!] Print error: {e}")
        return False

async def handle_print_job(job_data: dict, server_url: str, api_key: str, websocket):
    order_id = job_data["order_id"]
    download_rel_url = job_data["download_url"]
    filename = job_data.get("filename", "print_doc")
    
    full_url = f"{server_url.rstrip('/')}{download_rel_url}?key={api_key}"
    print(f"\n========================================================")
    print(f"[+] NEW PRINT ORDER: Order #{order_id} ({filename})")
    print(f"========================================================")
    
    temp_dir = tempfile.gettempdir()
    local_file_path = os.path.join(temp_dir, f"qsp_{order_id}_{filename}")
    
    try:
        print(f"[1/3] Downloading file from cloud...")
        urllib.request.urlretrieve(full_url, local_file_path)
        print(f"[✓] Download complete ({os.path.getsize(local_file_path)} bytes)")
        
        print(f"[2/3] Sending to printer...")
        success = print_file_windows(local_file_path)
        
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

def prompt_login_or_key(server_url: str, config: dict) -> str:
    print("\n" + "=" * 60)
    print("      QR Se Print - Agent Authentication Setup")
    print("=" * 60)
    print(f"Server: {server_url}")
    print("\nChoose an option to connect your printer:")
    print(" [1] Login with Shop Mobile & Password (মোবাইল ও পাসওয়ার্ড দিয়ে লগইন)")
    print(" [2] Paste Shop API Key (API Key পেস্ট করুন)")
    print("-" * 60)
    
    choice = input("Enter choice [1 or 2, default=1]: ").strip()
    if choice == "2":
        while True:
            k = input(">> Paste your Shop API Key: ").strip()
            if k:
                config["api_key"] = k
                save_config(config)
                print("[OK] API Key saved to agent_config.json!\n")
                return k
            print("[!] API Key cannot be empty.")
    else:
        while True:
            phone = input(">> Shop Mobile Number (যেমন: 01700000000): ").strip()
            password = input(">> Shop Password (যেমন: 123456): ").strip()
            if not phone or not password:
                print("[!] Mobile and password cannot be empty.")
                continue
                
            print(f"[*] Authenticating with {server_url}...")
            try:
                login_endpoint = f"{server_url.rstrip('/')}/api/agent/login"
                req_data = json.dumps({"phone": phone, "password": password}).encode("utf-8")
                req = urllib.request.Request(
                    login_endpoint,
                    data=req_data,
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res_body = json.loads(resp.read().decode("utf-8"))
                    if res_body.get("success"):
                        new_key = res_body["api_key"]
                        shop_name = res_body.get("shop_name", "Shop")
                        config["api_key"] = new_key
                        save_config(config)
                        print(f"[OK] Login successful! Welcome, {shop_name}")
                        print(f"[OK] Config saved to agent_config.json!\n")
                        return new_key
            except urllib.error.HTTPError as he:
                print(f"[!] Login failed: Invalid mobile or password (HTTP {he.code}). Try again.\n")
            except Exception as ex:
                print(f"[!] Connection error: {ex}. Try again.\n")

async def run_agent():
    print("=" * 60)
    print("      QR Se Print - Windows Desktop Auto-Print Agent       ")
    print("=" * 60)
    
    config = load_config()
    server_url = config.get("server_url", "https://qr-se-print.onrender.com").rstrip("/")
    api_key = config.get("api_key", "").strip()
    
    # If API key is not configured, prompt user
    if not api_key:
        api_key = prompt_login_or_key(server_url, config)
            
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
                        await handle_print_job(data.get("data", {}), server_url, api_key, ws)
                        
        except Exception as e:
            err_msg = str(e)
            if "403" in err_msg or "1008" in err_msg or "rejected" in err_msg:
                print(f"\n[ERROR] Server rejected this API Key ({api_key})!")
                api_key = prompt_login_or_key(server_url, config)
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
    except Exception as fatal_err:
        import traceback
        err_details = traceback.format_exc()
        print(f"\n[FATAL ERROR] Agent crashed unexpectedly:")
        print(err_details)
        
        # Write error to a log file next to the agent
        log_path = os.path.join(AGENT_DIR, "agent_error.log")
        try:
            with open(log_path, "w", encoding="utf-8") as lf:
                lf.write(err_details)
            print(f"[*] Error details saved to: {log_path}")
        except Exception:
            pass
        
        input("\nPress Enter to close...")
