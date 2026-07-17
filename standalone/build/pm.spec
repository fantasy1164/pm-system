# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包規格 —— 單機版單一 exe。

    pyinstaller --noconfirm --clean pm.spec

設計說明:
* 進入點是 standalone/serve.py —— 與 start.bat 走完全相同的程式,
  沒有另一套「exe 專用啟動器」,單機/線上/exe 三者共用同一套程式碼。
* pathex 加入 backend/,PyInstaller 的靜態分析因此能追到 app.py 及其
  相依套件 (flask、waitress、google-auth...),全部包進執行檔。
* schema.sql 與 frontend/ 以資料檔形式打包,執行時解壓到 sys._MEIPASS,
  backend/config.py 的 IS_FROZEN 分支會指向該處。
* console=False:不出現黑視窗。黑視窗原本兼任「停止開關」與「錯誤出口」,
  因此 serve.py 在無主控台時改以系統匣圖示 + 記錄檔 + 訊息框接手這兩件事
  (詳見 serve.py 的模組說明)。判定依據是 sys.stdout is None,
  start.bat 走原始碼、有主控台,行為完全不受影響。
* app.ico 同時作為 exe 圖示與系統匣圖示,故也需以資料檔打包一份。
* Splash():啟動畫面的第一段。它由 bootloader 在「解壓縮階段」就顯示出來 ——
  那是單檔 exe 最慢、使用者最容易以為沒反應的幾秒,也正是自製視窗補不到的
  空窗期 (自製視窗要等 Python 起來才畫得出來)。Python 起來後改由 serve.py
  的 Tk 視窗接手播放逐格動畫,兩段用同一批圖,交棒時畫面不會跳動。
* 這裡刻意「不」傳 text_pos:那個參數是文字圖層的開關,而只要文字圖層存在,
  bootloader 就會把正在解壓的檔名 (zlib1.dll…) 一個個寫上去。那是它內建的
  進度顯示,關不掉 —— 唯一的辦法就是根本不要文字圖層。
* onefile:a.binaries / a.datas 直接進 EXE(),不產生資料夾版。
"""
import glob
import json
import sys

from PyInstaller.utils.hooks import collect_submodules

HERE = SPECPATH                                   # standalone/build
STANDALONE = os.path.dirname(HERE)                # standalone
REPO = os.path.dirname(STANDALONE)                # repo 根目錄
BACKEND = os.path.join(REPO, "backend")
FRONTEND = os.path.join(REPO, "frontend")

# flask-limiter 的儲存後端 (limits 套件) 有動態載入,靜態分析抓不全,
# 全數列為 hidden imports —— 少了它,exe 啟動時會在 Limiter 初始化爆掉。
hidden = collect_submodules("limits") + collect_submodules("flask_limiter")

# 逐格圖用 glob 撈,但張數必須跟 make_splash.py 講好的一致 —— 否則少了幾張
# 只會讓動畫默默變短、變頓,exe 照樣產得出來,沒有人會發現。
_layout = json.load(open(os.path.join(HERE, "splash_layout.json"), encoding="utf-8"))
_frames = sorted(glob.glob(os.path.join(HERE, "splash_*.png")))
if len(_frames) != _layout["frames"]:
    raise SystemExit(
        f"啟動動畫應有 {_layout['frames']} 格,實際只找到 {len(_frames)} 格。"
        f"\n請重跑:python {os.path.join(HERE, 'make_splash.py')}")

# 系統匣不需要 hidden imports:tray_win32.py 只用標準庫的 ctypes,
# 靜態分析看得一清二楚 (這是自己寫那 200 行換來的另一個好處)。

a = Analysis(
    [os.path.join(STANDALONE, "serve.py")],
    pathex=[BACKEND],
    datas=[
        (os.path.join(BACKEND, "schema.sql"), "backend"),
        (FRONTEND, "frontend"),
        (os.path.join(HERE, "app.ico"), "standalone"),   # 系統匣圖示要讀它
        # 啟動動畫的逐格圖 (serve.py 的 Tk 視窗會讀它們)
        *[(f, "standalone") for f in _frames],
        # 授權聲明。exe 內已無 copyleft,但 BSD-3-Clause、Apache-2.0、ZPL
        # 這些寬鬆授權同樣要求「以二進位形式散布時須隨附著作權聲明」。
        # 單機版離線,聲明得跟著 exe 走;系統匣的「關於與授權」就是開它。
        (os.path.join(REPO, "THIRD_PARTY_NOTICES.md"), "standalone/licenses"),
        (os.path.join(REPO, "LICENSE"), "standalone/licenses"),
    ],
    hiddenimports=hidden,
    # tkinter 不可排除 —— 啟動動畫與控制面板都是用它畫的 (見 serve.py)。
    #
    # 下面這串排除有兩個目的,而且兩個都很實在:
    #
    # 1. copyleft 歸零。requests 會拉進 certifi (MPL-2.0,檔案級 copyleft),
    #    這是整包裡最容易被忽略的一個 —— 沒人會想到「CA 憑證清單」是 copyleft。
    # 2. 體積:少了 google-auth / requests / cryptography(內含 OpenSSL),
    #    exe 從 ~30MB 掉到 ~17MB。
    #
    # 為什麼排除掉不會壞:單機版的 config.py 在最後無條件覆寫 AUTH_ENABLED=False、
    # DRIVE_MODE="local"、NOTIFY_DRYRUN=True (任何環境變數都蓋不掉),而後端對
    # google/requests 的 import 全部寫在函式裡 (延遲載入)。那些函式在單機模式
    # 永遠不會被呼叫,模組自然永遠不會被 import。
    excludes=["gunicorn", "unittest", "pydoc", "test",
              "google", "google_auth_httplib2", "requests", "certifi",
              "cryptography", "urllib3", "idna", "charset_normalizer",
              "pyasn1", "pyasn1_modules", "rsa",
              "pystray", "PIL"],
)

pyz = PYZ(a.pure)

# 啟動畫面第一段:解壓縮期間顯示第 0 格 (小人靜止)。不給 text_pos = 沒有
# 文字圖層 = bootloader 沒地方寫它的解壓縮檔名。
splash = Splash(
    os.path.join(HERE, "splash_00.png"),
    binaries=a.binaries,
    datas=a.datas,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,                # 啟動畫面本體 (含 Tcl 腳本)
    splash.binaries,       # onefile 需要:Tcl/Tk 的最小執行期
    a.binaries,
    a.datas,
    name="專案管理系統",
    icon=(os.path.join(HERE, "app.ico") if sys.platform == "win32" else None),
    console=False,         # 不出現黑視窗;停止/錯誤改由系統匣與訊息框負責
    upx=False,             # UPX 壓縮常觸發防毒誤判,不值得省那點體積
    disable_windowed_traceback=False,
)
