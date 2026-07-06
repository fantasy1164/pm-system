# -*- coding: utf-8 -*-
"""專案期程預算管理系統 — 後端 API (第一階段: 資料模型 + CRUD)

本階段尚未啟用登入,所有寫入操作以 request header `X-User` 記入 audit log,
未帶則記為 local-dev。第四階段會以 JWT 取代。
"""
import json
import os
import sqlite3
from datetime import date, datetime

from flask import Flask, g, jsonify, request

import auth_core
import persistence

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PM_DB_PATH", os.path.join(BASE_DIR, "pm.sqlite"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

BACKUP = persistence.BackupManager(DB_PATH)
_started = False


def startup():
    """開機流程:還原 (或本機直接初始化) -> 啟動備份執行緒。
    由 __main__ 或 wsgi.py 呼叫;seed 等腳本單純 import 不會觸發。"""
    global _started
    if _started:
        return
    _started = True
    persistence.restore_on_boot(DB_PATH, init_db)
    BACKUP.start()

# 可寫入 projects 的欄位白名單 (防止任意欄位注入)
PROJECT_FIELDS = [
    "year", "status", "contract_no", "part_no", "so_number", "name",
    "start_date", "end_date", "participants", "awarded_amount",
    "kickoff_date", "notes", "sort_order",
]


# ---------------------------------------------------------------- DB helpers
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# 權限裝飾器 (PM_AUTH_ENABLED != 1 時全部為 no-op,維持開發模式行為)
VIEW = auth_core.require_auth(get_db)          # 任何 active 使用者
EDIT_GLOBAL = auth_core.require_edit(get_db)   # 需全域編輯權 (新增專案/新年度)
EDIT_PID = auth_core.require_edit(get_db, "pid")  # 逐案編輯權
ADMIN = auth_core.require_admin(get_db)        # 僅管理者


def init_db():
    db = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        db.executescript(f.read())
    db.commit()
    db.close()


def actor():
    return auth_core.current_actor()


def write_audit(db, action, entity, entity_id, changes):
    db.execute(
        "INSERT INTO audit_log (actor, action, entity, entity_id, changes)"
        " VALUES (?, ?, ?, ?, ?)",
        (actor(), action, entity, entity_id,
         json.dumps(changes, ensure_ascii=False)),
    )


