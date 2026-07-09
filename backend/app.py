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
    "kickoff_date", "warranty_years", "team_id", "contract_scan",
    "nda_date", "nda_scan", "notify_days_before", "notes", "sort_order",
]

# 欄位權限矩陣定義:矩陣欄位鍵 -> 實際資料欄位
PERM_ROLES = ("pm", "dept_head", "sales", "dev")   # admin 永遠全開
FIELD_MAP = {
    "status":         ["status"],
    "nda":            ["nda_date", "nda_scan"],
    "contract":       ["contract_scan"],
    "name":           ["name"],
    "contract_no":    ["contract_no"],
    "part_no":        ["part_no"],
    "so_number":      ["so_number"],
    "schedule":       ["start_date", "end_date", "duration_days",
                       "kickoff_date", "milestones", "notify_days_before"],
    "participants":   ["participants"],
    "awarded_amount": ["awarded_amount"],
    "budgets":        ["budgets"],
    "warranty":       ["warranty_years"],
    "team":           ["team_id"],
    "notes":          ["notes"],
}
FIELD_LABELS = [("status", "狀態"), ("nda", "保密文件"),
    ("contract", "合約"), ("name", "案名"),
    ("contract_no", "契約號"), ("part_no", "料號"), ("so_number", "SO number"),
    ("schedule", "期程/里程碑"), ("participants", "參與人員"),
    ("awarded_amount", "決標金額"), ("budgets", "預估認列"),
    ("warranty", "保固"), ("team", "團隊"), ("notes", "備註")]


def load_perm_matrix(db):
    """{role: {field: level}},未設定 = writable"""
    m = {r: {} for r in PERM_ROLES}
    for row in db.execute("SELECT role, field, level FROM field_perms"):
        if row["role"] in m and row["field"] in FIELD_MAP:
            m[row["role"]][row["field"]] = row["level"]
    return m


LEVEL_RANK = {"invisible": 0, "readonly": 1, "writable": 2}


def effective_roles(db):
    """請求者的有效角色集合 = 各團隊角色;無團隊歸屬視為 dev (最小權限檢視者)。
    管理者/開發模式回傳 None 表示不受限。"""
    u = getattr(g, "user", None)
    if u is None or u["role"] == "admin":
        return None
    roles = {r["role"] for r in db.execute(
        "SELECT role FROM team_members WHERE user_id = ?", (u["id"],))}
    return roles or {"dev"}


def visible_team_ids(db):
    """請求者可檢視的團隊 id 集合 (Bug2:團隊即資料牆)。
    回傳 None = 不受限 (管理者/開發模式);
    回傳 set = 僅這些團隊的專案可見 (無團隊者得空 set,看不到任何專案)。"""
    u = getattr(g, "user", None)
    if u is None or u["role"] == "admin":
        return None
    return {r["team_id"] for r in db.execute(
        "SELECT team_id FROM team_members WHERE user_id = ?", (u["id"],))}


def effective_levels(db):
    """{矩陣鍵: level},多重角色取最寬鬆;None = 不受限"""
    roles = effective_roles(db)
    if roles is None:
        return None
    matrix = load_perm_matrix(db)
    out = {}
    for fkey in FIELD_MAP:
        best = max(LEVEL_RANK[(matrix.get(r) or {}).get(fkey, "writable")]
                   for r in roles)
        out[fkey] = ["invisible", "readonly", "writable"][best]
    return out


def hide_not_awarded(db):
    """B.a:開發人員 (有效角色僅 dev) 看不到未成案"""
    roles = effective_roles(db)
    return roles is not None and roles <= {"dev"}


def strip_invisible(d, db):
    levels = effective_levels(db)
    if levels is None:
        return d
    for fkey, level in levels.items():
        if level == "invisible":
            for col in FIELD_MAP[fkey]:
                d.pop(col, None)
    return d


def writable_fields(db):
    """回傳目前請求者不可寫的實際欄位集合"""
    levels = effective_levels(db)
    if levels is None:
        return set()
    blocked = set()
    for fkey, level in levels.items():
        if level in ("invisible", "readonly"):
            blocked.update(FIELD_MAP[fkey])
    return blocked


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


# 欄位遷移清單:CREATE IF NOT EXISTS 不會對「既有表」加欄位,
# 新增欄位一律登記於此,init_db 會自動 ALTER TABLE 補上 (冪等)
MIGRATIONS = [
    ("projects", "warranty_years", "INTEGER"),
    ("projects", "team_id", "INTEGER"),
    ("projects", "contract_scan", "INTEGER NOT NULL DEFAULT 0"),
    ("projects", "nda_date", "TEXT"),
    ("projects", "nda_scan", "INTEGER NOT NULL DEFAULT 0"),
    ("projects", "notify_days_before", "INTEGER"),
    ("users", "notify_email", "TEXT"),
    ("users", "company_name", "TEXT"),
]


