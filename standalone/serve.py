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
import os
import socket
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(os.path.dirname(HERE), "backend")

# 必須在 import app 之前設定 —— config.py 是在 import 當下讀環境變數的
os.environ["PM_MODE"] = "standalone"
sys.path.insert(0, BACKEND)

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
    sys.exit(main())
