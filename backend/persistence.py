# -*- coding: utf-8 -*-
"""持久化機制 (第三階段) — 開機還原 + 寫入觸發備份 + 關機兜底

設計原則 (方案 B 保命規則):
1. 還原失敗絕不 fallback 成空資料庫 —— 服務進入 FAILED,一律回 503,人工介入
2. 備份一律版本化新檔名,不覆蓋;保留最近 N 份
3. 備份來源是 VACUUM INTO 的一致性快照,不直接複製使用中的 DB 檔
4. SIGTERM (Render spin down / redeploy) 時若有未備份變更,強制 flush 一次

環境變數:
  PM_SYNC_ENABLED   =1 啟用還原/備份 (預設 0,本機開發不啟用)
  PM_DRIVE_MODE     local | google        (預設 local)
  PM_LOCAL_DRIVE_DIR 本機模擬 Drive 的資料夾 (local 模式)
  PM_BOOTSTRAP      =1 允許「完全沒有任何備份」時以空庫初始化 (僅首次部署用)
  PM_BACKUP_DEBOUNCE 寫入後延遲秒數再備份 (預設 10)
  PM_BACKUP_KEEP    保留備份份數 (預設 30)
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN  (google 模式)
  PM_DRIVE_FOLDER_ID 備份存放的 Drive 資料夾 id (google 模式)
"""
import glob
import logging
import os
import shutil
import signal
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime

log = logging.getLogger("persistence")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

SYNC_ENABLED = os.environ.get("PM_SYNC_ENABLED") == "1"
DRIVE_MODE = os.environ.get("PM_DRIVE_MODE", "local")
DEBOUNCE = float(os.environ.get("PM_BACKUP_DEBOUNCE", "10"))
KEEP = int(os.environ.get("PM_BACKUP_KEEP", "30"))
PREFIX = "pmdb_"

# 服務狀態: starting -> ready | failed
STATE = {"phase": "starting", "detail": ""}
REQUIRED_TABLES = {"projects", "users", "budget_allocations",
                   "project_editors", "audit_log"}


# ============================================================ 儲存後端
class LocalFolderBackend:
    """以本機資料夾模擬 Drive,供開發與整合測試;介面與 Google 後端一致。"""

    def __init__(self):
        self.dir = os.environ.get("PM_LOCAL_DRIVE_DIR", "/tmp/pm-fake-drive")
        os.makedirs(self.dir, exist_ok=True)

    def list_backups(self):
        """回傳 [(name, ref)],依名稱新到舊 (檔名含時間戳,字典序即時間序)"""
        paths = glob.glob(os.path.join(self.dir, PREFIX + "*.sqlite"))
        names = sorted((os.path.basename(p) for p in paths), reverse=True)
        return [(n, os.path.join(self.dir, n)) for n in names]

    def download(self, ref, dest):
        shutil.copyfile(ref, dest)

    def upload(self, src, name):
        shutil.copyfile(src, os.path.join(self.dir, name))

    def delete(self, ref):
        os.remove(ref)


