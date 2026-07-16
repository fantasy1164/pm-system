# -*- coding: utf-8 -*-
"""單機版啟動器 —— 由 start.bat 呼叫,也可直接 python serve.py 執行。

做四件 .bat 做不好的事:
1. 強制 PM_MODE=standalone (在 import app 之前設好,config.py 才讀得到)
2. port 被佔用時自動換 port,而不是吐一整片 traceback
3. 等後端真的能連了才開瀏覽器 (直接開會看到「無法連線」)
4. 用 waitress 而非 gunicorn —— gunicorn 依賴 fcntl,Windows 上跑不起來

環境變數 (都可不設):
  PM_PORT       指定 port (預設 5000,被佔用則往後找)
  PM_NO_BROWSER =1 不自動開瀏覽器
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

# PyInstaller 單檔 exe:backend 模組已打包在執行檔內,不需要調 sys.path;
# 原始碼執行 (start.bat / python serve.py) 才需要把 backend 加進搜尋路徑。
FROZEN = bool(getattr(sys, "frozen", False))
if not FROZEN:
    HERE = os.path.dirname(os.path.abspath(__file__))
    BACKEND = os.path.join(os.path.dirname(HERE), "backend")
    sys.path.insert(0, BACKEND)

# 必須在 import app 之前設定 —— config.py 是在 import 當下讀環境變數的
os.environ["PM_MODE"] = "standalone"

HOST = "127.0.0.1"          # 只綁本機:不對區網開放,不需要防火牆例外


def find_port(preferred):
    """回傳可用的 port;preferred 被佔用就依序往後找。"""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    return None


def existing_instance(preferred):
    """掃描 port 區間,若本系統已在執行,回傳其網址。

    為什麼需要:使用者點兩下 exe 之後忘了,又點一次 —— 沒有這個檢查,
    第二份會換個 port 再開一次同一顆 SQLite,兩個備份執行緒互相踩。
    正確行為是:發現已經在跑,就只把瀏覽器帶到既有的那份,然後退出。

    判定方式:對 /api/health 發 HTTP 請求,回應是帶有 status 與 backup
    欄位的 JSON 才算是本系統 (503 也算 —— 那是還在開機的本系統)。
    """
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex((HOST, port)) != 0:
                continue
        try:
            r = urllib.request.urlopen(
                f"http://{HOST}:{port}/api/health", timeout=1)
            raw = r.read()
        except urllib.error.HTTPError as e:   # 503 = 本系統開機中,一樣讀 body
            raw = e.read()
        except Exception:
            continue                           # 不是 HTTP 服務,跳過
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if isinstance(body, dict) and "status" in body and "backup" in body:
            return f"http://{HOST}:{port}/"
    return None


def open_when_ready(url, port):
    """等後端真的接受連線了才開瀏覽器。"""
    for _ in range(100):                     # 最多等 10 秒
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex((HOST, port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.1)


def main():
    preferred = int(os.environ.get("PM_PORT", "5000"))

    running = existing_instance(preferred)
    if running:
        print(f"\n系統已經在執行中 → {running}")
        print("為你開啟瀏覽器,本視窗即將自動關閉。\n")
        if os.environ.get("PM_NO_BROWSER") != "1":
            webbrowser.open(running)
        time.sleep(2)
        return 0

    port = find_port(preferred)
    if port is None:
        print(f"\n找不到可用的 port ({preferred}-{preferred + 19} 都被佔用)。")
        print("請關閉佔用的程式,或設定 PM_PORT 指定其他 port。\n")
        return 1
    if port != preferred:
        print(f"提示:port {preferred} 已被其他程式佔用,改用 {port}。")

    try:
        from waitress import serve
    except ImportError:
        if FROZEN:
            print("\n執行檔內容不完整 (缺 waitress) —— 請重新下載或重新打包。\n")
        else:
            print("\n找不到 waitress —— 請先執行 install.bat 安裝相依套件。\n")
        return 1

    try:
        from app import app, startup
    except Exception as e:
        print(f"\n後端載入失敗:{e}\n")
        return 1

    startup()               # 建立/開啟資料庫,啟動備份執行緒

    import config
    url = f"http://{HOST}:{port}/"
    print()
    print("=" * 58)
    print("  專案管理系統 — 單機版")
    print("=" * 58)
    print(f"  網址    {url}")
    print(f"  資料庫  {config.DB_PATH}")
    print(f"  備份    {config.LOCAL_DRIVE_DIR}")
    print()
    print("  離線運作:不需網路,不會對外連線。")
    print("  關閉本視窗即停止服務 (資料已存檔,不會遺失)。")
    print("=" * 58)
    print()

    if os.environ.get("PM_NO_BROWSER") != "1":
        threading.Thread(target=open_when_ready, args=(url, port),
                         daemon=True).start()

    try:
        serve(app, host=HOST, port=port, threads=8)
    except KeyboardInterrupt:
        pass
    finally:
        # 關機兜底:把尚未備份的變更 flush 一次 (比照 gunicorn 的 worker_exit)
        try:
            from app import BACKUP
            BACKUP.flush_if_dirty()
        except Exception as e:
            print(f"關機備份失敗 (資料庫本身不受影響): {e}")
    print("\n服務已停止。")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    # 單檔 exe 出錯就結束的話,主控台視窗會瞬間消失,使用者看不到錯誤訊息。
    # 原始碼執行 (start.bat 內有 pause) 不需要這一段。
    if code and FROZEN:
        try:
            input("\n按 Enter 鍵關閉視窗 ...")
        except EOFError:
            pass
    sys.exit(code)