def init_db():
    db = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        db.executescript(f.read())
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
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
    wy = out.get("warranty_years")
    if wy is not None and (not isinstance(wy, int) or wy < 0 or wy > 50):
        return None, "保固年數須為 0~50 的整數"
    if out.get("nda_date") and parse_iso(out["nda_date"]) is None:
        return None, "保密文件簽署日期格式須為 YYYY-MM-DD"
    nd = out.get("notify_days_before")
    if nd is not None and (not isinstance(nd, int) or nd < 0 or nd > 365):
        return None, "提醒天數須為 0~365 的整數"
    for b in ("contract_scan", "nda_scan"):
        if b in out:
            out[b] = 1 if out[b] else 0
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
    if hide_not_awarded(db):
        sql += " AND status != 'not_awarded'"
    # Bug2:團隊即資料牆 — 非管理者只能看自己所屬團隊的專案
    vis = visible_team_ids(db)
    if vis is not None:
        if not vis:
            return jsonify([])          # 無團隊 → 看不到任何專案
        ph = ",".join("?" * len(vis))
        sql += f" AND team_id IN ({ph})"
        args.extend(vis)
    if year:
        sql += " AND year = ?"
        args.append(year)
    # 依履約起始日排序 (先執行的在上面);未填日期者排最後,同日以 id 穩定排序
    sql += " ORDER BY (start_date IS NULL), start_date, id"
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
    return jsonify([strip_invisible(project_to_dict(
        r, budget_map.get(r["id"], []), ms_map.get(r["id"], [])), db)
        for r in rows])


@app.get("/api/projects/<int:pid>")
@VIEW
def get_project(pid):
    db = get_db()
    p = fetch_project(db, pid)
    if p is None:
        return jsonify({"error": "找不到專案"}), 404
    if hide_not_awarded(db) and p.get("status") == "not_awarded":
        return jsonify({"error": "找不到專案"}), 404
    # Bug2:團隊即資料牆 — 非所屬團隊的專案視同不存在
    vis = visible_team_ids(db)
    if vis is not None and p.get("team_id") not in vis:
        return jsonify({"error": "找不到專案"}), 404
    return jsonify(strip_invisible(p, db))


@app.post("/api/projects")
@EDIT_GLOBAL
def create_project():
    data = request.get_json(silent=True) or {}
    db = get_db()
    blocked = writable_fields(db)
    for k in list(data.keys()):
        if k in blocked:
            data.pop(k)
    fields, err = validate_project(data)
    if err:
        return jsonify({"error": err}), 400
    u = getattr(g, "user", None)
    if u is not None and u["role"] != "admin":
        tid = fields.get("team_id")
        ok = tid is not None and db.execute(
            "SELECT 1 FROM team_members WHERE team_id = ? AND user_id = ?",
            (tid, u["id"])).fetchone()
        if not ok:
            return jsonify({"error": "請指定你所屬的團隊"}), 403
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
    blocked = writable_fields(db)
    for k in list(data.keys()):
        if k in blocked:
            data.pop(k)
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
    # 触发点5:结案通知 — 专案 status 改为 closed 的当下即时发送
    new_status = fields.get("status", old["status"])
    if new_status == "closed" and old["status"] != "closed":
        proj = db.execute("SELECT id, name, team_id FROM projects"
                          " WHERE id = ?", (pid,)).fetchone()
        if proj and proj["team_id"]:
            recipients = resolve_recipients(db, proj["team_id"], "project_closed")
            if recipients:
                subject = f"[專案結案] {proj['name']} 已結案"
                body = (f"專案「{proj['name']}」已結案。\n\n"
                        f"結案時間:{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                        f"系統網址:{SYSTEM_URL}\n")
                # 结案通知走即时寄送 (比照系统通知,但收件人来自专案矩阵)
                dry = os.environ.get("PM_NOTIFY_DRYRUN", "1") == "1"
                stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
                item = {"dedup_key": f"project_closed:{pid}:{stamp}",
                        "project_id": pid, "subject": subject,
                        "recipients": recipients}
                if dry:
                    _record(db, "project_closed", item, "dryrun", "乾跑模式")
                else:
                    try:
                        send_email(recipients, subject, body)
                        _record(db, "project_closed", item, "sent", None)
                    except Exception as e:
                        _record(db, "project_closed", item, "failed", str(e)[:300])
                db.commit()
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
@ADMIN
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
            " awarded_amount, kickoff_date, warranty_years, team_id,"
            " contract_scan, nda_date, nda_scan, notes, sort_order, copied_from)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_year, r["status"], r["contract_no"], r["part_no"],
             r["so_number"], r["name"], r["start_date"], r["end_date"],
             r["participants"], r["awarded_amount"], r["kickoff_date"],
             r["warranty_years"], r["team_id"], r["contract_scan"],
             r["nda_date"], r["nda_scan"], r["notes"], r["sort_order"], r["id"]),
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
    teams = user_teams(db, user["id"])
    editable = "all" if user["role"] == "admin" else [m["team_id"] for m in teams]
    # my_levels:此使用者對每個矩陣欄位的有效等級 (admin=全 writable)
    if user["role"] == "admin":
        my_levels = {k: "writable" for k in FIELD_MAP}
    else:
        roles = {m["role"] for m in teams} or {"dev"}
        matrix = load_perm_matrix(db)
        my_levels = {}
        for fkey in FIELD_MAP:
            best = max(LEVEL_RANK[(matrix.get(r) or {}).get(fkey, "writable")]
                       for r in roles)
            my_levels[fkey] = ["invisible", "readonly", "writable"][best]
    return {"user": {k: user[k] for k in
                     ("id", "email", "name", "role", "status", "can_edit",
                      "notify_email", "company_name")},
            "teams": teams,
            "editable": editable,
            "my_levels": my_levels,
            "hide_not_awarded": user["role"] != "admin" and
                                ({m["role"] for m in teams} or {"dev"}) <= {"dev"}}


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
        if is_admin:
            # 首位管理者:直接建号启用,不需补充资料
            cur = db.execute(
                "INSERT INTO users (email, name, role, status, can_edit)"
                " VALUES (?, ?, 'admin', 'active', 1)",
                (info["email"], info["name"]))
            write_audit(db, "create", "users", cur.lastrowid,
                        {"email": info["email"], "reason": "首位管理者"})
            db.commit()
            BACKUP.mark_dirty()
            row = db.execute("SELECT * FROM users WHERE id = ?",
                             (cur.lastrowid,)).fetchone()
        else:
            # 一般新使用者:先不建号,要求补充公司信箱+姓名 (需求2)
            # 回传经签章的注册凭证,前端填完资料再送 /auth/register
            reg_token = auth_core.issue_register_token(
                info["email"], info["name"])
            return jsonify({"code": "register",
                            "register_token": reg_token,
                            "email": info["email"],
                            "google_name": info["name"]}), 200
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


