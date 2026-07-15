# -*- coding: utf-8 -*-
"""模式回歸測試 — 證明 config.py 沒有改變線上行為,且單機版確實離線。

執行:  python test_modes.py        (不需 pytest)

每個情境都在乾淨的子行程中以指定的環境變數 import 模組,再把實際旗標
與「導入 config.py 之前那份程式碼的取值邏輯」逐一比對。

未來每次改動 config.py 都應先跑這支;online 情境有任何一項 FAIL,
就代表線上部署會受影響。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

DUMP = r"""
import json, sys
sys.path.insert(0, %r)
import config, auth_core, persistence
print("@@@" + json.dumps({
    "mode": config.MODE,
    "auth_enabled": auth_core.AUTH_ENABLED,
    "test_mode": auth_core.TEST_MODE,
    "oauth_client_id": auth_core.OAUTH_CLIENT_ID,
    "admin_email": auth_core.ADMIN_EMAIL,
    "jwt_secret_set": bool(auth_core.JWT_SECRET),
    "sync_enabled": persistence.SYNC_ENABLED,
    "restore_on_boot": persistence.RESTORE_ON_BOOT,
    "drive_mode": persistence.DRIVE_MODE,
    "debounce": persistence.DEBOUNCE,
    "keep": persistence.KEEP,
    "bootstrap": config.BOOTSTRAP,
    "notify_dryrun": config.NOTIFY_DRYRUN,
    "notify_token": config.NOTIFY_TOKEN,
    "cors_origins": config.CORS_ORIGINS,
    "serve_frontend": config.SERVE_FRONTEND,
    "debug": config.DEBUG,
}))
""" % HERE


def probe(env):
    """在乾淨環境中載入設定,回傳旗標 dict。"""
    clean = {k: v for k, v in os.environ.items()
             if not k.startswith(("PM_", "GOOGLE_"))}
    clean.update(env)
    r = subprocess.run([sys.executable, "-c", DUMP], env=clean,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"載入失敗:\n{r.stderr}")
    line = [l for l in r.stdout.splitlines() if l.startswith("@@@")][0]
    return json.loads(line[3:])


# ---------------------------------------------------------------- 情境
RENDER_ENV = {          # 對照 render.yaml 的正式環境設定
    "PM_SYNC_ENABLED": "1",
    "PM_DRIVE_MODE": "google",
    "PM_AUTH_ENABLED": "1",
    "PM_CORS_ORIGIN": "https://fantasy1164.github.io",
    "PM_ADMIN_EMAIL": "Admin@Example.com",
    "PM_JWT_SECRET": "s3cret",
    "GOOGLE_OAUTH_CLIENT_ID": "web-client-id",
    "GOOGLE_CLIENT_ID": "desktop-client-id",
    "GOOGLE_CLIENT_SECRET": "desktop-secret",
    "GOOGLE_REFRESH_TOKEN": "refresh-token",
    "PM_DRIVE_FOLDER_ID": "folder-id",
    "PM_NOTIFY_TOKEN": "notify-token",
    "PM_NOTIFY_DRYRUN": "0",
}

LEGACY_DEV_ENV = {}     # 什麼都不設 = 原本的本機開發模式

STANDALONE_ENV = {"PM_MODE": "standalone"}

# 惡意/誤設:單機模式下把聯網功能全部「打開」,結果必須仍是全關
STANDALONE_HOSTILE_ENV = {
    "PM_MODE": "standalone",
    "PM_AUTH_ENABLED": "1",
    "PM_JWT_SECRET": "s3cret",
    "GOOGLE_OAUTH_CLIENT_ID": "web-client-id",
    "PM_DRIVE_MODE": "google",
    "PM_NOTIFY_DRYRUN": "0",
    "PM_NOTIFY_TOKEN": "notify-token",
    "PM_CORS_ORIGIN": "*",
    "PM_RESTORE_ON_BOOT": "1",
    "PM_BOOTSTRAP": "1",
}

fails = []


def check(scenario, got, key, expect, why=""):
    ok = got[key] == expect
    mark = "PASS" if ok else "FAIL"
    tail = f"  ({why})" if why and not ok else ""
    print(f"  [{mark}] {key:16} = {got[key]!r:32} 預期 {expect!r}{tail}")
    if not ok:
        fails.append(f"{scenario}.{key}: 實際 {got[key]!r} != 預期 {expect!r}")


# ================================================ 1. 線上正式環境:行為必須不變
print("\n=== 情境 1:線上正式環境 (render.yaml) — 每一項都必須與重構前相同 ===")
g = probe(RENDER_ENV)
check("online", g, "mode", "online")
check("online", g, "auth_enabled", True, "登入壞掉 = 線上全毀")
check("online", g, "oauth_client_id", "web-client-id")
check("online", g, "admin_email", "admin@example.com", "原本會 .strip().lower()")
check("online", g, "jwt_secret_set", True)
check("online", g, "sync_enabled", True)
check("online", g, "restore_on_boot", True, "拆分後線上仍須還原,否則資料回不來")
check("online", g, "drive_mode", "google")
check("online", g, "bootstrap", False, "首備份後已移除,不該自己變 True")
check("online", g, "notify_dryrun", False, "設 0 就要真的寄信")
check("online", g, "notify_token", "notify-token")
check("online", g, "cors_origins", ["https://fantasy1164.github.io"])
check("online", g, "debounce", 10.0)
check("online", g, "keep", 30)
check("online", g, "serve_frontend", False, "線上前端在 Pages,後端不該服務靜態檔")
check("online", g, "debug", False)

# ================================================ 2. 既有本機開發流程:行為必須不變
print("\n=== 情境 2:本機開發 (無任何環境變數) — 沿用原本的預設 ===")
g = probe(LEGACY_DEV_ENV)
check("dev", g, "mode", "online", "未設 PM_MODE 即維持舊行為")
check("dev", g, "auth_enabled", False)
check("dev", g, "sync_enabled", False)
check("dev", g, "restore_on_boot", False)
check("dev", g, "drive_mode", "local")
check("dev", g, "notify_dryrun", True, "原本預設就是乾跑")
check("dev", g, "cors_origins", [])
check("dev", g, "serve_frontend", False, "既有兩終端流程不受影響")

# ================================================ 3. 單機版:必須完全離線
print("\n=== 情境 3:單機版 (PM_MODE=standalone) ===")
g = probe(STANDALONE_ENV)
check("standalone", g, "mode", "standalone")
check("standalone", g, "auth_enabled", False, "不得連 Google 登入")
check("standalone", g, "sync_enabled", True, "本機備份仍要有")
check("standalone", g, "drive_mode", "local", "絕不走 Drive API")
check("standalone", g, "restore_on_boot", False, "開機還原會蓋掉使用者的 DB")
check("standalone", g, "notify_dryrun", True, "不得連 Gmail API")
check("standalone", g, "cors_origins", [], "同源,不需跨來源")
check("standalone", g, "serve_frontend", True, "單一 process 服務前後端")

# ================================================ 4. 單機版不可被誤設破壞
print("\n=== 情境 4:單機版 + 誤設一堆聯網環境變數 — 必須全部無效 ===")
g = probe(STANDALONE_HOSTILE_ENV)
check("hostile", g, "auth_enabled", False, "PM_AUTH_ENABLED=1 不得生效")
check("hostile", g, "oauth_client_id", "", "client id 必須被清掉")
check("hostile", g, "drive_mode", "local", "PM_DRIVE_MODE=google 不得生效")
check("hostile", g, "restore_on_boot", False, "PM_RESTORE_ON_BOOT=1 不得生效")
check("hostile", g, "bootstrap", False, "PM_BOOTSTRAP=1 不得生效 (會刪 DB)")
check("hostile", g, "notify_dryrun", True, "PM_NOTIFY_DRYRUN=0 不得生效")
check("hostile", g, "notify_token", "", "不需要外部排程 token")
check("hostile", g, "cors_origins", [], "PM_CORS_ORIGIN=* 不得生效")

# ================================================ 5. 防呆
print("\n=== 情境 5:設定防呆 ===")
try:
    probe({"PM_MODE": "typo"})
    fails.append("bad_mode: PM_MODE 打錯字竟然沒擋下來")
    print("  [FAIL] PM_MODE=typo 應該要拒絕啟動")
except AssertionError:
    print("  [PASS] PM_MODE 打錯字 → 拒絕啟動 (不會靜默跑錯模式)")

try:
    probe({"PM_AUTH_ENABLED": "1"})   # 少了 PM_JWT_SECRET
    fails.append("no_secret: 缺 PM_JWT_SECRET 竟然沒擋下來")
    print("  [FAIL] PM_AUTH_ENABLED=1 缺 JWT_SECRET 應該要拒絕啟動")
except AssertionError:
    print("  [PASS] PM_AUTH_ENABLED=1 缺 PM_JWT_SECRET → 拒絕啟動 (原有行為保留)")

# ================================================ 結果
print("\n" + "=" * 60)
if fails:
    print(f"{len(fails)} 項失敗:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("全部通過 — 線上行為未改變,單機版離線性質成立")
