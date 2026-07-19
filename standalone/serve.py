# -*- coding: utf-8 -*-
"""單機版啟動器 —— 由 start.bat 呼叫,也可直接 python serve.py 執行,
單一 exe (PyInstaller) 也是以本檔為進入點。

做四件 .bat 做不好的事:
1. 強制 PM_MODE=standalone (在 import app 之前設好,config.py 才讀得到)
2. port 被佔用時自動換 port,而不是吐一整片 traceback
3. 等後端真的能連了才開瀏覽器 (直接開會看到「無法連線」)
4. 用 waitress 而非 gunicorn —— gunicorn 依賴 fcntl,Windows 上跑不起來

## 有無主控台 (黑視窗) 的兩種形態

單一 exe 以 console=False 打包,執行時不會有黑視窗。但黑視窗原本身兼三職,
拿掉它就得把三件事都補回來,否則使用者會陷入「不知道怎麼停、看不到錯誤」:

| 黑視窗原本負責 | 無主控台時由誰接手 |
| 停止服務 (關視窗) | 系統匣圖示 →「停止並結束」 |
| 顯示錯誤訊息       | 記錄檔 logs/pm.log + 致命錯誤跳訊息框 |
| 顯示網址/資料路徑  | 系統匣圖示 →「開啟系統」「開啟資料夾」 |

判定依據是 `sys.stdout is None` —— PyInstaller 的 windowed 模式如此,
原始碼執行 (start.bat) 則否,兩者共用本檔同一份程式碼。

環境變數 (都可不設):
  PM_PORT       指定 port (預設 5000,被佔用則往後找)
  PM_NO_BROWSER =1 不自動開瀏覽器
  PM_NO_TRAY    =1 不建立系統匣圖示 (除錯用)
  PM_DATA_DIR   指定資料存放位置 (預設 %LOCALAPPDATA%\\專案管理系統)
  PM_NO_SPLASH  =1 不顯示啟動畫面的動畫 (除錯用)
"""
import json
import logging
import logging.handlers
import os
import shutil
import socket
import subprocess
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

# 無主控台時 sys.stdout/stderr 都是 None:print 會被 Python 靜默忽略 (無害),
# 但 logging 的預設 StreamHandler 會寫進 None 而噴錯 —— 因此下面要改導記錄檔。
CONSOLE = sys.stdout is not None and sys.stderr is not None

if CONSOLE:
    # 有主控台時,把輸出編碼釘死成 UTF-8。
    #
    # 原因:輸出一旦被轉向 (start.bat > log.txt、管線、CI 擷取),Python 就不
    # 走主控台的 Unicode 介面,改用系統地區編碼 —— 英文版 Windows 是 cp1252,
    # 印不出中文,直接 UnicodeEncodeError。本檔的訊息全是中文,等於「因為要
    # 印一句提示而讓整個系統啟動失敗」。errors="replace" 再保一層:寧可某個
    # 字變成問號,也不要為了一句訊息而死。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

HOST = "127.0.0.1"          # 只綁本機:不對區網開放,不需要防火牆例外

import config                # 只讀環境變數,不牽動 logging,可安全提早 import


def say(msg=""):
    """有主控台才輸出 —— 無主控台時這些話沒有觀眾,由記錄檔接手。"""
    if CONSOLE:
        print(msg, flush=True)


def setup_logging():
    """無主控台時把日誌導向檔案。

    必須在 import app/persistence 之前呼叫:persistence 模組在 import 當下
    會執行 logging.basicConfig(),那一版預設寫 sys.stderr —— 無主控台時
    sys.stderr 是 None,寫入會噴 handler error。搶先設定好 root handler,
    persistence 的 basicConfig 就會變成 no-op (basicConfig 對已有 handler
    的 root logger 不做事),它照樣 logging.getLogger 拿到寫檔的設定。
    """
    if CONSOLE:
        return None
    log_dir = os.path.join(config.STANDALONE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "pm.log")
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=1_048_576, backupCount=3, encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[handler])
    return path