@app.post("/api/auth/register")
def auth_register():
    """完成注册:验证注册凭证 + 补充的公司信箱/姓名,建立 pending 帐号,
    并即时发出「注册申请提醒」给申请人与管理者。"""
    if not auth_core.AUTH_ENABLED:
        return jsonify({"error": "登入功能未啟用"}), 400
    data = request.get_json(silent=True) or {}
    reg_token = data.get("register_token", "")
    try:
        email, gname = auth_core.verify_register_token(reg_token)
    except Exception:
        return jsonify({"error": "註冊憑證無效或已過期,請重新登入"}), 401
    company_email = (data.get("notify_email") or "").strip()
    company_name = (data.get("company_name") or "").strip()
    if not company_email or "@" not in company_email or len(company_email) > 200:
        return jsonify({"error": "請填寫有效的公司信箱"}), 400
    if not company_name or len(company_name) > 100:
        return jsonify({"error": "請填寫姓名"}), 400
    db = get_db()
    # 防重复:期间若已被建号 (例如重复送出),直接回 pending
    exist = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if exist:
        return jsonify({"code": "pending",
                        "error": "帳號已建立,請等待管理者核准"}), 403
    cur = db.execute(
        "INSERT INTO users (email, name, role, status, can_edit,"
        " notify_email, company_name) VALUES (?, ?, 'dev', 'pending', 0, ?, ?)",
        (email, gname, company_email, company_name))
    uid = cur.lastrowid
    write_audit(db, "create", "users", uid,
                {"email": email, "reason": "註冊申請 (待核准)"})
    db.commit()
    BACKUP.mark_dirty()
    # 触发点1:注册申请提醒 — 发给申请人本人 + 所有管理者
    send_system_notify(db, "reg_pending", [company_email],
        "[專案管理系統] 註冊申請已收到,待管理者核准",
        f"{company_name} 您好,\n\n您的帳號註冊申請已成功送出,目前狀態為"
        f"「待核准」,請等待管理者審核啟用。\n\n登入信箱:{email}\n"
        f"公司信箱:{company_email}\n系統網址:{SYSTEM_URL}\n")
    admins = all_admin_emails(db)
    if admins:
        send_system_notify(db, "reg_pending", admins,
            "[專案管理系統] 有新的帳號註冊申請待核准",
            f"有使用者提出帳號註冊申請,請至權限設定頁審核:\n\n"
            f"姓名:{company_name}\n登入信箱:{email}\n"
            f"公司信箱:{company_email}\n\n權限設定頁:{SYSTEM_URL}\n")
    db.commit()
    return jsonify({"code": "pending",
                    "error": "帳號已建立,請等待管理者核准"}), 403


@app.get("/api/auth/me")
@VIEW
def auth_me():
    if not auth_core.AUTH_ENABLED:
        return jsonify({"user": None, "editable": "all"})
    return jsonify(_me_payload(get_db(), g.user))