class GoogleDriveBackend:
    """真正的 Google Drive (drive.file scope,只能存取本 App 建立的檔案)。"""

    TOKEN_URL = "https://oauth2.googleapis.com/token"
    API = "https://www.googleapis.com/drive/v3"
    UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"

    def __init__(self):
        import requests  # 延遲載入,local 模式不需要
        self.rq = requests
        self.client_id = os.environ["GOOGLE_CLIENT_ID"]
        self.client_secret = os.environ["GOOGLE_CLIENT_SECRET"]
        self.refresh_token = os.environ["GOOGLE_REFRESH_TOKEN"]
        self.folder = os.environ["PM_DRIVE_FOLDER_ID"]
        self._token = None
        self._token_exp = 0

    def _tok(self):
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = self.rq.post(self.TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token",
        }, timeout=30)
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._token_exp = time.time() + int(d.get("expires_in", 3600))
        return self._token

    def _h(self):
        return {"Authorization": "Bearer " + self._tok()}

    def list_backups(self):
        q = (f"'{self.folder}' in parents and trashed=false"
             f" and name contains '{PREFIX}'")
        r = self.rq.get(f"{self.API}/files", headers=self._h(), params={
            "q": q, "fields": "files(id,name)", "pageSize": 100,
            "orderBy": "name desc"}, timeout=30)
        r.raise_for_status()
        return [(f["name"], f["id"]) for f in r.json().get("files", [])]

    def download(self, ref, dest):
        with self.rq.get(f"{self.API}/files/{ref}?alt=media",
                         headers=self._h(), stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)

    def upload(self, src, name):
        import json
        meta = json.dumps({"name": name, "parents": [self.folder]})
        with open(src, "rb") as f:
            files = {
                "metadata": ("metadata", meta, "application/json; charset=UTF-8"),
                "file": (name, f, "application/octet-stream"),
            }
            r = self.rq.post(f"{self.UPLOAD}?uploadType=multipart",
                             headers=self._h(), files=files, timeout=300)
        r.raise_for_status()

    def delete(self, ref):
        r = self.rq.delete(f"{self.API}/files/{ref}",
                           headers=self._h(), timeout=30)
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()


def make_backend():
    return GoogleDriveBackend() if DRIVE_MODE == "google" else LocalFolderBackend()