def alert(title, msg):
    """致命錯誤的出口:有主控台就印,沒有就跳 Windows 訊息框。

    沒有這個的話,無主控台的 exe 遇到錯誤會「點兩下什麼都沒發生」——
    對使用者而言這是最糟的失敗模式:沒有任何線索可回報。
    """
    splash_close()          # 啟動畫面是永遠置頂的,不收掉會蓋住這個對話框
    say(f"\n{msg}\n")
    if CONSOLE or os.name != "nt":
        return
    try:
        import ctypes
        # 0x10 = MB_ICONERROR, 0x40000 = MB_TOPMOST
        ctypes.windll.user32.MessageBoxW(None, msg, title, 0x10 | 0x40000)
    except Exception:
        pass                 # 連訊息框都失敗就算了,記錄檔仍有完整 traceback


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
    """等後端真的接受連線了,才開瀏覽器並收掉啟動畫面。

    兩件事綁在一起是刻意的:啟動畫面必須撐到瀏覽器畫面出得來為止。太早收掉,
    使用者會盯著空白桌面猜「到底好了沒」;晚一點收,他看到的是啟動畫面直接
    被瀏覽器接手 —— 沒有任何一刻是沒有回饋的。
    """
    for _ in range(600):                     # 最多等 60 秒 (首次啟動可能較久)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex((HOST, port)) == 0:
                if os.environ.get("PM_NO_BROWSER") != "1":
                    webbrowser.open(url)
                time.sleep(0.6)              # 讓瀏覽器視窗先浮上來再收畫面
                splash_close()
                return
        time.sleep(0.1)
    # 等不到服務:也要收掉,不能讓啟動畫面永遠掛在桌面上
    splash_close()


# ---------------------------------------------------------------- 啟動畫面
# 單檔 exe 從點兩下到瀏覽器出現要好幾秒 (解壓縮 + 開資料庫 + 起服務)。這段
# 期間畫面上什麼都沒有 —— 使用者的合理反應是「壞了?」然後再點兩下。啟動畫面
# 填滿這段空窗,並且撐到瀏覽器真的畫出來為止才收掉。
#
# 為什麼不用 PyInstaller 內建的 Splash (它能在「解壓縮階段」就顯示,涵蓋更早):
# 那個畫面是 bootloader 在解壓縮途中載入 Tcl 畫出來的,而 Tcl 的相依 DLL
# (zlib1.dll、VC 執行期…) 那時還沒被解出來,Windows 只好退去 System32 撈。
# 開發機與 CI runner 裝了 VS 撈得到,使用者的乾淨 Windows 撈不到 —— 結果是
# exe 在「我們測得到的每一台機器上」都好好的,到了使用者手上點兩下就跳
# 「Failed to load Tcl DLL」然後死掉。啟動畫面只是裝飾,不值得為它冒
# 「在乾淨機器上開不起來」的風險。
#
# 這裡的視窗則是等 Python 起來之後才建立 —— 那時全部檔案都解壓完了,同一顆
# tcl86t.dll 的相依就躺在 _MEI 目錄裡,不必向系統求援。代價是解壓縮那幾秒
# 沒有畫面,由 claim_single_instance() 去擋「以為沒反應而多點幾下」的後果。
_SPLASH_STOP = threading.Event()


def licenses_dir():
    """授權文件的位置。打包後在 _MEIPASS/standalone/licenses/。

    exe 內已無任何 copyleft 元件,但這份東西還是得帶著:BSD-3-Clause、
    Apache-2.0、ZPL 這些寬鬆授權同樣要求「以二進位形式散布時,必須隨附
    著作權聲明」。單機版是離線的,聲明不能只留在 GitHub 上,得跟著 exe 走。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (os.path.join(config.REPO_ROOT, "standalone", "licenses"),
                 os.path.join(os.path.dirname(here), "licenses")):
        if os.path.isdir(base):
            return base
    return None


def frame_paths():
    """逐格圖的位置。打包後在 _MEIPASS/standalone/,原始碼執行時在 build/。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (os.path.join(config.REPO_ROOT, "standalone"),
                 os.path.join(here, "build")):
        paths = [os.path.join(base, f"splash_{i:02d}.png") for i in range(99)]
        paths = [p for p in paths if os.path.exists(p)]
        if paths:
            return paths
    return []