# =============================================================
# 通知系統 (可擴充框架;第一個類型:里程碑到期提醒)
# =============================================================
# 专案类通知:per-team 角色矩阵 + 事件/排程触发
NOTIFY_TYPES = [
    {"key": "milestone_due", "label": "里程碑到期提醒"},
    {"key": "project_closed", "label": "專案結案通知"},
]
NOTIFY_ROLES = ("pm", "dept_head", "sales", "dev")

# 系统类通知:不分角色、即时触发、发给事件当事人 (+管理者)。
# enabled 存 app_settings (key = sysnotify:<key>);预设启用。
SYSTEM_NOTIFY_TYPES = [
    {"key": "reg_pending",      "label": "註冊申請提醒",
     "desc": "使用者完成註冊申請、待管理者核准時,通知申請人與管理者"},
    {"key": "reg_approved",     "label": "註冊成功提醒",
     "desc": "帳號經核准啟用時,通知當事人 (含系統網址)"},
    {"key": "account_disabled", "label": "帳號停用提醒",
     "desc": "帳號被停用時,通知當事人"},
    {"key": "perm_assigned",    "label": "權限設定成功提醒",
     "desc": "被加入團隊/設定角色時,通知當事人 (含團隊與角色)"},
]
SYSTEM_URL = "https://fantasy1164.github.io/pm-system/"


def sys_notify_enabled(db, key):
    """系统通知是否启用 (预设启用)"""
    return get_setting(db, f"sysnotify:{key}", "1") == "1"


def all_admin_emails(db):
    """所有 active 管理者的公司信箱 (无则退回登入 email)"""
    out = []
    for r in db.execute(
            "SELECT notify_email, email FROM users"
            " WHERE role = 'admin' AND status = 'active'"):
        e = (r["notify_email"] or "").strip() or (r["email"] or "").strip()
        if e:
            out.append(e)
    return out


def send_system_notify(db, key, recipients, subject, body):
    """即时寄送系统通知 (未启用则跳过);记录进 notifications 表。
    dedup_key 带时间戳,每次事件独立记录 (系统通知不做跨次去重)。"""
    if not sys_notify_enabled(db, key):
        return
    recipients = [r for r in dict.fromkeys(  # 去重且保序
        (e or "").strip() for e in recipients) if r]
    if not recipients:
        return
    dry = os.environ.get("PM_NOTIFY_DRYRUN", "1") == "1"
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    item = {"dedup_key": f"{key}:{stamp}", "project_id": None,
            "subject": subject, "recipients": recipients}
    if dry:
        _record(db, key, item, "dryrun", "乾跑模式")
        return
    try:
        send_email(recipients, subject, body)
        _record(db, key, item, "sent", None)
    except Exception as e:
        _record(db, key, item, "failed", str(e)[:300])

# 掃描頻率選項 (分鐘);外部排程每 10 分鐘敲門,此值決定節流間隔
SCAN_INTERVALS = [
    {"value": 10, "label": "10 分鐘"},
    {"value": 30, "label": "30 分鐘"},
    {"value": 60, "label": "1 小時"},
    {"value": 360, "label": "6 小時"},
    {"value": 1440, "label": "1 天"},
]
DEFAULT_SCAN_INTERVAL = 1440   # 預設 1 天


def get_setting(db, key, default=None):
    row = db.execute("SELECT value FROM app_settings WHERE key = ?",
                     (key,)).fetchone()
    return row["value"] if row else default


def set_setting(db, key, value):
    db.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)"
               " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, str(value)))


def is_team_pm(db, user, team_id):
    """使用者是否為某團隊的專案經理 (管理者恆真)"""
    if user is None:                       # 開發模式
        return True
    if user["role"] == "admin":
        return True
    return db.execute(
        "SELECT 1 FROM team_members WHERE team_id = ? AND user_id = ?"
        " AND role = 'pm'", (team_id, user["id"])).fetchone() is not None


def load_notify_matrix(db, team_id):
    """{ntype: {role: bool}};未設定的類型預設只發給 pm"""
    m = {nt["key"]: {r: False for r in NOTIFY_ROLES} for nt in NOTIFY_TYPES}
    seen = set()
    for row in db.execute(
            "SELECT ntype, role, enabled FROM team_notify_matrix"
            " WHERE team_id = ?", (team_id,)):
        if row["ntype"] in m and row["role"] in NOTIFY_ROLES:
            m[row["ntype"]][row["role"]] = bool(row["enabled"])
            seen.add(row["ntype"])
    for nt in NOTIFY_TYPES:                 # 未設定過的類型 → 預設發給 pm
        if nt["key"] not in seen:
            m[nt["key"]]["pm"] = True
    return m


def resolve_recipients(db, team_id, ntype):
    """依團隊矩陣勾選的角色,解析出收件 email 清單 (取 notify_email)"""
    matrix = load_notify_matrix(db, team_id)
    roles = [r for r, on in matrix.get(ntype, {}).items() if on]
    if not roles:
        return []
    q = ",".join("?" * len(roles))
    rows = db.execute(
        f"SELECT DISTINCT u.notify_email FROM team_members tm"
        f" JOIN users u ON u.id = tm.user_id"
        f" WHERE tm.team_id = ? AND tm.role IN ({q})"
        f" AND u.status = 'active' AND u.notify_email IS NOT NULL"
        f" AND u.notify_email != ''", [team_id, *roles])
    return [r["notify_email"] for r in rows]