# ------------------------------------------------------------- serialization
def parse_iso(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def project_to_dict(row, budgets, milestones=()):
    d = dict(row)
    d.pop("deleted", None)
    start, end = parse_iso(d.get("start_date")), parse_iso(d.get("end_date"))
    d["duration_days"] = (end - start).days + 1 if start and end else None
    d["budgets"] = [
        {"year": b["year"], "amount": b["amount"]} for b in budgets
    ]
    d["milestones"] = [
        {"date": m["date"], "name": m["name"]} for m in milestones
    ]
    return d


def validate_project(data, partial=False):
    """回傳 (清洗後欄位 dict, 錯誤訊息或 None)"""
    out = {}
    for f in PROJECT_FIELDS:
        if f in data:
            out[f] = data[f]
    if not partial:
        if not data.get("name"):
            return None, "案名 (name) 為必填"
        if not isinstance(data.get("year"), int):
            return None, "年度 (year) 為必填整數,例 115"
    for f in ("start_date", "end_date", "kickoff_date"):
        if out.get(f) and parse_iso(out[f]) is None:
            return None, f"{f} 日期格式須為 YYYY-MM-DD"
    s, e = out.get("start_date"), out.get("end_date")
    if s and e and s > e:
        return None, "履約迄日不可早於起日"
    if "status" in out and out["status"] not in ("ongoing", "not_awarded", "closed"):
        return None, "status 僅接受 ongoing / not_awarded / closed"
    return out, None


def validate_milestones(ms):
    """回傳 (清洗後 list, 錯誤或 None)"""
    out = []
    for m in ms or []:
        d, n = (m.get("date") or "").strip(), (m.get("name") or "").strip()
        if not d or parse_iso(d) is None:
            return None, "里程碑日期格式須為 YYYY-MM-DD"
        if not n:
            return None, "里程碑名稱為必填"
        out.append({"date": d, "name": n})
    out.sort(key=lambda m: m["date"])
    return out, None


def upsert_milestones(db, project_id, ms):
    db.execute("DELETE FROM milestones WHERE project_id = ?", (project_id,))
    for m in ms:
        db.execute("INSERT INTO milestones (project_id, date, name)"
                   " VALUES (?, ?, ?)", (project_id, m["date"], m["name"]))


def upsert_budgets(db, project_id, budgets):
    db.execute("DELETE FROM budget_allocations WHERE project_id = ?", (project_id,))
    for b in budgets or []:
        db.execute(
            "INSERT INTO budget_allocations (project_id, year, amount)"
            " VALUES (?, ?, ?)",
            (project_id, int(b["year"]), int(b.get("amount") or 0)),
        )


def fetch_project(db, pid):
    row = db.execute(
        "SELECT * FROM projects WHERE id = ? AND deleted = 0", (pid,)
    ).fetchone()
    if row is None:
        return None
    budgets = db.execute(
        "SELECT year, amount FROM budget_allocations"
        " WHERE project_id = ? ORDER BY year", (pid,)
    ).fetchall()
    ms = db.execute(
        "SELECT date, name FROM milestones"
        " WHERE project_id = ? ORDER BY date", (pid,)
    ).fetchall()
    return project_to_dict(row, budgets, ms)


# -------------------------------------------------------------------- CORS
@app.after_request
def add_cors(resp):
    # PM_CORS_ORIGIN 支援逗號分隔多來源,例:
    #   https://xxx.github.io,http://localhost:8000
    # 回應時回傳「與請求相符的那一個」;CORS 只是瀏覽器端防線,
    # 真正的存取控制在 JWT,允許 localhost 不影響安全性
    allowed = [o.strip() for o in
               os.environ.get("PM_CORS_ORIGIN", "*").split(",") if o.strip()]
    origin = request.headers.get("Origin", "")
    if "*" in allowed:
        resp.headers["Access-Control-Allow-Origin"] = "*"
    elif origin in allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-User, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Expose-Headers"] = "X-New-Token"
    new_tok = getattr(g, "new_token", None)
    if new_tok:
        resp.headers["X-New-Token"] = new_tok
    return resp


@app.route("/api/<path:_p>", methods=["OPTIONS"])
def cors_preflight(_p):
    return "", 204


@app.before_request
def readiness_gate():
    """還原完成前 (或還原失敗時) 除 health 外一律 503,絕不讓空庫接請求。"""
    if request.method == "OPTIONS" or request.path == "/api/health":
        return None
    if persistence.STATE["phase"] != "ready":
        return jsonify({"error": "服務尚未就緒",
                        "phase": persistence.STATE["phase"],
                        "detail": persistence.STATE["detail"]}), 503


# ------------------------------------------------------------------- routes
@app.get("/api/health")
def health():
    ready = persistence.STATE["phase"] == "ready"
    body = {"status": "ok" if ready else persistence.STATE["phase"],
            "detail": persistence.STATE["detail"],
            "backup": BACKUP.status(),
            "time": datetime.now().isoformat(timespec="seconds")}
    return jsonify(body), (200 if ready else 503)


@app.get("/api/years")
@VIEW
def list_years():
    rows = get_db().execute(
        "SELECT DISTINCT year FROM projects WHERE deleted = 0 ORDER BY year DESC"
    ).fetchall()
    return jsonify([r["year"] for r in rows])


@app.get("/api/projects")
@VIEW
def list_projects():
    year = request.args.get("year", type=int)
    db = get_db()
    sql = "SELECT * FROM projects WHERE deleted = 0"
    args = []
    if year:
        sql += " AND year = ?"
        args.append(year)
    sql += " ORDER BY sort_order, id"
    rows = db.execute(sql, args).fetchall()
    ids = [r["id"] for r in rows]
    budget_map, ms_map = {}, {}
    if ids:
        q = ",".join("?" * len(ids))
        for b in db.execute(
            f"SELECT project_id, year, amount FROM budget_allocations"
            f" WHERE project_id IN ({q}) ORDER BY year", ids
        ):
            budget_map.setdefault(b["project_id"], []).append(b)
        for m in db.execute(
            f"SELECT project_id, date, name FROM milestones"
            f" WHERE project_id IN ({q}) ORDER BY date", ids
        ):
            ms_map.setdefault(m["project_id"], []).append(m)
    return jsonify([project_to_dict(r, budget_map.get(r["id"], []),
                                    ms_map.get(r["id"], [])) for r in rows])


@app.get("/api/projects/<int:pid>")
@VIEW
def get_project(pid):
    p = fetch_project(get_db(), pid)
    if p is None:
        return jsonify({"error": "找不到專案"}), 404
    return jsonify(p)


@app.post("/api/projects")
@EDIT_GLOBAL
def create_project():
    data = request.get_json(silent=True) or {}
    fields, err = validate_project(data)
    if err:
        return jsonify({"error": err}), 400
    db = get_db()
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    cur = db.execute(
        f"INSERT INTO projects ({cols}) VALUES ({marks})", list(fields.values())
    )
    pid = cur.lastrowid
    upsert_budgets(db, pid, data.get("budgets"))
    ms, err = validate_milestones(data.get("milestones"))
    if err:
        return jsonify({"error": err}), 400
    upsert_milestones(db, pid, ms)
    write_audit(db, "create", "projects", pid, {"new": fields})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify(fetch_project(db, pid)), 201


@app.put("/api/projects/<int:pid>")
@EDIT_PID
def update_project(pid):
    db = get_db()
    old = db.execute(
        "SELECT * FROM projects WHERE id = ? AND deleted = 0", (pid,)
    ).fetchone()
    if old is None:
        return jsonify({"error": "找不到專案"}), 404
    data = request.get_json(silent=True) or {}
    fields, err = validate_project(data, partial=True)
    if err:
        return jsonify({"error": err}), 400
    # 日期順序須以「合併後」的值檢查,否則只改單邊日期會繞過驗證
    merged_s = fields.get("start_date", old["start_date"])
    merged_e = fields.get("end_date", old["end_date"])
    if merged_s and merged_e and merged_s > merged_e:
        return jsonify({"error": "履約迄日不可早於起日"}), 400

    changes = {
        f: [old[f], v] for f, v in fields.items() if old[f] != v
    }
    if fields:
        sets = ", ".join(f"{f} = ?" for f in fields)
        db.execute(
            f"UPDATE projects SET {sets},"
            " updated_at = datetime('now','localtime') WHERE id = ?",
            list(fields.values()) + [pid],
        )
    if "budgets" in data:
        upsert_budgets(db, pid, data["budgets"])
        changes["budgets"] = ["(replaced)", data["budgets"]]
    if "milestones" in data:
        ms, err = validate_milestones(data["milestones"])
        if err:
            return jsonify({"error": err}), 400
        upsert_milestones(db, pid, ms)
        changes["milestones"] = ["(replaced)", ms]
    if changes:
        write_audit(db, "update", "projects", pid, changes)
    db.commit()
    BACKUP.mark_dirty()
    return jsonify(fetch_project(db, pid))


@app.delete("/api/projects/<int:pid>")
@EDIT_PID
def delete_project(pid):
    db = get_db()
    row = db.execute(
        "SELECT id, name FROM projects WHERE id = ? AND deleted = 0", (pid,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "找不到專案"}), 404
    db.execute(
        "UPDATE projects SET deleted = 1,"
        " updated_at = datetime('now','localtime') WHERE id = ?", (pid,)
    )
    write_audit(db, "delete", "projects", pid, {"name": row["name"]})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"deleted": pid})