def splash_animate():
    """自製的動畫視窗:無邊框、置頂、螢幕正中、背景透明。

    透明是靠 Windows 的色鍵:圖裡的洋紅像素會變成透明,因此畫面上只看得到
    小人與文字,沒有方框 (見 make_splash.py)。非 Windows 沒有這個機制,
    會直接看到洋紅底 —— 單機版只出 Windows,無妨。

    整個函式跑在自己的執行緒裡,而且所有 Tk 呼叫都留在這個執行緒內
    (Tk 不是執行緒安全的,但「一個執行緒自己建、自己用」是可以的)。
    主執行緒同時在做啟動的粗活,結束後還要讓給系統匣的訊息迴圈。
    """
    paths = frame_paths()
    if not paths or os.environ.get("PM_NO_SPLASH") == "1":
        return
    try:
        import tkinter as tk
    except Exception as e:
        logging.getLogger("splash").warning("tkinter 無法載入,不做動畫: %s", e)
        return
    try:
        root = tk.Tk()
        root.overrideredirect(True)          # 無標題列、無邊框
        root.attributes("-topmost", True)
        try:
            root.attributes("-transparentcolor", "magenta")
        except tk.TclError:
            pass                             # 非 Windows:沒有色鍵,看得到底色
        frames = [tk.PhotoImage(file=p) for p in paths]
        w, h = frames[0].width(), frames[0].height()
        label = tk.Label(root, image=frames[0], bd=0,
                         highlightthickness=0, bg="magenta")
        label.pack()
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 2
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.update()                        # 先把第一格畫出來再進迴圈

        def tick(i=0):
            if _SPLASH_STOP.is_set():
                root.destroy()
                return
            label.configure(image=frames[i % len(frames)])
            root.after(110, tick, i + 1)

        root.after(0, tick)
        root.mainloop()
    except Exception as e:
        logging.getLogger("splash").warning("啟動畫面動畫失敗: %s", e)


def splash_close():
    """收掉啟動畫面。可重複呼叫;任何離場路徑都必須先經過這裡 —— 包括錯誤
    跳窗 (alert),否則對話框會被永遠置頂的啟動畫面蓋住。"""
    _SPLASH_STOP.set()


# ---------------------------------------------------------------- 只准一份
_MUTEX = None           # 保留參照:控制代碼被回收 = 鎖跟著沒了


def claim_single_instance():
    """宣告「我是唯一的實例」。回傳 False 表示已經有另一份在跑或正在啟動。

    為什麼不能只靠 existing_instance() 掃 port:那要等對方「綁上 port」才看得到。
    而解壓縮那幾秒畫面上什麼都沒有,使用者很自然會再點兩下 —— 兩個行程於是
    同時在解壓縮、同時掃 port、都沒掃到,然後各自開一套服務,對同一顆 SQLite
    跑兩條備份執行緒。互斥鎖在行程一開始就宣告,不必等 port,才擋得住這個競態。
    (原本這個空窗由啟動畫面遮著,砍掉它之後,這道鎖就從「保險」變成必要。)

    刻意失敗即放行:鎖只是防呆,建不出來時寧可讓系統照常啟動。
    """
    global _MUTEX
    if os.name != "nt":
        return True                 # 只在 Windows 出貨;其他平台不擋
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.restype = wintypes.HANDLE
        k.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                   wintypes.LPCWSTR]
        # Local\ 前綴 = 只在目前這個登入工作階段內唯一。用 Global\ 會讓
        # 同一台機器上的另一個 Windows 使用者被擋住 —— 他們的資料各自獨立
        # (%LOCALAPPDATA% 是每個帳號一份),本來就該能各跑各的。
        h = k.CreateMutexW(None, False, "Local\\pm-system-standalone")
        if not h:
            return True
        if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
            return False
        _MUTEX = h                  # 不釋放:行程結束時作業系統自然回收
        return True
    except Exception as e:
        logging.getLogger("serve").warning("互斥鎖建立失敗,不擋: %s", e)
        return True


