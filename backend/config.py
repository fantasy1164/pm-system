# -*- coding: utf-8 -*-
"""執行模式集中設定 — 線上版 (online) / 單機版 (standalone)

三條設計原則:

1. 向下相容第一。
   PM_MODE 未設定或設為 online 時,本檔每一個旗標的取值都與導入本檔之前
   逐字相同 (沿用原本的環境變數與原本的預設值)。Render 上現有的環境變數
   不需要任何調整,線上行為零改變。

2. standalone 的離線性質不可被環境變數破壞。
   單機模式強制關閉所有需要聯網的功能 (Google 登入、Drive 備份、Gmail 寄信)。
   「強制」的意思是:即使有人設了 PM_AUTH_ENABLED=1,單機模式依然不啟用。
   這是刻意的 —— 單機版的賣點就是「絕不對外連線」,不能被一個誤設的
   環境變數悄悄破功。

3. 本檔不 import 專案內其他模組,只讀 os.environ,避免循環 import。
   auth_core / persistence / mailer / app 都可以安全 import 它。

未來新增「本質上需要聯網」的功能時,把它的開關加到本檔,並在
standalone 區塊強制關閉 —— 這是唯一需要記得的維護規則。
"""
import os
import sys


def _flag(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v == "1"


# ------------------------------------------------------------------ 路徑
# PyInstaller 單檔 exe 執行時 (sys.frozen),程式資產被解壓到暫存資料夾
# sys._MEIPASS —— 每次啟動都是新的、結束就刪。因此「程式資產」與
# 「使用者資料」必須分家:
#   程式資產 (backend/schema.sql、frontend/) → _MEIPASS,唯讀
#   使用者資料 (data/、backups/)             → exe 所在資料夾,持久
# 線上版 (Render) 與原始碼執行永遠不會 frozen,走 else 分支,行為零改變。
IS_FROZEN = bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")

if IS_FROZEN:
    REPO_ROOT = sys._MEIPASS                              # 解壓後的資產根目錄
    BASE_DIR = os.path.join(REPO_ROOT, "backend")         # schema.sql 在這
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # .../backend
    REPO_ROOT = os.path.dirname(BASE_DIR)                  # .../pm-system
FRONTEND_DIR = os.path.join(REPO_ROOT, "frontend")

# ------------------------------------------------------------------ 模式
MODE = os.environ.get("PM_MODE", "online").strip().lower()
if MODE not in ("online", "standalone"):
    raise RuntimeError(
        f"PM_MODE 只能是 online 或 standalone,收到:{MODE!r}")
IS_STANDALONE = MODE == "standalone"


def _writable(d):
    """能在 d 建立並刪除檔案才算可寫 —— os.access 在 Windows 上不可信。"""
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".pm-write-test")
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


# 單機版的資料落點:與程式碼分離,更新程式不會動到資料
if IS_FROZEN:
    # 首選:exe 旁邊 (data/、backups/ 跟著 exe 走,使用者看得到、好備份)。
    # exe 若被放進 Program Files 之類不可寫的位置,退到使用者資料夾。
    _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if _writable(_exe_dir):
        STANDALONE_DIR = _exe_dir
    else:
        _appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        STANDALONE_DIR = os.path.join(_appdata, "pm-system")
else:
    STANDALONE_DIR = os.path.join(REPO_ROOT, "standalone")
STANDALONE_DATA_DIR = os.path.join(STANDALONE_DIR, "data")
STANDALONE_BACKUP_DIR = os.path.join(STANDALONE_DIR, "backups")

# ------------------------------------------------------------------ 登入
AUTH_ENABLED = _flag("PM_AUTH_ENABLED")
JWT_SECRET = os.environ.get("PM_JWT_SECRET", "")
OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
ADMIN_EMAIL = os.environ.get("PM_ADMIN_EMAIL", "").strip().lower()
AUTH_TEST_MODE = _flag("PM_AUTH_TEST_MODE")