def scan_milestone_due(db, today):
    """回傳待發通知 [{dedup_key, project_id, subject, body, team_id, recipients}]"""
    out = []
    rows = db.execute(
        "SELECT p.id, p.name, p.team_id, p.notify_days_before, p.year"
        " FROM projects p WHERE p.deleted = 0 AND p.team_id IS NOT NULL"
        " AND p.notify_days_before IS NOT NULL").fetchall()
    for p in rows:
        for m in db.execute(
                "SELECT date, name FROM milestones WHERE project_id = ?",
                (p["id"],)):
            due = parse_iso(m["date"])
            if due is None:
                continue
            lead = (due - today).days
            if lead != p["notify_days_before"]:
                continue                    # 只在「剛好提前 N 天」那天發,發一次
            recipients = resolve_recipients(db, p["team_id"], "milestone_due")
            dedup = f"milestone_due:{p['id']}:{m['date']}:{m['name']}"
            subject = f"[專案提醒] {p['name']} 里程碑「{m['name']}」將於 {lead} 天後到期"
            body = (f"專案:{p['name']}\n"
                    f"里程碑:{m['name']}\n"
                    f"到期日:{m['date']} (民國 {roc_str(m['date'])})\n"
                    f"距今:{lead} 天\n")
            out.append({"dedup_key": dedup, "project_id": p["id"],
                        "subject": subject, "body": body,
                        "team_id": p["team_id"], "recipients": recipients})
    return out


def roc_str(iso):
    y, m, d = iso.split("-")
    return f"{int(y) - 1911}/{int(m)}/{int(d)}"


SCANNERS = {"milestone_due": scan_milestone_due}


# ------------------------------------------------------- 通知矩陣 (pm 可寫自己團隊)
@app.get("/api/notify/types")
@VIEW
def notify_types():
    return jsonify({"types": NOTIFY_TYPES, "roles": list(NOTIFY_ROLES)})


@app.get("/api/notify/system")
@VIEW
def get_system_notify():
    """系统通知类型 + 各自启用状态"""
    db = get_db()
    return jsonify({"types": [
        {**t, "enabled": sys_notify_enabled(db, t["key"])}
        for t in SYSTEM_NOTIFY_TYPES]})


@app.put("/api/notify/system")
@ADMIN
def put_system_notify():
    """管理者设定某系统通知的启用/停用"""
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    valid = {t["key"] for t in SYSTEM_NOTIFY_TYPES}
    if key not in valid:
        return jsonify({"error": "無效的通知類型"}), 400
    enabled = "1" if data.get("enabled") else "0"
    db = get_db()
    set_setting(db, f"sysnotify:{key}", enabled)
    write_audit(db, "update", "app_settings", 0,
                {f"sysnotify:{key}": enabled})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"key": key, "enabled": enabled == "1"})


@app.get("/api/notify/matrix/<int:team_id>")
@VIEW
def get_notify_matrix(team_id):
    db = get_db()
    if db.execute("SELECT 1 FROM teams WHERE id = ?", (team_id,)).fetchone() is None:
        return jsonify({"error": "找不到團隊"}), 404
    return jsonify({"team_id": team_id, "matrix": load_notify_matrix(db, team_id)})


@app.put("/api/notify/matrix/<int:team_id>")
@VIEW
def put_notify_matrix(team_id):
    db = get_db()
    user = getattr(g, "user", None)
    if not is_team_pm(db, user, team_id):
        return jsonify({"error": "僅該團隊的專案經理或管理者可設定"}), 403
    data = (request.get_json(silent=True) or {}).get("matrix") or {}
    db.execute("DELETE FROM team_notify_matrix WHERE team_id = ?", (team_id,))
    valid_types = {nt["key"] for nt in NOTIFY_TYPES}
    for ntype, roles in data.items():
        if ntype not in valid_types:
            continue
        for role, on in roles.items():
            if role in NOTIFY_ROLES and on:
                db.execute("INSERT INTO team_notify_matrix"
                           " (team_id, ntype, role, enabled) VALUES (?,?,?,1)",
                           (team_id, ntype, role))
    write_audit(db, "update", "team_notify_matrix", team_id, {"matrix": data})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"team_id": team_id, "matrix": load_notify_matrix(db, team_id)})


# ------------------------------------------------------- 通知歷史
@app.get("/api/notify/history")
@VIEW
def notify_history():
    rows = get_db().execute(
        "SELECT id, ntype, project_id, subject, recipients, status, detail,"
        " created_at FROM notifications ORDER BY id DESC LIMIT 200").fetchall()
    return jsonify([dict(r) for r in rows])