def open_folder(path):
    try:
        if os.name == "nt":
            os.startfile(path)                      # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        logging.getLogger("tray").warning("開啟資料夾失敗: %s", e)


def flush_backup():
    """關機兜底:把尚未備份的變更 flush 一次 (比照 gunicorn 的 worker_exit)。"""
    try:
        from app import BACKUP
        BACKUP.flush_if_dirty()
    except Exception as e:
        logging.getLogger("serve").warning(
            "關機備份失敗 (資料庫本身不受影響): %s", e)
        say(f"關機備份失敗 (資料庫本身不受影響): {e}")


def shutdown(reason):
    """優雅收攤:存好資料再退場。"""
    logging.getLogger("serve").info("停止服務 (%s)", reason)
    splash_close()
    flush_backup()
    say("\n服務已停止。")
    os._exit(0)             # waitress 的執行緒不會自己收攤,直接落幕


# ---------------------------------------------------------------- 停止的路
# 系統匣的「停止並結束」在同一個行程內,直接呼叫函式就好。下面這套是給
# 「第二次點兩下 exe」用的 —— 那是另一個行程,得有辦法叫停已經在跑的那個。
#
# 為什麼用一個檔案當旗標,而不是 HTTP 端點:端點會讓任何本機網頁都能關掉
# 使用者的系統 (瀏覽器裡的 JS 打得到 127.0.0.1),要擋就得再設計 token,
# 平白多出攻擊面。也不用 os.kill:Windows 沒有 SIGTERM 語意,os.kill 實際上
# 是硬殺,會跳過關機備份。放個旗標讓執行中的自己看到、自己收攤,最單純。
def stop_file():
    return os.path.join(config.STANDALONE_DIR, ".stop-request")


def watch_stop_file():
    """執行中的實例:看到停止旗標就自己收攤。"""
    p = stop_file()
    try:
        os.remove(p)        # 清掉上次當掉時留下的,否則這次一啟動就自殺
    except OSError:
        pass
    while True:
        time.sleep(0.5)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
            shutdown("控制面板要求停止")


def request_stop(port):
    """另一個行程:放下停止旗標,等對方真的收攤。"""
    try:
        with open(stop_file(), "w"):
            pass
    except OSError as e:
        logging.getLogger("panel").warning("無法寫入停止旗標: %s", e)
        return False
    for _ in range(40):                     # 最多等 20 秒
        time.sleep(0.5)
        if existing_instance(port) is None:
            return True
    return False


def control_panel(url, port):
    """第二次點兩下 exe 時跳出的小面板。

    這不是系統匣的替代品,是它的安全網:系統匣圖示常被 Windows 摺進「^」裡,
    使用者找不到;遠端桌面或精簡版 Windows 甚至根本做不出系統匣。而對不懂
    電腦的人來說,「再點兩下那個 exe」往往是他唯一會的動作 —— 那個動作至少
    要能把「停止並結束」交到他手上,而不是要他去開工作管理員。
    """
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as e:
        logging.getLogger("panel").warning("控制面板開不起來: %s", e)
        return False

    root.title("專案管理系統")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    tk.Label(root, text="系統已經在執行中", font=("", 12, "bold")).pack(
        padx=18, pady=(14, 2))
    status = tk.Label(root, text=url, fg="#3a6ea5")
    status.pack(padx=18)

    def do_stop():
        status.config(text="停止中,請稍候…", fg="#8a6d1f")
        for b in buttons:
            b.config(state="disabled")
        root.update()
        ok = request_stop(port)
        status.config(
            text="系統已停止。" if ok else "停止逾時 —— 請用工作管理員結束。",
            fg="#3f7d3f" if ok else "#a33333")
        root.update()

    box = tk.Frame(root)
    box.pack(padx=18, pady=10)
    buttons = []
    for text, fn in (("開啟系統", lambda: webbrowser.open(url)),
                     ("開啟資料夾", lambda: open_folder(config.STANDALONE_DIR)),
                     ("停止並結束", do_stop)):
        b = tk.Button(box, text=text, width=11, command=fn)
        b.pack(side="left", padx=4)
        buttons.append(b)
    tk.Button(root, text="關閉", width=8, command=root.destroy).pack(pady=(0, 12))

    root.update_idletasks()
    root.geometry(
        f"+{(root.winfo_screenwidth() - root.winfo_width()) // 2}"
        f"+{(root.winfo_screenheight() - root.winfo_height()) // 2}")
    root.mainloop()
    return True


