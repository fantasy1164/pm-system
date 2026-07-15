# -*- coding: utf-8 -*-
"""單機版安裝腳本 —— 由 install.bat 以「系統 Python」呼叫。

為什麼安裝邏輯放在 .py 而不是直接寫在 .bat:
  Windows 是以系統 ANSI codepage (繁中為 cp950) 解析 .bat 檔的,
  中文訊息寫進 .bat 存成 UTF-8 會變亂碼,存成 cp950 又在其他語系的
  Windows 上爆掉。把訊息放進 Python 就沒有這個問題。
  因此 .bat 只保留純 ASCII 的薄殼,實際工作都在這裡。

只用標準函式庫 —— 這支是在虛擬環境「還沒建立」的時候跑的。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
VENV = os.path.join(REPO, "backend", ".venv")
REQ = os.path.join(HERE, "requirements.txt")

MIN_PY = (3, 9)


def venv_python():
    """虛擬環境裡的 python 路徑 (Windows 在 Scripts,其他在 bin)。"""
    if os.name == "nt":
        return os.path.join(VENV, "Scripts", "python.exe")
    return os.path.join(VENV, "bin", "python")


def line(msg=""):
    print(msg, flush=True)


def fail(msg, hint=""):
    line()
    line("=" * 58)
    line(f"  安裝失敗:{msg}")
    if hint:
        line()
        for h in hint.splitlines():
            line(f"  {h}")
    line("=" * 58)
    line()
    return 1


def main():
    line()
    line("=" * 58)
    line("  專案管理系統 — 單機版安裝")
    line("=" * 58)
    line()

    # ---------------------------------------------------------- 1. Python
    if sys.version_info < MIN_PY:
        return fail(
            f"Python 版本過舊 (目前 {sys.version.split()[0]})",
            f"需要 Python {MIN_PY[0]}.{MIN_PY[1]} 以上,建議 3.12。\n"
            "下載:https://www.python.org/downloads/\n"
            "安裝時請務必勾選「Add Python to PATH」。")
    line(f"[1/3] Python {sys.version.split()[0]} — OK")

    # ---------------------------------------------------------- 2. 虛擬環境
    if os.path.exists(venv_python()):
        line("[2/3] 虛擬環境已存在,沿用")
    else:
        line("[2/3] 建立虛擬環境...")
        r = subprocess.run([sys.executable, "-m", "venv", VENV])
        if r.returncode != 0 or not os.path.exists(venv_python()):
            return fail("建立虛擬環境失敗",
                        "若使用 Microsoft Store 版的 Python,建議改裝\n"
                        "python.org 的正式版本後重試。")

    # ---------------------------------------------------------- 3. 套件
    line("[3/3] 安裝相依套件 (這一步需要網路,只有安裝時需要)...")
    py = venv_python()
    subprocess.run([py, "-m", "pip", "install", "--upgrade", "pip", "-q"])
    r = subprocess.run([py, "-m", "pip", "install", "-r", REQ])
    if r.returncode != 0:
        return fail("套件安裝失敗",
                    "多半是網路問題,請確認連線後重新執行 install.bat。\n"
                    "若公司網路有 Proxy,可能需要設定 pip 的 proxy 參數。")

    # ---------------------------------------------------------- 驗證
    check = subprocess.run(
        [py, "-c", "import flask, waitress, jwt; print('ok')"],
        capture_output=True, text=True)
    if "ok" not in check.stdout:
        return fail("套件驗證失敗", check.stderr.strip()[:400])

    line()
    line("=" * 58)
    line("  安裝完成")
    line()
    line("  接下來執行 start.bat 即可啟動系統。")
    line("  啟動後就完全離線運作,不需要網路。")
    line("=" * 58)
    line()
    return 0


if __name__ == "__main__":
    sys.exit(main())