@app.post("/api/years/<int:new_year>/init")
@EDIT_GLOBAL
def init_year(new_year):
    """建立新年度:把履約期跨入新年度的進行中專案「複製」一份到新年度。

    - 每個年度是獨立快照,新年度的修改不影響上一年度的定稿
    - copied_from 記錄來源;同一來源已複製過則跳過 (端點可安全重打)
    - budgets 全數複製,便於新年度獨立調整預估認列
    """
    db = get_db()
    boundary = f"{1911 + new_year}-01-01"  # 民國轉西元
    rows = db.execute(
        "SELECT * FROM projects p WHERE p.deleted = 0 AND p.status = 'ongoing'"
        " AND p.year = ? AND p.end_date >= ?"
        " AND NOT EXISTS (SELECT 1 FROM projects c WHERE c.copied_from = p.id"
        "                 AND c.year = ? AND c.deleted = 0)",
        (new_year - 1, boundary, new_year),
    ).fetchall()
    copied = []
    for r in rows:
        cur = db.execute(
            "INSERT INTO projects (year, status, contract_no, part_no,"
            " so_number, name, start_date, end_date, participants,"
            " awarded_amount, kickoff_date, notes, sort_order, copied_from)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_year, r["status"], r["contract_no"], r["part_no"],
             r["so_number"], r["name"], r["start_date"], r["end_date"],
             r["participants"], r["awarded_amount"], r["kickoff_date"],
             r["notes"], r["sort_order"], r["id"]),
        )
        new_id = cur.lastrowid
        db.execute(
            "INSERT INTO budget_allocations (project_id, year, amount)"
            " SELECT ?, year, amount FROM budget_allocations"
            " WHERE project_id = ?", (new_id, r["id"]),
        )
        write_audit(db, "create", "projects", new_id,
                    {"reason": "年度複製", "copied_from": r["id"],
                     "year": [r["year"], new_year]})
        copied.append({"id": new_id, "copied_from": r["id"], "name": r["name"]})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"year": new_year, "copied": copied})