# ============================================================ 驗證
def verify_db(path):
    """回傳 (ok: bool, detail: str)。空庫/缺表/損毀 都不通過。"""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False, "檔案不存在或為空"
    try:
        db = sqlite3.connect(path)
        try:
            row = db.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                return False, f"integrity_check: {row and row[0]}"
            tables = {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                return False, f"缺少資料表: {sorted(missing)}"
            n = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            return True, f"通過 (projects {n} 筆)"
        finally:
            db.close()
    except sqlite3.DatabaseError as e:
        return False, f"無法開啟: {e}"


# ============================================================ 開機還原
def restore_on_boot(db_path, init_db_fn):
    """依保命規則決定服務能否啟動。回傳 True=ready / False=failed。"""
    if not SYNC_ENABLED:
        init_db_fn()
        STATE.update(phase="ready", detail="同步未啟用 (本機開發模式)")
        log.info("PM_SYNC_ENABLED != 1,跳過還原,直接使用本機 DB")
        return True

    try:
        backend = make_backend()
        backups = backend.list_backups()
    except Exception as e:
        STATE.update(phase="failed", detail=f"無法連線備份儲存端: {e}")
        log.error(STATE["detail"])
        return False

    if not backups:
        if os.environ.get("PM_BOOTSTRAP") == "1":
            for suffix in ("", "-wal", "-shm"):
                p = db_path + suffix
                if os.path.exists(p):
                    os.remove(p)
            init_db_fn()
            STATE.update(phase="ready", detail="首次部署:以空庫初始化 (PM_BOOTSTRAP=1)")
            log.warning(STATE["detail"])
            return True
        STATE.update(phase="failed",
                     detail="儲存端沒有任何備份,且未設 PM_BOOTSTRAP=1;"
                            "為避免誤以空庫上線,拒絕啟動")
        log.error(STATE["detail"])
        return False

    name, ref = backups[0]
    tmp = db_path + ".restore"
    try:
        backend.download(ref, tmp)
    except Exception as e:
        STATE.update(phase="failed", detail=f"下載最新備份 {name} 失敗: {e}")
        log.error(STATE["detail"])
        return False

    ok, detail = verify_db(tmp)
    if not ok:
        STATE.update(phase="failed",
                     detail=f"最新備份 {name} 驗證失敗 ({detail})。"
                            f"不自動回退舊版以免默默遺失近期編輯;"
                            f"請人工確認後移除壞檔再重啟")
        log.error(STATE["detail"])
        os.remove(tmp)
        return False

    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            os.remove(p)
    os.replace(tmp, db_path)
    init_db_fn()  # schema 為冪等 CREATE IF NOT EXISTS,順便套用新增資料表
    STATE.update(phase="ready", detail=f"已還原 {name} ({detail})")
    log.info(STATE["detail"])
    return True


# ============================================================ 備份管理
class BackupManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.backend = make_backend() if SYNC_ENABLED else None
        self._wake = threading.Event()    # 只負責喚醒迴圈
        self._pending = False             # 尚有未備份變更 (備份成功才清除)
        self._stop = threading.Event()
        self._lock = threading.Lock()     # 保護 _pending
        self._thread = None
        self.last_backup = None
        self.last_error = None

    def start(self, register_signals=True):
        """冪等:執行緒活著就不重複啟動。
        重要:gunicorn 會 fork worker,而執行緒不會被 fork 複製——
        因此必須在 worker 內 (gunicorn.conf.py 的 post_fork) 再呼叫一次;
        register_signals=False 供 gunicorn 路徑使用 (兜底改掛 worker 退出鉤子)。"""
        if not SYNC_ENABLED:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="backup-loop")
        self._thread.start()
        if register_signals:
            try:
                signal.signal(signal.SIGTERM, self._on_term)
                signal.signal(signal.SIGINT, self._on_term)
            except ValueError:
                pass  # 非主執行緒無法註冊 signal,改由外部鉤子兜底
        log.info("備份執行緒啟動 (pid %d, debounce %.0fs, 保留 %d 份)",
                 os.getpid(), DEBOUNCE, KEEP)

    def mark_dirty(self):
        if not SYNC_ENABLED:
            return
        with self._lock:
            self._pending = True
        self._wake.set()

    def _loop(self):
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                break
            # debounce: 等編輯告一段落;pending 在此期間持續為真,
            # SIGTERM 兜底隨時可據以 flush,變更不會「困在睡眠裡」遺失
            time.sleep(DEBOUNCE)
            if self._stop.is_set():
                break
            self._backup_if_pending()

    def _backup_if_pending(self):
        with self._lock:
            if not self._pending:
                return
            self._pending = False  # 先清,備份期間的新編輯會重新標記
        try:
            self._do_backup()
        except Exception as e:
            with self._lock:
                self._pending = True   # 失敗還原 pending,下一輪重試
            self._wake.set()
            self.last_error = str(e)
            log.error("備份失敗,將重試: %s", e)

    def _do_backup(self):
        snap = None
        try:
            fd, snap = tempfile.mkstemp(suffix=".sqlite")
            os.close(fd)
            os.remove(snap)  # VACUUM INTO 要求目標不存在
            src = sqlite3.connect(self.db_path)
            try:
                src.execute("VACUUM INTO ?", (snap,))
            finally:
                src.close()
            name = PREFIX + datetime.now().strftime("%Y%m%d_%H%M%S") + ".sqlite"
            self.backend.upload(snap, name)
            self.last_backup = name
            self.last_error = None
            log.info("備份完成: %s", name)
            self._prune()
        finally:
            if snap and os.path.exists(snap):
                os.remove(snap)

    def _prune(self):
        try:
            backups = self.backend.list_backups()
            for name, ref in backups[KEEP:]:
                self.backend.delete(ref)
                log.info("清除舊備份: %s", name)
        except Exception as e:
            log.warning("清除舊備份失敗 (不影響服務): %s", e)

    def flush_if_dirty(self):
        if SYNC_ENABLED:
            self._backup_if_pending()

    def _on_term(self, signum, _frame):
        log.info("收到訊號 %s,執行關機兜底", signum)
        self._stop.set()
        self._wake.set()               # 喚醒 loop 讓它退出
        with self._lock:
            has_pending = self._pending
        if has_pending:
            log.info("關機前 flush 未備份變更…")
        self.flush_if_dirty()
        sys.exit(0)

    def status(self):
        with self._lock:
            pending = self._pending
        return {"last_backup": self.last_backup, "last_error": self.last_error,
                "dirty": pending}