def run_tray(url, on_quit):
    """系統匣圖示 (原生 Win32,見 tray_win32.py)。

    回傳 True = 使用者從選單結束了服務;False = 這台機器做不出系統匣,
    呼叫端得自己撐著 —— 圖示只是裝飾,不能讓它的缺席拖垮服務。
    """
    if os.environ.get("PM_NO_TRAY") == "1":
        return False
    try:
        import tray_win32
    except Exception as e:
        logging.getLogger("tray").warning("系統匣模組載入失敗: %s", e)
        return False

    icon = os.path.join(config.REPO_ROOT, "standalone", "app.ico")
    if not os.path.exists(icon):
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "build", "app.ico")

    items = [("開啟系統", lambda: webbrowser.open(url)),
             ("開啟資料夾", lambda: open_folder(config.STANDALONE_DIR))]
    d = licenses_dir()
    if d:
        # 授權聲明:BSD/Apache/ZPL 等寬鬆授權同樣要求「以二進位形式散布時
        # 必須隨附著作權聲明」。這個選項就是那份聲明的去處。
        items.append(("關於與授權", lambda: open_folder(d)))
    items += [("-", None), ("停止並結束", lambda: None)]

    ok = tray_win32.run(icon, f"專案管理系統 — 執行中\n{url}", items)
    if ok and on_quit:
        on_quit()
    return ok


def legacy_dirs():
    """舊版 exe 可能把資料留在哪些地方 (依可能性排序)。

    v1 的 exe 把 data/backups/logs 放在 exe 旁邊,放不進去才退到
    %LOCALAPPDATA%\\pm-system。v2 起一律固定在 %LOCALAPPDATA%\\專案管理系統。
    """
    out = [os.path.dirname(os.path.abspath(sys.executable))]      # exe 旁邊
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    out.append(os.path.join(base, "pm-system"))                   # v1 的退路
    return out