# ------------------------------------------------------------- auth
@app.get("/api/auth/config")
def auth_config():
    """前端據此決定要不要顯示登入畫面 (公開端點,不含機密)。"""
    return jsonify({"auth_enabled": auth_core.AUTH_ENABLED,
                    "google_client_id": auth_core.OAUTH_CLIENT_ID})


def _me_payload(db, user):
    if user["role"] == "admin" or user["can_edit"]:
        editable = "all"
    else:
        editable = [r["project_id"] for r in db.execute(
            "SELECT project_id FROM project_editors WHERE user_id = ?",
            (user["id"],))]
    return {"user": {k: user[k] for k in
                     ("id", "email", "name", "role", "status", "can_edit")},
            "editable": editable}


@app.post("/api/auth/google")
def auth_google():
    if not auth_core.AUTH_ENABLED:
        return jsonify({"error": "登入功能未啟用"}), 400
    credential = (request.get_json(silent=True) or {}).get("credential", "")
    try:
        info = auth_core.verify_google_token(credential)
    except Exception:
        return jsonify({"error": "Google 憑證驗證失敗,請重試"}), 401
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE email = ?",
                     (info["email"],)).fetchone()
    if row is None:
        is_admin = (info["email"] == auth_core.ADMIN_EMAIL)
        cur = db.execute(
            "INSERT INTO users (email, name, role, status, can_edit)"
            " VALUES (?, ?, ?, ?, ?)",
            (info["email"], info["name"],
             "admin" if is_admin else "dev",
             "active" if is_admin else "pending",
             1 if is_admin else 0))
        write_audit(db, "create", "users", cur.lastrowid,
                    {"email": info["email"],
                     "reason": "首位管理者" if is_admin else "首次登入待核准"})
        db.commit()
        BACKUP.mark_dirty()
        if not is_admin:
            return jsonify({"code": "pending",
                            "error": "帳號已建立,請等待管理者核准"}), 403
        row = db.execute("SELECT * FROM users WHERE id = ?",
                         (cur.lastrowid,)).fetchone()
    # PM_ADMIN_EMAIL 為持續有效的權威設定:符合者確保為 active admin
    # (涵蓋「先以一般身分建號、之後才設定/修正 ADMIN_EMAIL」的情況)
    if (info["email"] == auth_core.ADMIN_EMAIL and
            (row["role"] != "admin" or row["status"] != "active")):
        db.execute("UPDATE users SET role='admin', status='active',"
                   " can_edit=1, updated_at=datetime('now','localtime')"
                   " WHERE id = ?", (row["id"],))
        write_audit(db, "update", "users", row["id"],
                    {"reason": "PM_ADMIN_EMAIL 自我修復晉升",
                     "role": [row["role"], "admin"],
                     "status": [row["status"], "active"]})
        db.commit()
        BACKUP.mark_dirty()
        row = db.execute("SELECT * FROM users WHERE id = ?",
                         (row["id"],)).fetchone()
    if row["status"] == "pending":
        return jsonify({"code": "pending", "error": "尚待管理者核准"}), 403
    if row["status"] == "disabled":
        return jsonify({"code": "disabled", "error": "帳號已停用"}), 403
    if row["name"] != info["name"] and info["name"]:
        db.execute("UPDATE users SET name = ? WHERE id = ?",
                   (info["name"], row["id"]))
        db.commit()
        BACKUP.mark_dirty()
    user = dict(db.execute("SELECT * FROM users WHERE id = ?",
                           (row["id"],)).fetchone())
    return jsonify({"token": auth_core.issue_jwt(user["id"]),
                    **_me_payload(db, user)})


