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
* console=True:黑色視窗就是「關閉系統」的開關,也是錯誤訊息的出口,
  與 start.bat 的使用經驗一致。
* onefile:a.binaries / a.datas 直接進 EXE(),不產生資料夾版。
"""
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

a = Analysis(
    [os.path.join(STANDALONE, "serve.py")],
    pathex=[BACKEND],
    datas=[
        (os.path.join(BACKEND, "schema.sql"), "backend"),
        (FRONTEND, "frontend"),
    ],
    hiddenimports=hidden,
    # gunicorn 依賴 fcntl,Windows 上分析會出警告;tkinter 等純屬多餘體積
    excludes=["gunicorn", "tkinter", "unittest", "pydoc", "test"],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="專案管理系統",
    icon=(os.path.join(HERE, "app.ico") if sys.platform == "win32" else None),
    console=True,          # 黑色視窗 = 停止服務的開關 + 錯誤訊息出口
    upx=False,             # UPX 壓縮常觸發防毒誤判,不值得省那點體積
    disable_windowed_traceback=False,
)