@app.delete("/api/notify/history/<int:nid>")
@ADMIN
def delete_notify(nid):
    db = get_db()
    row = db.execute("SELECT dedup_key FROM notifications WHERE id = ?",
                     (nid,)).fetchone()
    if row is None:
        return jsonify({"error": "找不到紀錄"}), 404
    db.execute("DELETE FROM notifications WHERE id = ?", (nid,))
    write_audit(db, "delete", "notifications", nid,
                {"dedup_key": row["dedup_key"]})
    db.commit()
    BACKUP.mark_dirty()
    # 刪除後該 dedup_key 解除,下次掃描符合條件會重新發送
    return jsonify({"deleted": nid})


@app.delete("/api/notify/history")
@ADMIN
def clear_notify():
    db = get_db()
    n = db.execute("SELECT COUNT(*) c FROM notifications").fetchone()["c"]
    db.execute("DELETE FROM notifications")
    write_audit(db, "delete", "notifications", 0, {"cleared": n})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"cleared": n})


# ------------------------------------------------------- 掃描頻率設定
@app.get("/api/notify/settings")
@VIEW
def get_notify_settings():
    db = get_db()
    interval = int(get_setting(db, "scan_interval", DEFAULT_SCAN_INTERVAL))
    last = get_setting(db, "last_scan_at", None)
    return jsonify({"interval": interval, "options": SCAN_INTERVALS,
                    "last_scan_at": last})


@app.put("/api/notify/settings")
@ADMIN
def put_notify_settings():
    data = request.get_json(silent=True) or {}
    iv = data.get("interval")
    valid = {o["value"] for o in SCAN_INTERVALS}
    if iv not in valid:
        return jsonify({"error": "無效的掃描頻率"}), 400
    db = get_db()
    set_setting(db, "scan_interval", iv)
    write_audit(db, "update", "app_settings", 0, {"scan_interval": iv})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"interval": iv})


# ------------------------------------------------------- 執行掃描 (外部排程呼叫)
@app.post("/api/notify/run")
def notify_run():
    # 以獨立 token 保護 (供 GitHub Actions 等外部排程呼叫,不需登入)
    # 授權:外部排程用 X-Notify-Token;或登入的管理者 (供「立即掃描」按鈕)
    # 帶了 Bearer token 就先嘗試載入使用者 (失敗不擋,還有 token 那條路)
    if request.headers.get("Authorization", "").startswith("Bearer "):
        auth_core.load_current_user(get_db)
    token = os.environ.get("PM_NOTIFY_TOKEN", "")
    given = request.headers.get("X-Notify-Token", "")
    is_admin_user = getattr(g, "user", None) and g.user["role"] == "admin"
    if not is_admin_user and (not token or given != token):
        return jsonify({"error": "unauthorized"}), 401
    db = get_db()
    # force=1 (立即掃描) 無視節流;否則依系統設定的頻率節流
    force = request.args.get("force") == "1" or is_admin_user
    now = datetime.now()
    if not force:
        interval = int(get_setting(db, "scan_interval", DEFAULT_SCAN_INTERVAL))
        last = get_setting(db, "last_scan_at", None)
        if last:
            try:
                elapsed = (now - datetime.fromisoformat(last)).total_seconds()
                if elapsed < interval * 60 - 30:   # 容 30 秒抖動
                    return jsonify({"skipped_throttle": True,
                                    "next_in_sec": int(interval * 60 - elapsed)})
            except ValueError:
                pass
    set_setting(db, "last_scan_at", now.isoformat(timespec="seconds"))
    db.commit()
    dry = os.environ.get("PM_NOTIFY_DRYRUN", "1") == "1"  # 預設乾跑,mailer 接上再關
    today = now.date()
    result = {"scanned": 0, "sent": 0, "skipped": 0, "failed": 0, "dryrun": dry}
    for ntype, scanner in SCANNERS.items():
        for item in scanner(db, today):
            result["scanned"] += 1
            # 去重:僅「成功發送」過才跳過;失敗/乾跑/略過可再次嘗試
            done = db.execute(
                "SELECT 1 FROM notifications WHERE dedup_key = ?"
                " AND status = 'sent'", (item["dedup_key"],)).fetchone()
            if done:
                result["skipped"] += 1
                continue
            recipients = item["recipients"]
            if not recipients:
                # 沒有收件人 (矩陣沒勾或無 notify_email):記錄但標記,避免每天重掃
                _record(db, ntype, item, "skipped", "無收件人")
                result["skipped"] += 1
                continue
            if dry:
                _record(db, ntype, item, "dryrun", "乾跑模式 (mailer 尚未啟用)")
                result["sent"] += 1
                continue
            try:
                send_email(recipients, item["subject"], item["body"])
                _record(db, ntype, item, "sent", None)
                result["sent"] += 1
            except Exception as e:
                _record(db, ntype, item, "failed", str(e)[:300])
                result["failed"] += 1
    db.commit()
    BACKUP.mark_dirty()
    return jsonify(result)


