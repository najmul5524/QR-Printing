import os
import sys
import time
import json
import asyncio
import tempfile
import urllib.request
import ssl
import websockets

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(AGENT_DIR, "agent_config.json")

DEFAULT_CONFIG = {
    "server_url": "https://qr-printing.onrender.com",
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
                print(f"[🖨️] লোকাল প্রিন্টারে পাঠানো হচ্ছে: {target_printer} ({os.path.basename(file_path)})")
                
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
                print(f"[🖨️] ডিফল্ট প্রিন্টারে পাঠানো হচ্ছে (os.startfile): {os.path.basename(file_path)}")
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
    print(f"[⚡] নতুন প্রিন্ট অর্ডার এসেছে! Order #{order_id} ({filename})")
    print(f"========================================================")
    
    temp_dir = tempfile.gettempdir()
    local_file_path = os.path.join(temp_dir, f"qsp_{order_id}_{filename}")
    
    try:
        print(f"[১/৩] ক্লাউড সার্ভার থেকে ফাইল ডাউনলোড হচ্ছে...")
        urllib.request.urlretrieve(full_url, local_file_path)
        print(f"[✓] ডাউনলোড সম্পন্ন ({os.path.getsize(local_file_path)} bytes)")
        
        print(f"[২/৩] প্রিন্টারে স্বয়ংক্রিয় কমান্ড পাঠানো হচ্ছে...")
        success = print_file_windows(local_file_path)
        
        if success:
            print(f"[✓] প্রিন্ট সফল হয়েছে!")
            await websocket.send(json.dumps({
                "event": "PRINT_COMPLETED",
                "order_id": order_id
            }))
        else:
            print(f"[!] প্রিন্ট ব্যর্থ হয়েছে।")
            
    except Exception as e:
        print(f"[!] প্রসেসিং ত্রুটি: {e}")
    finally:
        try:
            if os.path.exists(local_file_path):
                os.remove(local_file_path)
                print(f"[🔒] গোপনীয়তা নিশ্চিত: পিসি থেকে ফাইল স্থায়ীভাবে মুছে দেওয়া হয়েছে।")
        except Exception as err:
            print(f"[!] ফাইল মুছতে সমস্যা: {err}")

async def run_agent():
    print("=" * 60)
    print("      QR Se Print - Windows Desktop Auto-Print Agent       ")
    print("=" * 60)
    
    config = load_config()
    server_url = config.get("server_url", "https://qr-printing.onrender.com").rstrip("/")
    api_key = config.get("api_key", "").strip()
    
    # If API key is not configured or default, prompt user
    while not api_key or api_key == "qsp_demo_key_here":
        print(f"\n[*] বর্তমান সার্ভার: {server_url}")
        print("    আপনার ড্যাশবোর্ডে (/admin) লগইন করে ওপরের হলুদ বক্স থেকে API Key কপি করুন।")
        entered_key = input("👉 আপনার দোকানের API Key টি এখানে পেস্ট করে Enter চাপুন: ").strip()
        if entered_key:
            api_key = entered_key
            config["api_key"] = api_key
            save_config(config)
            print(f"[✓] API Key সংরক্ষিত হয়েছে!\n")
            break
        else:
            print("[!] API Key ফাঁকা রাখা যাবে না। আবার চেষ্টা করুন।")
            
    ws_protocol = "wss" if server_url.startswith("https") else "ws"
    host_port = server_url.split("://")[-1]
    
    # SSL context for Render
    ssl_context = None
    if ws_protocol == "wss":
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
    print(f"[*] সার্ভার: {server_url}")
    print(f"[*] শপ API Key: {api_key[:8]}...{api_key[-4:] if len(api_key)>12 else ''}")
    print(f"[*] সংযোগ স্থাপন করা হচ্ছে...")
    
    retry_delay = 3
    while True:
        ws_url = f"{ws_protocol}://{host_port}/ws/agent/{api_key}"
        try:
            async with websockets.connect(ws_url, ssl=ssl_context, ping_interval=20, ping_timeout=20) as ws:
                print(f"\n[🟢 ONLINE] সফলভাবে সার্ভারের সাথে সংযুক্ত হয়েছে!")
                print(f"[*] নতুন প্রিন্ট অর্ডারের জন্য অপেক্ষা করা হচ্ছে...\n")
                retry_delay = 3
                
                while True:
                    raw_msg = await ws.recv()
                    data = json.loads(raw_msg)
                    if data.get("event") == "PRINT_JOB":
                        await handle_print_job(data.get("data", {}), server_url, api_key, ws)
                        
        except (websockets.exceptions.InvalidStatusCode, websockets.exceptions.InvalidHandshake, Exception) as e:
            err_msg = str(e)
            if "403" in err_msg or "1008" in err_msg or "rejected" in err_msg:
                print(f"\n[❌ ভুল API Key] সার্ভার এই কী ({api_key}) গ্রহণ করেনি!")
                print(f"👉 দয়া করে {server_url}/admin পেজ থেকে আপনার বর্তমান সঠিক API Key টি কপি করে এখানে দিন:")
                new_key = input("সঠিক API Key: ").strip()
                if new_key:
                    api_key = new_key
                    config["api_key"] = api_key
                    save_config(config)
                    print(f"[+] নতুন কী সেভ হয়েছে। পুনরায় কানেক্ট হচ্ছে...")
                    continue
            elif "ConnectionRefused" in err_msg or "WinError 10061" in err_msg:
                print(f"\r[🔴 অফলাইন] লোকাল সার্ভার বন্ধ। সার্ভার চালু করুন। রিট্রাই হচ্ছে ({retry_delay}s)...", end="")
            else:
                print(f"\r[!] সার্ভারে সংযোগ বিচ্ছিন্ন ({err_msg})। রিট্রাই হচ্ছে ({retry_delay}s)...", end="")
                
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay + 2, 20)

if __name__ == "__main__":
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        print("\n[*] এজেন্ট বন্ধ করা হয়েছে।")