@app.get("/api/auth/me")
@VIEW
def auth_me():
    if not auth_core.AUTH_ENABLED:
        return jsonify({"user": None, "editable": "all"})
    return jsonify(_me_payload(get_db(), g.user))


# ------------------------------------------------------- users (admin)
USER_EDITABLE = ("role", "status", "can_edit")


@app.get("/api/users")
@ADMIN
def list_users():
    rows = get_db().execute(
        "SELECT id, email, name, role, status, can_edit, created_at"
        " FROM users ORDER BY status = 'pending' DESC, id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.put("/api/users/<int:uid>")
@ADMIN
def update_user(uid):
    db = get_db()
    old = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if old is None:
        return jsonify({"error": "找不到使用者"}), 404
    me = getattr(g, "user", None)
    if me and me["id"] == uid:
        return jsonify({"error": "不可修改自己的權限 (避免誤鎖)"}), 400
    data = request.get_json(silent=True) or {}
    fields = {k: data[k] for k in USER_EDITABLE if k in data}
    if "role" in fields and fields["role"] not in (
            "admin", "pm", "dept_head", "sales", "dev"):
        return jsonify({"error": "無效的角色"}), 400
    if "status" in fields and fields["status"] not in (
            "pending", "active", "disabled"):
        return jsonify({"error": "無效的狀態"}), 400
    changes = {k: [old[k], v] for k, v in fields.items() if old[k] != v}
    if changes:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE users SET {sets},"
                   " updated_at = datetime('now','localtime') WHERE id = ?",
                   list(fields.values()) + [uid])
        write_audit(db, "update", "users", uid, changes)
        db.commit()
        BACKUP.mark_dirty()
    row = db.execute("SELECT id, email, name, role, status, can_edit"
                     " FROM users WHERE id = ?", (uid,)).fetchone()
    return jsonify(dict(row))


@app.delete("/api/users/<int:uid>")
@ADMIN
def delete_user(uid):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        return jsonify({"error": "找不到使用者"}), 404
    me = getattr(g, "user", None)
    if me and me["id"] == uid:
        return jsonify({"error": "不可刪除自己"}), 400
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    write_audit(db, "delete", "users", uid, {"email": row["email"]})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"deleted": uid})


# ------------------------------------------- 逐案編輯授權 (admin)
@app.get("/api/projects/<int:pid>/editors")
@ADMIN
def get_editors(pid):
    rows = get_db().execute(
        "SELECT user_id FROM project_editors WHERE project_id = ?",
        (pid,)).fetchall()
    return jsonify([r["user_id"] for r in rows])


@app.put("/api/projects/<int:pid>/editors")
@ADMIN
def set_editors(pid):
    db = get_db()
    if db.execute("SELECT 1 FROM projects WHERE id = ? AND deleted = 0",
                  (pid,)).fetchone() is None:
        return jsonify({"error": "找不到專案"}), 404
    ids = (request.get_json(silent=True) or {}).get("user_ids", [])
    db.execute("DELETE FROM project_editors WHERE project_id = ?", (pid,))
    for u in ids:
        db.execute("INSERT OR IGNORE INTO project_editors"
                   " (project_id, user_id) VALUES (?, ?)", (pid, int(u)))
    write_audit(db, "update", "project_editors", pid, {"user_ids": ids})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"project_id": pid, "user_ids": ids})


@app.get("/api/audit")
@VIEW
def list_audit():
    limit = min(request.args.get("limit", 50, type=int), 500)
    rows = get_db().execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["changes"] = json.loads(d["changes"])
        out.append(d)
    return jsonify(out)


if __name__ == "__main__":
    startup()
    app.run(host="127.0.0.1", port=5000, debug=True)