def _record(db, ntype, item, status, detail):
    # upsert:同一 dedup_key 若已有記錄 (如先前失敗/乾跑),更新其狀態與時間;
    # 否則新增。確保「重試成功」能正確覆蓋為 sent,去重判斷才準確。
    row = db.execute("SELECT id FROM notifications WHERE dedup_key = ?",
                     (item["dedup_key"],)).fetchone()
    if row:
        db.execute(
            "UPDATE notifications SET status = ?, detail = ?, recipients = ?,"
            " subject = ?, created_at = datetime('now','localtime')"
            " WHERE id = ?",
            (status, detail, ",".join(item["recipients"]),
             item["subject"], row["id"]))
    else:
        db.execute(
            "INSERT INTO notifications (ntype, dedup_key, project_id,"
            " subject, recipients, status, detail) VALUES (?,?,?,?,?,?,?)",
            (ntype, item["dedup_key"], item["project_id"], item["subject"],
             ",".join(item["recipients"]), status, detail))


def send_email(recipients, subject, body):
    """透過 Gmail API 寄送 (mailer 使用含 gmail.send scope 的憑證)"""
    from mailer import get_mailer
    return get_mailer().send(recipients, subject, body)


# ------------------------------------------------------- teams (B.a)
@app.get("/api/teams")
@VIEW
def list_teams():
    rows = get_db().execute("SELECT id, name FROM teams ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/teams")
@ADMIN
def create_team():
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name:
        return jsonify({"error": "團隊名稱為必填"}), 400
    db = get_db()
    if db.execute("SELECT 1 FROM teams WHERE name = ?", (name,)).fetchone():
        return jsonify({"error": "團隊名稱已存在"}), 400
    cur = db.execute("INSERT INTO teams (name) VALUES (?)", (name,))
    write_audit(db, "create", "teams", cur.lastrowid, {"name": name})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"id": cur.lastrowid, "name": name}), 201


@app.delete("/api/teams/<int:tid>")
@ADMIN
def delete_team(tid):
    db = get_db()
    row = db.execute("SELECT * FROM teams WHERE id = ?", (tid,)).fetchone()
    if row is None:
        return jsonify({"error": "找不到團隊"}), 404
    np = db.execute("SELECT COUNT(*) c FROM projects WHERE team_id = ?"
                    " AND deleted = 0", (tid,)).fetchone()["c"]
    nu = db.execute("SELECT COUNT(*) c FROM team_members WHERE team_id = ?",
                    (tid,)).fetchone()["c"]
    if np or nu:
        return jsonify({"error": f"無法刪除:仍有 {np} 個專案、{nu} 位成員"
                                 f"歸屬於「{row['name']}」,請先移除歸屬"}), 400
    db.execute("DELETE FROM teams WHERE id = ?", (tid,))
    write_audit(db, "delete", "teams", tid, {"name": row["name"]})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"deleted": tid})


# ---------------------------------------------- 欄位權限矩陣 (B.a 下半)
@app.get("/api/perms")
@VIEW
def get_perms():
    return jsonify({"roles": list(PERM_ROLES),
                    "fields": [{"key": k, "label": v} for k, v in FIELD_LABELS],
                    "matrix": load_perm_matrix(get_db())})


@app.put("/api/perms")
@ADMIN
def put_perms():
    data = (request.get_json(silent=True) or {}).get("matrix") or {}
    db = get_db()
    db.execute("DELETE FROM field_perms")
    valid_fields = set(FIELD_MAP)
    for role, fields in data.items():
        if role not in PERM_ROLES:
            continue
        for f, level in fields.items():
            if f not in valid_fields:
                continue
            # 狀態是總表分組骨架,不可 invisible (最多 readonly)
            if f == "status" and level == "invisible":
                level = "readonly"
            if level in ("invisible", "readonly"):
                db.execute("INSERT INTO field_perms (role, field, level)"
                           " VALUES (?,?,?)", (role, f, level))
    write_audit(db, "update", "field_perms", 0, {"matrix": data})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"matrix": load_perm_matrix(db)})


def user_teams(db, uid):
    return [{"team_id": r["team_id"], "role": r["role"]} for r in db.execute(
        "SELECT team_id, role FROM team_members WHERE user_id = ?", (uid,))]


# ------------------------------------------------------- users (admin)
USER_EDITABLE = ("role", "status", "can_edit", "notify_email", "company_name")
# 联络资讯类栏位:改自己的也允许 (其余 role/status/teams 改自己会被挡)
SELF_EDITABLE = ("notify_email", "company_name")


@app.get("/api/users")
@ADMIN
def list_users():
    db = get_db()
    rows = db.execute(
        "SELECT id, email, name, role, status, can_edit, notify_email, company_name, created_at"
        " FROM users ORDER BY status = 'pending' DESC, id").fetchall()
    return jsonify([{**dict(r), "teams": user_teams(db, r["id"])} for r in rows])


