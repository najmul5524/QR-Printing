import os
import sys
import time
import json
import asyncio
import tempfile
import urllib.request
import websockets

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(AGENT_DIR, "agent_config.json")

DEFAULT_CONFIG = {
    "server_url": "http://localhost:8000",
    "api_key": "qsp_demo_key_here"
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
    server_url = config.get("server_url", "http://localhost:8000").rstrip("/")
    api_key = config.get("api_key", "").strip()
    
    # If API key is not configured or default, prompt user
    if not api_key or api_key == "qsp_demo_key_here":
        print("\n[!] আপনার দোকানের API Key এখনো কনফিগার করা হয়নি।")
        print("    দোকানদার ড্যাশবোর্ড (/admin) এর হলুদ ব্যানারে আপনার API Key আছে।")
        entered_key = input("👉 আপনার API Key টি এখানে পেস্ট করে Enter চাপুন: ").strip()
        if entered_key:
            api_key = entered_key
            config["api_key"] = api_key
            save_config(config)
            print(f"[✓] API Key সফলভাবে সেভ হয়েছে!\n")
        else:
            print("[!] কোনো API Key দেওয়া হয়নি। প্রোগ্রাম বন্ধ হচ্ছে।")
            input("Press Enter to exit...")
            return
            
    ws_protocol = "wss" if server_url.startswith("https") else "ws"
    host_port = server_url.split("://")[-1]
    ws_url = f"{ws_protocol}://{host_port}/ws/agent/{api_key}"
    
    print(f"[*] সার্ভার: {server_url}")
    print(f"[*] শপ API Key: {api_key[:8]}...{api_key[-4:] if len(api_key)>12 else ''}")
    print(f"[*] সংযোগ চেষ্টা করা হচ্ছে...")
    
    retry_delay = 3
    first_attempt = True
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                print(f"\n[🟢 ONLINE] সফলভাবে সার্ভারের সাথে সংযুক্ত হয়েছে!")
                print(f"[*] নতুন প্রিন্ট অর্ডারের জন্য অপেক্ষা করা হচ্ছে...\n")
                retry_delay = 3
                first_attempt = False
                
                while True:
                    raw_msg = await ws.recv()
                    data = json.loads(raw_msg)
                    if data.get("event") == "PRINT_JOB":
                        await handle_print_job(data.get("data", {}), server_url, api_key, ws)
                        
        except (websockets.exceptions.InvalidStatusCode, websockets.exceptions.NegotiationError) as e:
            print(f"\n[❌ ত্রুটি] API Key সঠিক নয়! ড্যাশবোর্ড থেকে সঠিক কী দিন।")
            config["api_key"] = "qsp_demo_key_here"
            save_config(config)
            input("Press Enter to exit...")
            return
        except (ConnectionRefusedError, OSError) as e:
            if "localhost" in server_url or "127.0.0.1" in server_url:
                print(f"\r[🔴 অফলাইন] লোকাল সার্ভার ({server_url}) বন্ধ আছে। আগে 'python main.py' চালু করুন। প্রতি {retry_delay}s পর রিট্রাই হবে...", end="")
            else:
                print(f"\r[🔴 অফলাইন] ইন্টারনেটে সংযোগ পাওয়া যায়নি ({e})। রিট্রাই হচ্ছে ({retry_delay}s)...", end="")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay + 2, 20)
        except Exception as e:
            print(f"\r[!] সংযোগ বিচ্ছিন্ন ({e})। পুনরায় চেষ্টা চলছে ({retry_delay}s)...", end="")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay + 2, 20)

if __name__ == "__main__":
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        print("\n[*] এজেন্ট বন্ধ করা হয়েছে।")
