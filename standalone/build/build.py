# -*- coding: utf-8 -*-
"""單機版打包腳本 —— 由 build.bat 呼叫,把系統打成單一 exe。

這支是給「開發者」用的,不是給終端使用者用的:
  開發機需要 Python 3.9+ 與網路 (下載相依套件與 PyInstaller)。
  打包完成後產生的 exe,終端使用者的電腦什麼都不用裝。

與 install.py 相同的慣例:.bat 只放純 ASCII 薄殼,中文訊息都在 .py。

流程:
  1. 在 build/.venv-build 建立獨立虛擬環境 (不污染 backend/.venv)
  2. 安裝 standalone/requirements.txt + pyinstaller
  3. 依 pm.spec 打包 → standalone/dist/專案管理系統.exe
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))       # standalone/build
STANDALONE = os.path.dirname(HERE)
VENV = os.path.join(HERE, ".venv-build")
WORK = os.path.join(HERE, ".work")
DIST = os.path.join(STANDALONE, "dist")
REQ = os.path.join(STANDALONE, "requirements.txt")
SPEC = os.path.join(HERE, "pm.spec")

MIN_PY = (3, 9)


def venv_python():
    if os.name == "nt":
        return os.path.join(VENV, "Scripts", "python.exe")
    return os.path.join(VENV, "bin", "python")


def line(msg=""):
    print(msg, flush=True)


def run(argv, step):
    line(f"  {step} ...")
    r = subprocess.run(argv)
    if r.returncode != 0:
        line()
        line("=" * 58)
        line(f"  打包失敗:{step}")
        line("=" * 58)
        sys.exit(1)


def main():
    line()
    line("=" * 58)
    line("  專案管理系統 — 單機版打包 (單一 exe)")
    line("=" * 58)
    line()

    if sys.version_info < MIN_PY:
        line(f"  需要 Python {MIN_PY[0]}.{MIN_PY[1]} 以上,"
             f"目前是 {sys.version.split()[0]}")
        sys.exit(1)

    if not os.path.exists(venv_python()):
        run([sys.executable, "-m", "venv", VENV], "建立打包用虛擬環境")

    py = venv_python()
    run([py, "-m", "pip", "install", "--quiet", "-r", REQ, "pyinstaller"],
        "安裝相依套件與 PyInstaller")

    run([py, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", DIST, "--workpath", WORK, SPEC],
        "打包 exe (第一次約需數分鐘)")

    exe = os.path.join(
        DIST, "專案管理系統.exe" if os.name == "nt" else "專案管理系統")
    size_mb = os.path.getsize(exe) / 1048576 if os.path.exists(exe) else 0

    line()
    line("=" * 58)
    line("  打包完成")
    line("=" * 58)
    line(f"  輸出    {exe}")
    line(f"  大小    {size_mb:.0f} MB")
    line()
    line("  把這個檔案交給使用者即可 —— 對方的電腦不需要安裝 Python。")
    line("  使用者第一次執行時,Windows SmartScreen 可能出現藍色警告,")
    line("  點「其他資訊」→「仍要執行」即可 (未簽章 exe 的正常現象)。")
    line("=" * 58)
    line()


if __name__ == "__main__":
    main()