@app.put("/api/users/<int:uid>")
@ADMIN
def update_user(uid):
    db = get_db()
    old = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if old is None:
        return jsonify({"error": "找不到使用者"}), 404
    me = getattr(g, "user", None)
    is_self = me and me["id"] == uid
    data = request.get_json(silent=True) or {}
    fields = {k: data[k] for k in USER_EDITABLE if k in data}
    # 改自己时:只允许联络资讯,敏感栏位 (role/status/teams) 一律忽略避免误锁
    if is_self:
        sensitive = [k for k in fields if k not in SELF_EDITABLE]
        for k in sensitive:
            fields.pop(k)
        if "teams" in data:
            return jsonify({"error": "不可修改自己的团队归属 (避免误锁)"}), 400
    if "notify_email" in fields:
        ne = (fields["notify_email"] or "").strip()
        if ne and ("@" not in ne or len(ne) > 200):
            return jsonify({"error": "通知信箱格式不正確"}), 400
        fields["notify_email"] = ne or None
    if "company_name" in fields:
        cn = (fields["company_name"] or "").strip()
        if len(cn) > 100:
            return jsonify({"error": "姓名過長"}), 400
        fields["company_name"] = cn or None
    if "role" in fields and fields["role"] not in ("admin", "dev"):
        return jsonify({"error": "角色僅接受 admin(管理者)/dev(一般成員)"}), 400
    if "status" in fields and fields["status"] not in (
            "pending", "active", "disabled"):
        return jsonify({"error": "無效的狀態"}), 400
    changes = {k: [old[k], v] for k, v in fields.items() if old[k] != v}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE users SET {sets},"
                   " updated_at = datetime('now','localtime') WHERE id = ?",
                   list(fields.values()) + [uid])
    # 團隊歸屬 (B.a):pending/disabled 使用者不歸屬任何團隊
    final_status = fields.get("status", old["status"])
    if "teams" in data or final_status != "active":
        db.execute("DELETE FROM team_members WHERE user_id = ?", (uid,))
        if final_status == "active":
            for m in (data.get("teams") or []):
                r = m.get("role", "dev")
                if r not in ("pm", "dept_head", "sales", "dev"):
                    r = "dev"
                db.execute("INSERT OR IGNORE INTO team_members"
                           " (team_id, user_id, role) VALUES (?,?,?)",
                           (int(m["team_id"]), uid, r))
        changes["teams"] = ["(replaced)", data.get("teams")
                            if final_status == "active" else []]
    if changes:
        write_audit(db, "update", "users", uid, changes)
    db.commit()
    BACKUP.mark_dirty()
    row = db.execute("SELECT id, email, name, role, status, can_edit, notify_email,"
                     " company_name FROM users WHERE id = ?", (uid,)).fetchone()
    # ── 系统通知即时触发 (对他人操作时) ──────────────────────
    if not is_self:
        target_mail = (row["notify_email"] or "").strip() or row["email"]
        target_name = row["company_name"] or row["name"] or ""
        old_status = old["status"]
        new_status = row["status"]
        # 触发点2:注册成功 (status → active,且原本非 active)
        if new_status == "active" and old_status != "active":
            send_system_notify(db, "reg_approved", [target_mail],
                "[專案管理系統] 您的帳號已核准啟用",
                f"{target_name} 您好,\n\n您的帳號已通過管理者核准,現在可以登入使用。\n\n"
                f"登入信箱:{row['email']}\n系統網址:{SYSTEM_URL}\n\n"
                f"請使用您的 Google 帳號登入。\n")
        # 触发点3:帐号停用 (status → disabled,且原本非 disabled)
        if new_status == "disabled" and old_status != "disabled":
            send_system_notify(db, "account_disabled", [target_mail],
                "[專案管理系統] 您的帳號已被停用",
                f"{target_name} 您好,\n\n您的帳號已被管理者停用,目前無法登入系統。\n"
                f"如有疑問請洽系統管理者。\n\n登入信箱:{row['email']}\n")
        # 触发点4:权限设定成功 (teams 有变更且最终为 active)
        if "teams" in data and new_status == "active":
            tms = user_teams(db, uid)
            if tms:
                ROLE_TXT = {"pm": "專案經理", "dept_head": "部門主管",
                            "sales": "業務", "dev": "開發人員"}
                lines = []
                for m in tms:
                    tn = db.execute("SELECT name FROM teams WHERE id = ?",
                                    (m["team_id"],)).fetchone()
                    lines.append(f"・{tn['name'] if tn else m['team_id']}"
                                 f"({ROLE_TXT.get(m['role'], m['role'])})")
                send_system_notify(db, "perm_assigned", [target_mail],
                    "[專案管理系統] 您的團隊與權限已設定",
                    f"{target_name} 您好,\n\n您已被加入以下團隊,權限如下:\n\n"
                    + "\n".join(lines)
                    + f"\n\n系統網址:{SYSTEM_URL}\n")
        db.commit()
    return jsonify({**dict(row), "teams": user_teams(db, uid)})


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
