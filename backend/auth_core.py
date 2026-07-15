# -*- coding: utf-8 -*-
"""授權機制 (第四階段) — Google OAuth 登入 + JWT + 角色權限

流程:
  前端 Google Sign-In 取得 ID token → POST /api/auth/google →
  後端驗簽 + 比對 audience → 查/建 users → active 者簽發本系統 JWT (30 分鐘) →
  之後每個請求帶 Authorization: Bearer <jwt>,每次通過驗證即滑動續期
  (剩餘 < 25 分鐘時於回應 X-New-Token 換發新 token,前端自動替換)。

權限模型:
  - 角色 admin / pm / dept_head / sales / dev;status pending / active / disabled
  - 檢視:所有 active 使用者
  - 編輯:admin,或該專案所屬團隊的成員 (欄位層級由權限矩陣控管)
  - 使用者管理:僅 admin
  - JWT 只放 uid,角色與授權每次請求都從 DB 讀 → 管理者改權限即刻生效

設定來源:見 config.py (PM_AUTH_ENABLED / PM_JWT_SECRET /
GOOGLE_OAUTH_CLIENT_ID / PM_ADMIN_EMAIL / PM_AUTH_TEST_MODE)。
單機模式 (PM_MODE=standalone) 一律 AUTH_ENABLED=False —— 不連 Google。
"""
import functools
import time

import jwt as pyjwt
from flask import g, jsonify, request

import config

AUTH_ENABLED = config.AUTH_ENABLED
JWT_SECRET = config.JWT_SECRET
OAUTH_CLIENT_ID = config.OAUTH_CLIENT_ID
ADMIN_EMAIL = config.ADMIN_EMAIL
TEST_MODE = config.AUTH_TEST_MODE
TOKEN_MINUTES = 30          # JWT 有效期 = idle 上限
RENEW_BELOW = 25 * 60       # 剩餘秒數低於此值即換發 (滑動續期)


# ------------------------------------------------------------ Google 驗證
def verify_google_token(credential):
    """驗證 Google ID token,回傳 {email, name};失敗 raise ValueError。"""
    if TEST_MODE and credential.startswith("test:"):
        _, email, name = credential.split(":", 2)
        return {"email": email.lower(), "name": name}
    from google.auth.transport import requests as garequests
    from google.oauth2 import id_token as gid
    info = gid.verify_oauth2_token(credential, garequests.Request(),
                                   OAUTH_CLIENT_ID)
    if not info.get("email_verified"):
        raise ValueError("email 未驗證")
    return {"email": info["email"].lower(), "name": info.get("name", "")}


# ------------------------------------------------------------ JWT
def issue_jwt(uid):
    now = int(time.time())
    return pyjwt.encode({"uid": uid, "iat": now,
                         "exp": now + TOKEN_MINUTES * 60},
                        JWT_SECRET, algorithm="HS256")


def decode_jwt(token):
    """回傳 payload dict;無效/過期 raise pyjwt 例外。"""
    return pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])


def issue_register_token(email, name):
    """签发短期注册凭证 (15 分钟);内含经 Google 验证过的 email/name。"""
    now = int(time.time())
    return pyjwt.encode({"reg": True, "email": email, "name": name,
                         "iat": now, "exp": now + 15 * 60},
                        JWT_SECRET, algorithm="HS256")


def verify_register_token(token):
    """验证注册凭证,回传 (email, name);无效/过期 raise pyjwt 例外。"""
    payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    if not payload.get("reg"):
        raise pyjwt.InvalidTokenError("非註冊憑證")
    return payload["email"], payload.get("name", "")


# ------------------------------------------------------------ 請求層
def load_current_user(get_db):
    """驗 Bearer token 並載入使用者。成功回 None 並設定 g.user;失敗回 (resp, code)。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "未登入", "code": "no_token"}), 401
    try:
        payload = decode_jwt(auth[7:])
    except pyjwt.ExpiredSignatureError:
        return jsonify({"error": "連線逾時,請重新登入", "code": "expired"}), 401
    except pyjwt.InvalidTokenError:
        return jsonify({"error": "無效的憑證", "code": "invalid"}), 401
    row = get_db().execute("SELECT * FROM users WHERE id = ?",
                           (payload["uid"],)).fetchone()
    if row is None or row["status"] != "active":
        return jsonify({"error": "帳號不存在或已停用", "code": "inactive"}), 401
    g.user = dict(row)
    # 滑動續期:剩餘不足 RENEW_BELOW 就準備換發
    if payload["exp"] - time.time() < RENEW_BELOW:
        g.new_token = issue_jwt(row["id"])
    return None


def require_auth(get_db):
    """裝飾器工廠:檢視權限 (任何 active 使用者)。AUTH 未啟用時直接放行。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if not AUTH_ENABLED:
                return fn(*a, **kw)
            err = load_current_user(get_db)
            if err:
                return err
            return fn(*a, **kw)
        return wrapper
    return deco


def can_edit_project(db, user, project_id):
    """編輯權 = 管理者,或屬於該專案團隊的成員 (欄位層級另由矩陣控管)。
    project_id None (新增) → 有任一團隊歸屬即可,團隊限制由 handler 檢查。"""
    if user["role"] == "admin":
        return True
    if project_id is None:
        return db.execute("SELECT 1 FROM team_members WHERE user_id = ?",
                          (user["id"],)).fetchone() is not None
    row = db.execute(
        "SELECT p.team_id FROM projects p WHERE p.id = ?",
        (project_id,)).fetchone()
    if row is None:
        return False
    if row["team_id"] is not None and db.execute(
        "SELECT 1 FROM team_members WHERE team_id = ? AND user_id = ?",
        (row["team_id"], user["id"])).fetchone():
        return True   # 主包團隊成員
    # 分包:屬於此案 active 分包團隊的成員也可編輯 (僅自己的獨立欄位,
    # 共享欄位由 update_project 的硬規則擋成唯讀)
    return db.execute(
        "SELECT 1 FROM team_members tm"
        " JOIN project_subcontracts sc ON sc.team_id = tm.team_id"
        " WHERE tm.user_id = ? AND sc.project_id = ? AND sc.active = 1",
        (user["id"], project_id)).fetchone() is not None


def require_edit(get_db, project_id_arg=None):
    """裝飾器工廠:編輯權限。project_id_arg 指定路徑參數名 (如 'pid') 則做逐案檢查;
    None 表示「需要全域編輯權」(新增專案、建新年度)。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if not AUTH_ENABLED:
                return fn(*a, **kw)
            err = load_current_user(get_db)
            if err:
                return err
            pid = kw.get(project_id_arg) if project_id_arg else None
            if not can_edit_project(get_db(), g.user, pid):
                return jsonify({"error": "沒有編輯權限,請向管理者申請",
                                "code": "forbidden"}), 403
            return fn(*a, **kw)
        return wrapper
    return deco


def require_admin(get_db):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if not AUTH_ENABLED:
                return fn(*a, **kw)
            err = load_current_user(get_db)
            if err:
                return err
            if g.user["role"] != "admin":
                return jsonify({"error": "僅管理者可執行此操作",
                                "code": "forbidden"}), 403
            return fn(*a, **kw)
        return wrapper
    return deco


def current_actor():
    """audit log 用:登入模式取 g.user,否則沿用 X-User (開發模式)。"""
    u = getattr(g, "user", None)
    if u:
        return u["email"]
    from urllib.parse import unquote
    return unquote(request.headers.get("X-User", "local-dev"))