# ------------------------------------------------------------------ 持久化
SYNC_ENABLED = _flag("PM_SYNC_ENABLED")
DRIVE_MODE = os.environ.get("PM_DRIVE_MODE", "local")
LOCAL_DRIVE_DIR = os.environ.get("PM_LOCAL_DRIVE_DIR", "/tmp/pm-fake-drive")
BOOTSTRAP = _flag("PM_BOOTSTRAP")
BACKUP_DEBOUNCE = float(os.environ.get("PM_BACKUP_DEBOUNCE", "10"))
BACKUP_KEEP = int(os.environ.get("PM_BACKUP_KEEP", "30"))

# 開機還原與寫入備份是兩件事,必須分開控制。
#   線上版:Render 磁碟是暫時的,DB 每次開機都得從快照還原 → 兩者同時開。
#   單機版:DB 就在使用者硬碟上,是唯一真實來源。開機去下載快照覆蓋它,
#           會吃掉使用者上次關機前的編輯 → 還原關閉、備份保留。
# 預設值 = SYNC_ENABLED,線上行為與拆分前完全一致。
RESTORE_ON_BOOT = _flag("PM_RESTORE_ON_BOOT", default=SYNC_ENABLED)

DB_PATH = os.environ.get("PM_DB_PATH", os.path.join(BASE_DIR, "pm.sqlite"))

# ------------------------------------------------------------------ 通知
NOTIFY_DRYRUN = _flag("PM_NOTIFY_DRYRUN", default=True)   # 預設乾跑
NOTIFY_TOKEN = os.environ.get("PM_NOTIFY_TOKEN", "")

# ------------------------------------------------------------------ 其他
CORS_ORIGINS = [o.strip() for o in
                os.environ.get("PM_CORS_ORIGIN", "").split(",") if o.strip()]
DEBUG = _flag("PM_DEBUG")

# 由 Flask 自己服務 frontend/index.html (單一 process,免開兩個終端、免 CORS)
SERVE_FRONTEND = _flag("PM_SERVE_FRONTEND", default=IS_STANDALONE)


# ============================================================ standalone 覆寫
# 放在最後:不論上面各環境變數被設成什麼,單機模式一律以此為準。
if IS_STANDALONE:
    AUTH_ENABLED = False        # 不做 Google 登入,不連 accounts.google.com
    AUTH_TEST_MODE = False
    JWT_SECRET = ""
    OAUTH_CLIENT_ID = ""

    SYNC_ENABLED = True         # 備份保留 —— 但落在本機資料夾
    DRIVE_MODE = "local"        # 絕不走 Google Drive API
    RESTORE_ON_BOOT = False     # 硬碟上的 DB 就是本尊,開機不覆蓋它
    BOOTSTRAP = False           # 不還原就用不到,避免誤刪 DB 的路徑被觸發
    LOCAL_DRIVE_DIR = os.environ.get("PM_LOCAL_DRIVE_DIR",
                                     STANDALONE_BACKUP_DIR)

    NOTIFY_DRYRUN = True        # 只寫通知歷史,絕不連 Gmail API
    NOTIFY_TOKEN = ""

    CORS_ORIGINS = []           # 同源服務,不需要跨來源
    SERVE_FRONTEND = True

    DB_PATH = os.environ.get("PM_DB_PATH",
                             os.path.join(STANDALONE_DATA_DIR, "pm.sqlite"))
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(LOCAL_DRIVE_DIR, exist_ok=True)


# ============================================================ 啟動檢查
if AUTH_ENABLED and not JWT_SECRET:
    raise RuntimeError("PM_AUTH_ENABLED=1 時必須設定 PM_JWT_SECRET")


def summary():
    """啟動時印出的一行摘要,方便確認自己跑在哪個模式。"""
    return (f"PM_MODE={MODE} auth={AUTH_ENABLED} sync={SYNC_ENABLED}"
            f" drive={DRIVE_MODE} restore_on_boot={RESTORE_ON_BOOT}"
            f" notify_dryrun={NOTIFY_DRYRUN} serve_frontend={SERVE_FRONTEND}"
            f" db={DB_PATH}")