def migrate_legacy_data():
    """把舊版留在別處的資料搬到現在的固定位置。

    為什麼非做不可:v1 的使用者資料在 exe 旁邊。位置一改,新版會在新家開一顆
    空白資料庫 —— 使用者看到的是「我的專案全不見了」,而檔案其實好端端躺在
    舊資料夾。這種「資料沒壞但使用者以為壞了」的情境,比真的壞掉更難處理。

    只在新家還沒有資料庫時才搬 (搬過一次就不會再搬),且絕不覆蓋既有資料。
    """
    if not FROZEN:
        return
    if os.path.exists(config.DB_PATH):
        return                                  # 新家已有資料,不動它
    log = logging.getLogger("migrate")
    for old in legacy_dirs():
        if os.path.abspath(old) == os.path.abspath(config.STANDALONE_DIR):
            continue
        if not os.path.exists(os.path.join(old, "data", "pm.sqlite")):
            continue
        say(f"發現舊版資料,搬移中:{old} → {config.STANDALONE_DIR}")
        log.info("搬移舊版資料 %s → %s", old, config.STANDALONE_DIR)
        # 只搬 data 與 backups —— 這兩個是不可再生的。舊 logs 沒有保留價值,
        # 而且新家的 pm.log 早在啟動時就被日誌系統開好了,搬過去只會同名打架
        # (跳過 → 舊資料夾清不空 → 刪不掉 → exe 旁邊繼續留一個 logs\)。
        for sub in ("data", "backups"):
            src = os.path.join(old, sub)
            dst = os.path.join(config.STANDALONE_DIR, sub)
            if not os.path.isdir(src):
                continue
            try:
                os.makedirs(dst, exist_ok=True)
                for name in os.listdir(src):
                    s, d = os.path.join(src, name), os.path.join(dst, name)
                    if os.path.exists(d):       # 保守:同名檔案在就跳過
                        continue
                    shutil.move(s, d)
                # 搬空了才刪 —— 有東西沒搬成功就把資料夾留著,不製造遺憾
                if not os.listdir(src):
                    os.rmdir(src)
            except OSError as e:
                log.warning("搬移 %s 失敗: %s", src, e)
                alert("專案管理系統",
                      f"舊版資料搬移失敗:{e}\n\n"
                      f"你的資料仍在:{old}\n"
                      f"請手動把該資料夾內的 data、backups 複製到:\n"
                      f"{config.STANDALONE_DIR}")
                return
        # 舊 logs 直接丟棄 (不可再生的東西都已搬完,留著只是散亂)
        shutil.rmtree(os.path.join(old, "logs"), ignore_errors=True)
        log.info("搬移完成")
        return


def main():
    log_path = setup_logging()
    threading.Thread(target=splash_animate, daemon=True).start()
    preferred = int(os.environ.get("PM_PORT", "5000"))

    running = existing_instance(preferred)
    if running is None and not claim_single_instance():
        # 另一份正在啟動 (多半還在解壓縮),它還沒綁上 port,所以掃不到。
        # 等它 —— 這比「再開一套」對使用者好,也保護了資料庫。
        say("\n另一個實例正在啟動,等待中…")
        for _ in range(120):                    # 最多等 60 秒
            time.sleep(0.5)
            running = existing_instance(preferred)
            if running:
                break
        else:
            # 等不到:對方可能已經死了,鎖是它留下的殘影。照常啟動,不要卡死使用者。
            logging.getLogger("serve").warning("等不到另一個實例,照常啟動")

    if running:
        splash_close()
        say(f"\n系統已經在執行中 → {running}")
        # 先把瀏覽器叫出來 —— 使用者點兩下就是想看到系統,別讓他多按一次。
        if os.environ.get("PM_NO_BROWSER") != "1":
            webbrowser.open(running)
        # 然後才是控制面板:它同時回答了「為什麼沒反應?」(系統早就在跑了)
        # 與「那我怎麼關掉它?」——後者原本只有系統匣圖示能回答,而那顆圖示
        # 常被 Windows 摺進「^」裡。
        port_running = int(running.rsplit(":", 1)[1].rstrip("/"))
        if not control_panel(running, port_running):
            say("為你開啟瀏覽器。\n")
            if CONSOLE:
                time.sleep(2)
        return 0

    port = find_port(preferred)
    if port is None:
        alert("專案管理系統",
              f"找不到可用的 port ({preferred}-{preferred + 19} 都被佔用)。\n\n"
              "請關閉佔用這些 port 的程式後再試一次。")
        return 1
    if port != preferred:
        say(f"提示:port {preferred} 已被其他程式佔用,改用 {port}。")

    try:
        from waitress import serve
    except ImportError:
        alert("專案管理系統",
              "執行檔內容不完整 (缺 waitress) —— 請重新下載或重新打包。"
              if FROZEN else
              "找不到 waitress —— 請先執行 install.bat 安裝相依套件。")
        return 1

    # 必須在 import app 之前:app 一載入就會建立/開啟 config.DB_PATH 的資料庫,
    # 那時再搬就來不及了 —— 新家已經有一顆空白 DB,搬移條件不成立。
    migrate_legacy_data()

    try:
        from app import app, startup, startup_scan
    except Exception as e:
        alert("專案管理系統", f"後端載入失敗:{e}")
        return 1

    try:
        startup()           # 建立/開啟資料庫,啟動備份執行緒
    except Exception as e:
        alert("專案管理系統", f"資料庫啟動失敗:{e}")
        return 1

    # 單機版沒有外部排程器,里程碑到期提醒改成「每次啟動掃一次」。放在 startup()
    # 之後 (資料庫已就緒)、serve() 之前;失敗只記錄、不擋服務 —— 提醒是加值,
    # 不能讓它拖垮整個系統的啟動。
    startup_scan()

    url = f"http://{HOST}:{port}/"
    say()
    say("=" * 58)
    say("  專案管理系統 — 單機版")
    say("=" * 58)
    say(f"  網址    {url}")
    say(f"  資料庫  {config.DB_PATH}")
    say(f"  備份    {config.LOCAL_DRIVE_DIR}")
    say()
    say("  離線運作:不需網路,不會對外連線。")
    say("  關閉本視窗即停止服務 (資料已存檔,不會遺失)。")
    say("=" * 58)
    say()
    logging.getLogger("serve").info("單機版啟動 %s (db=%s)", url, config.DB_PATH)

    # 不論要不要開瀏覽器都要跑:這個執行緒同時負責收掉啟動畫面。
    threading.Thread(target=open_when_ready, args=(url, port),
                     daemon=True).start()

    # 「第二次點兩下 exe」的控制面板要能叫停這個行程,靠的是這個看門執行緒。
    threading.Thread(target=watch_stop_file, daemon=True).start()

    def run_server():
        try:
            serve(app, host=HOST, port=port, threads=8)
        except KeyboardInterrupt:
            pass

    if CONSOLE:
        # 有黑視窗:視窗本身就是停止開關,維持原本的行為 (serve 佔住主執行緒)。
        try:
            run_server()
        finally:
            flush_backup()
        say("\n服務已停止。")
        return 0

    # 無黑視窗:服務丟到背景執行緒,主執行緒讓給系統匣的事件迴圈
    # (Windows 的系統匣訊息迴圈必須待在主執行緒)。
    threading.Thread(target=run_server, daemon=True).start()

    if run_tray(url, None):
        shutdown("使用者由系統匣結束")

    # 系統匣建不起來 (遠端桌面、精簡版 Windows)。服務照跑 —— 少了圖示總比
    # 整個系統打不開好。但「怎麼停止」不能因此變成無解:再點兩下 exe 就會
    # 跳出控制面板,那裡有「停止並結束」,不必去開工作管理員。
    logging.getLogger("serve").warning("無系統匣圖示,服務仍在執行:%s", url)
    if log_path:
        alert("專案管理系統",
              "系統已啟動,但這台電腦無法顯示系統匣圖示。\n\n"
              f"系統網址:{url}\n\n"
              "要停止系統,請再點兩下本程式,在跳出的視窗選「停止並結束」。\n"
              f"詳細記錄:{log_path}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        flush_backup()
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        import traceback
        detail = traceback.format_exc()
        try:
            logging.getLogger("serve").error("未預期的錯誤\n%s", detail)
        except Exception:
            pass
        if CONSOLE:
            traceback.print_exc()
        else:
            alert("專案管理系統", f"啟動時發生未預期的錯誤:\n\n{detail[-800:]}")
        code = 1
    # 有主控台的單檔 exe 出錯就結束的話,視窗會瞬間消失,使用者看不到錯誤訊息。
    # (無主控台時錯誤已走 alert 訊息框,不需要也不能等 input。)
    if code and FROZEN and CONSOLE:
        try:
            input("\n按 Enter 鍵關閉視窗 ...")
        except EOFError:
            pass
    sys.exit(code)
