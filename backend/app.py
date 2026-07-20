# -*- coding: utf-8 -*-
"""專案期程預算管理系統 — 後端 API (第一階段: 資料模型 + CRUD)

本階段尚未啟用登入,所有寫入操作以 request header `X-User` 記入 audit log,
未帶則記為 local-dev。第四階段會以 JWT 取代。
"""
import hmac
import json
import os
import sqlite3
from datetime import date, datetime

from flask import Flask, Response, abort, g, jsonify, request, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

import auth_core
import config
import persistence

BASE_DIR = config.BASE_DIR
DB_PATH = config.DB_PATH
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

# Render 前面有一層反向代理,真實用戶端 IP 在 X-Forwarded-For。
# 不套 ProxyFix 的話 request.remote_addr 會是代理 IP,導致所有使用者
# 被限流器視為同一來源而互相拖累。x_for=1 = 信任一層代理 (Render 標準)。
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# 速率限制:預設每 IP 每分鐘上限,擋一般性濫用/DoS;登入類端點另加嚴格限制
# (見各端點的 @limiter.limit)。單 worker,記憶體儲存;instance 靠定時 ping
# 保溫平常不重啟、計數穩定,redeploy/重啟時計數歸零 (可接受)。
# health 端點豁免 (保溫 ping 與前端喚醒輪詢會頻繁打它)。
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["240 per minute"],
    storage_uri="memory://",
    strategy="fixed-window",
)

BACKUP = persistence.BackupManager(DB_PATH)
_started = False


def startup():
    """開機流程:還原 (或本機直接初始化) -> 啟動備份執行緒。
    由 __main__ 或 wsgi.py 呼叫;seed 等腳本單純 import 不會觸發。"""
    global _started
    if _started:
        return
    _started = True
    app.logger.info("啟動模式: %s", config.summary())
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


def effective_roles(db, team_id="__all__"):
    """請求者的有效角色集合。
    - team_id 未指定 (__all__):使用者在所有團隊的角色聯集 (僅用於全域檢視,
      如列表的欄位可見性上限)。
    - team_id 指定:只取使用者「在該團隊」的角色 (權限資安關鍵:
      編輯某專案時,只能用該專案所屬團隊的角色,不可跨團隊套用)。
      使用者不屬於該團隊 → 空集合 (無任何權限)。
    管理者/開發模式回傳 None 表示不受限。"""
    u = getattr(g, "user", None)
    if u is None or u["role"] == "admin":
        return None
    if team_id == "__all__":
        roles = {r["role"] for r in db.execute(
            "SELECT role FROM team_members WHERE user_id = ?", (u["id"],))}
        return roles or {"dev"}
    # 指定團隊:只取在該團隊的角色;不屬於該團隊 → 空集合 (最嚴格,無任何權限)
    return {r["role"] for r in db.execute(
        "SELECT role FROM team_members WHERE user_id = ? AND team_id IS ?",
        (u["id"], team_id))}


def visible_team_ids(db):
    """請求者可檢視的團隊 id 集合 (Bug2:團隊即資料牆)。
    回傳 None = 不受限 (管理者/開發模式);
    回傳 set = 僅這些團隊的專案可見 (無團隊者得空 set,看不到任何專案)。"""
    u = getattr(g, "user", None)
    if u is None or u["role"] == "admin":
        return None
    return {r["team_id"] for r in db.execute(
        "SELECT team_id FROM team_members WHERE user_id = ?", (u["id"],))}


def effective_levels(db, team_id="__all__"):
    """{矩陣鍵: level},多重角色取最寬鬆;None = 不受限。
    指定 team_id 時只依「該團隊角色」判定 (修跨團隊越權)。
    角色集合為空 (不屬於該團隊) → 全部 invisible (最嚴格)。"""
    roles = effective_roles(db, team_id)
    if roles is None:
        return None
    matrix = load_perm_matrix(db)
    out = {}
    for fkey in FIELD_MAP:
        if not roles:
            out[fkey] = "invisible"      # 不屬於該團隊:全不可見
            continue
        best = max(LEVEL_RANK[(matrix.get(r) or {}).get(fkey, "writable")]
                   for r in roles)
        out[fkey] = ["invisible", "readonly", "writable"][best]
    return out


def hide_not_awarded(db):
    """B.a:開發人員 (有效角色僅 dev) 看不到未成案"""
    roles = effective_roles(db)
    return roles is not None and roles <= {"dev"}


def strip_invisible(d, db, team_id="__all__"):
    levels = effective_levels(db, team_id)
    if levels is None:
        return d
    for fkey, level in levels.items():
        if level == "invisible":
            for col in FIELD_MAP[fkey]:
                d.pop(col, None)
    # members (勾選成員) 與 participants 同權限群:不可見時一併移除
    if levels.get("participants") == "invisible":
        d.pop("members", None)
    return d


def writable_fields(db, team_id="__all__"):
    """回傳目前請求者不可寫的實際欄位集合 (指定 team_id 時依該團隊角色)"""
    levels = effective_levels(db, team_id)
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
    # 跨團隊分包:主包/各分包團隊各自一組里程碑、認列、參與成員
    ("milestones", "team_id", "INTEGER"),
    ("budget_allocations", "team_id", "INTEGER"),
    ("project_members", "team_id", "INTEGER"),
    ("project_team_overrides", "notify_days_before", "INTEGER"),
    ("project_team_overrides", "participants", "TEXT NOT NULL DEFAULT ''"),
]


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        db.executescript(f.read())
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    # 清掉孤兒的已讀記錄。正常情況 ON DELETE CASCADE 會處理,但那要連線有開
    # foreign_keys pragma —— 備份還原等路徑用的是另開的連線,不保證開了。
    # 更要命的是 SQLite 會重用 id:殘留的孤兒 read 記錄可能撞上「未來某則新
    # 通知」的 id,害那則新通知一建立就被當成已讀、使用者永遠看不到。開機
    # 清一次,把這個隱患從根上斷掉 (init_db 每次開機必跑,是最穩的落點)。
    db.execute("DELETE FROM notification_reads WHERE notif_id NOT IN"
               " (SELECT id FROM notifications)")
    db.commit()
    # 分包遷移:既有子表的 team_id 補成該專案的主包 team_id (無縫接軌,
    # 既有專案無分包關係、行為完全不變)
    # 分包遷移:既有子表的 team_id 補成該專案的主包 team_id (無縫接軌,
    # 既有專案無分包關係、行為完全不變)。
    #
    # 有唯一約束的表 (budget_allocations、project_members) 要先清殘列:若同一
    # 鍵已經有「主包 team_id」的列、又有一筆 team_id=NULL 的舊殘列,直接把 NULL
    # 補成主包 id 會讓兩列的唯一鍵相同 → UNIQUE constraint failed,連帶讓整個
    # 資料庫開不起來。那筆 NULL 是舊版遺留,主包列才是正解,所以補之前先刪掉
    # 「補上去就會撞」的 NULL 殘列。milestones 沒有這種唯一約束,不受影響。
    db.execute(
        "DELETE FROM budget_allocations WHERE team_id IS NULL AND EXISTS ("
        "  SELECT 1 FROM budget_allocations b2, projects p"
        "  WHERE p.id = budget_allocations.project_id"
        "    AND b2.project_id = budget_allocations.project_id"
        "    AND b2.year = budget_allocations.year"
        "    AND b2.team_id = p.team_id)")
    db.execute(
        "DELETE FROM project_members WHERE team_id IS NULL AND EXISTS ("
        "  SELECT 1 FROM project_members m2, projects p"
        "  WHERE p.id = project_members.project_id"
        "    AND m2.project_id = project_members.project_id"
        "    AND m2.user_id = project_members.user_id"
        "    AND m2.team_id = p.team_id)")
    for tbl in ("milestones", "budget_allocations", "project_members"):
        db.execute(
            f"UPDATE {tbl} SET team_id = ("
            f"  SELECT p.team_id FROM projects p WHERE p.id = {tbl}.project_id)"
            f" WHERE team_id IS NULL")
    db.commit()
    _rebuild_constraints_for_subcontract(db)
    db.commit()
    db.close()


def _rebuild_constraints_for_subcontract(db):
    """SQLite 無法 ALTER 既有 PK/UNIQUE;分包需要 team_id 進入鍵。
    偵測舊約束並重建表 (一次性,冪等)。"""
    # project_members:PK 需含 team_id
    pk = [r[1] for r in db.execute("PRAGMA table_info(project_members)") if r[5]]
    if "team_id" not in pk:
        db.executescript("""
            CREATE TABLE project_members_new (
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                team_id INTEGER,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                note TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (project_id, team_id, user_id));
            INSERT INTO project_members_new (project_id, team_id, user_id, note)
                SELECT project_id, team_id, user_id, note FROM project_members;
            DROP TABLE project_members;
            ALTER TABLE project_members_new RENAME TO project_members;
            CREATE INDEX IF NOT EXISTS idx_pm_project ON project_members (project_id);
        """)
    # budget_allocations:UNIQUE 需含 team_id
    idx_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table'"
        " AND name='budget_allocations'").fetchone()
    if idx_sql and "team_id, year" not in (idx_sql[0] or ""):
        db.executescript("""
            CREATE TABLE budget_allocations_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                team_id INTEGER,
                year INTEGER NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0,
                UNIQUE (project_id, team_id, year));
            INSERT INTO budget_allocations_new (id, project_id, team_id, year, amount)
                SELECT id, project_id, team_id, year, amount FROM budget_allocations;
            DROP TABLE budget_allocations;
            ALTER TABLE budget_allocations_new RENAME TO budget_allocations;
        """)


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


def project_to_dict(row, budgets, milestones=(), members=()):
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
    # 參與成員:勾選的已註冊成員 (含公司姓名/認證姓名 供顯示) + 各自備註
    d["members"] = [
        {"user_id": m["user_id"],
         "name": m["company_name"] or m["name"] or m["email"],
         "note": m["note"] or ""} for m in members
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


def upsert_milestones(db, project_id, ms, team_id):
    db.execute("DELETE FROM milestones WHERE project_id = ? AND team_id IS ?",
               (project_id, team_id))
    for m in ms:
        db.execute("INSERT INTO milestones (project_id, team_id, date, name)"
                   " VALUES (?, ?, ?, ?)", (project_id, team_id, m["date"], m["name"]))


def upsert_budgets(db, project_id, budgets, team_id):
    db.execute("DELETE FROM budget_allocations WHERE project_id = ? AND team_id IS ?",
               (project_id, team_id))
    for b in budgets or []:
        db.execute(
            "INSERT INTO budget_allocations (project_id, team_id, year, amount)"
            " VALUES (?, ?, ?, ?)",
            (project_id, team_id, int(b["year"]), int(b.get("amount") or 0)),
        )


def fetch_members(db, project_id, team_id):
    """回傳某團隊在此專案的勾選成員 (join users 取顯示名)"""
    return db.execute(
        "SELECT pm.user_id, pm.note, u.company_name, u.name, u.email"
        " FROM project_members pm JOIN users u ON u.id = pm.user_id"
        " WHERE pm.project_id = ? AND pm.team_id IS ? ORDER BY u.id",
        (project_id, team_id)).fetchall()


def validate_members(db, project_id, members, team_id):
    """清洗成員清單;僅接受「指定 team_id 團隊」的成員,回傳 (list, err)。"""
    valid_ids = set()
    if team_id:
        valid_ids = {r["user_id"] for r in db.execute(
            "SELECT user_id FROM team_members WHERE team_id = ?", (team_id,))}
    out = []
    seen = set()
    for m in members or []:
        try:
            uid = int(m.get("user_id"))
        except (TypeError, ValueError):
            continue
        if uid in seen or uid not in valid_ids:
            continue        # 去重 + 只收該團隊成員 (擋越權塞任意 user)
        seen.add(uid)
        note = (m.get("note") or "").strip()[:200]
        out.append({"user_id": uid, "note": note})
    return out, None


def upsert_members(db, project_id, members, team_id):
    db.execute("DELETE FROM project_members WHERE project_id = ? AND team_id IS ?",
               (project_id, team_id))
    for m in members:
        db.execute("INSERT INTO project_members (project_id, team_id, user_id, note)"
                   " VALUES (?, ?, ?, ?)",
                   (project_id, team_id, m["user_id"], m["note"]))


def fetch_project(db, pid, force_view=None):
    row = db.execute(
        "SELECT * FROM projects WHERE id = ? AND deleted = 0", (pid,)
    ).fetchone()
    if row is None:
        return None
    master_team = row["team_id"]
    # force_view=(team_id, is_sub):指定視角 (供雙棲使用者拆兩筆);否則自動判定
    vteam, is_sub = force_view if force_view is not None \
        else viewing_team(db, pid, master_team)
    # 獨立欄位 (認列/里程碑/成員) 取「視角團隊」那組
    budgets = db.execute(
        "SELECT year, amount FROM budget_allocations"
        " WHERE project_id = ? AND team_id IS ? ORDER BY year", (pid, vteam)
    ).fetchall()
    ms = db.execute(
        "SELECT date, name FROM milestones"
        " WHERE project_id = ? AND team_id IS ? ORDER BY date", (pid, vteam)
    ).fetchall()
    members = fetch_members(db, pid, vteam)
    d = project_to_dict(row, budgets, ms, members)
    # 分包視角:備註、提醒天數、其他參與者、決標金額 取該團隊 override
    # (各自獨立);分包看不到也不沿用主包的決標金額
    if is_sub:
        ov = fetch_override(db, pid, vteam)
        d["notes"] = ov["notes"] if ov else ""
        d["notify_days_before"] = ov["notify_days_before"] if ov else None
        d["participants"] = ov["participants"] if ov else ""
        d["awarded_amount"] = ov["awarded_amount"] if ov else None
    # 分包關係資訊 (供前端顯示 label)
    subs = subcontract_teams(db, pid)
    d["subcontract_to"] = subs                    # 主包視角:分包給誰
    d["is_subcontract_view"] = is_sub             # 我是不是以分包身分在看
    d["master_team_id"] = master_team
    d["viewing_team_id"] = vteam
    # 帶「使用者對此專案的欄位權限等級」(依視角團隊角色,修跨團隊越權的 UI 顯示);
    # admin/開發模式 → None 表示全可寫
    lv = effective_levels(db, vteam)
    d["my_levels"] = lv        # None=不受限;否則 {fkey: level}
    # 分包視角:額外帶主包里程碑 (供甘特圖顯示「主:」)
    if is_sub:
        master_ms = db.execute(
            "SELECT date, name FROM milestones"
            " WHERE project_id = ? AND team_id IS ? ORDER BY date",
            (pid, master_team)).fetchall()
        d["master_milestones"] = [dict(m) for m in master_ms]
    return d


# ==================== 跨團隊分包 (方向A) 核心 ====================
def subcontract_teams(db, pid, active_only=True):
    """回傳此專案的分包團隊 id 清單 (預設只取 active)"""
    sql = "SELECT team_id FROM project_subcontracts WHERE project_id = ?"
    if active_only:
        sql += " AND active = 1"
    return [r["team_id"] for r in db.execute(sql, (pid,))]


def is_subcontract_team(db, pid, team_id, active_only=True):
    if team_id is None:
        return False
    return team_id in subcontract_teams(db, pid, active_only)


_UNSET = object()   # 區分「未提供」與「明確設為 None」


def viewing_team(db, pid, master_team_id, want_team=_UNSET):
    """判定當前請求者用哪個團隊的視角看/改此專案。

    want_team:請求端明確指定「要以哪個團隊的視角操作」(前端編輯分包那筆時傳入)。
    這是修正一個嚴重資料 bug 的關鍵 —— 原本純從使用者身分推導視角,導致:
      - 管理者一律被判為主包視角;他從進度總表點開「分包那筆」編輯,資料卻寫回
        主包,把主包的參與人員/認列/里程碑/備註全部蓋掉。
      - 雙棲使用者 (同時在主包與某分包團隊) 同理,永遠被判主包視角。
    身分只能決定「可以用哪些視角」,不能決定「現在正在用哪個視角」—— 後者只有
    前端 (使用者點的是哪一筆) 知道。所以改成:前端明確指定 want_team,後端只做
    權限校驗 (你必須真的屬於/有權管理那個團隊),校驗過就採用。

    回傳 (team_id, is_sub)。"""
    u = getattr(g, "user", None)
    subs = set(subcontract_teams(db, pid))

    # 明確指定視角:校驗後採用 (管理者可用任一視角;一般人只能用自己團隊的視角)
    if want_team is not _UNSET and want_team is not None:
        want_team = int(want_team)
        is_admin = (u is None) or (u["role"] == "admin")
        my_teams = set() if is_admin else {r["team_id"] for r in db.execute(
            "SELECT team_id FROM team_members WHERE user_id = ?", (u["id"],))}
        if want_team == master_team_id and (is_admin or master_team_id in my_teams):
            return master_team_id, False
        if want_team in subs and (is_admin or want_team in my_teams):
            return want_team, True
        # 指定了無權的團隊:落回身分推導 (下方),不直接信任前端

    # 未指定 (或指定無效):從身分推導 —— 管理者/主包成員看主包,分包成員看分包
    if u is None or u["role"] == "admin":
        return master_team_id, False
    my_teams = {r["team_id"] for r in db.execute(
        "SELECT team_id FROM team_members WHERE user_id = ?", (u["id"],))}
    if master_team_id in my_teams:
        return master_team_id, False
    mine_sub = my_teams & subs
    if mine_sub:
        return sorted(mine_sub)[0], True     # 使用者屬多個分包團隊時取最小 id
    return master_team_id, False             # 其他 (理論上看不到,由資料牆擋)


def fetch_override(db, pid, team_id):
    return db.execute(
        "SELECT awarded_amount, notes, notify_days_before, participants"
        " FROM project_team_overrides"
        " WHERE project_id = ? AND team_id = ?", (pid, team_id)).fetchone()


def upsert_override(db, pid, team_id, awarded_amount=None, notes=None,
                    notify_days_before=_UNSET, participants=_UNSET):
    cur = fetch_override(db, pid, team_id)
    aa = awarded_amount if awarded_amount is not None else (cur["awarded_amount"] if cur else None)
    nt = notes if notes is not None else (cur["notes"] if cur else "")
    if notify_days_before is _UNSET:
        nd = cur["notify_days_before"] if cur else None
    else:
        nd = notify_days_before
    if participants is _UNSET:
        pt = cur["participants"] if cur else ""
    else:
        pt = participants or ""
    db.execute(
        "INSERT INTO project_team_overrides"
        " (project_id, team_id, awarded_amount, notes, notify_days_before, participants)"
        " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, team_id)"
        " DO UPDATE SET awarded_amount = excluded.awarded_amount,"
        " notes = excluded.notes, notify_days_before = excluded.notify_days_before,"
        " participants = excluded.participants",
        (pid, team_id, aa, nt, nd, pt))
# ================================================================


# -------------------------------------------------------------------- CORS
@app.after_request
def add_cors(resp):
    # PM_CORS_ORIGIN 支援逗號分隔多來源,例:
    #   https://xxx.github.io,http://localhost:8000
    # 回應時回傳「與請求相符的那一個」;CORS 只是瀏覽器端防線,
    # 真正的存取控制在 JWT,允許 localhost 不影響安全性
    # 預設空:未設定 PM_CORS_ORIGIN 時「不發 Allow-Origin」(瀏覽器擋跨域),
    # 而非自動全開。要全開需主動設 PM_CORS_ORIGIN=* (本機測試用)。
    allowed = config.CORS_ORIGINS
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


# --------------------------------------------------- 前端靜態檔 (單機版專用)
# 線上版的前端在 GitHub Pages,後端不服務靜態檔 (SERVE_FRONTEND=False),
# 這整段不會註冊 —— 線上路由表與重構前完全相同。
if config.SERVE_FRONTEND:

    # 注入標記:讓前端知道「這頁是後端自己吐的」,據此改用同源相對路徑 /api。
    # 關鍵:只改吐出去的那份 HTML,磁碟上的 index.html 一字不動 ——
    # GitHub Pages 拿到的仍是原檔,前端因此維持單一份程式碼。
    # 這比猜測 port 可靠:不論單機版跑在哪個 port 都成立。
    _FLASK_MARKER = '<script>window.PM_SERVED_BY_FLASK=1;</script>\n</head>'

    @app.get("/")
    @limiter.exempt
    def serve_index():
        path = os.path.join(config.FRONTEND_DIR, "index.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        html = html.replace("</head>", _FLASK_MARKER, 1)
        resp = Response(html, mimetype="text/html")
        resp.headers["Cache-Control"] = "no-store"   # git pull 後立刻看到新版
        return resp

    @app.get("/<path:filename>")
    @limiter.exempt
    def serve_frontend_file(filename):
        # 保險:即使路由優先序有意外,也絕不讓靜態檔處理器攔截 API
        if filename.startswith("api/"):
            abort(404)
        return send_from_directory(config.FRONTEND_DIR, filename)


# ------------------------------------------------------------------- routes
@app.get("/api/health")
@limiter.exempt
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
    # 團隊即資料牆 + 分包:非管理者可看「主包是自己團隊」或「分包給自己團隊」的專案
    vis = visible_team_ids(db)
    if vis is not None:
        if not vis:
            return jsonify([])
        ph = ",".join("?" * len(vis))
        sql += (f" AND (team_id IN ({ph})"
                f" OR id IN (SELECT project_id FROM project_subcontracts"
                f"           WHERE active = 1 AND team_id IN ({ph})))")
        args.extend(list(vis) + list(vis))
    if year:
        sql += " AND year = ?"
        args.append(year)
    sql += " ORDER BY (start_date IS NULL), start_date, id"
    rows = db.execute(sql, args).fetchall()
    # 逐案用視角組合;若使用者對同一案身兼主包+分包成員,拆成兩筆
    # (主包視角=分包TO、分包視角=分包FROM),因兩邊獨立資料各自呈現
    out = []
    for r in rows:
        for view in project_views_for_user(db, r["id"], r["team_id"]):
            d = fetch_project(db, r["id"], force_view=view)
            if d is not None:
                # 欄位可見性依「該視角團隊」的角色 (不可跨團隊越權)
                out.append(strip_invisible(d, db, view[0]))
    return jsonify(out)


def project_views_for_user(db, pid, master_team):
    """回傳使用者對此案應呈現的視角清單 [(team_id, is_sub), ...]。
    - 管理者/開發模式:主包視角 + 每個 active 分包團隊視角 (綜觀全貌)
    - 一般使用者:是主包成員→主包視角;是 active 分包團隊成員→各分包視角
      (雙棲者兩者皆有,故可能回傳多筆)"""
    u = getattr(g, "user", None)
    if u is None or u["role"] == "admin":
        views = [(master_team, False)]
        for st in subcontract_teams(db, pid):
            views.append((st, True))                # 管理者也看得到各分包
        return views
    my_teams = {r["team_id"] for r in db.execute(
        "SELECT team_id FROM team_members WHERE user_id = ?", (u["id"],))}
    views = []
    if master_team in my_teams:
        views.append((master_team, False))          # 主包視角
    for st in subcontract_teams(db, pid):
        if st in my_teams:
            views.append((st, True))                # 各分包視角
    if not views:
        views.append((master_team, False))          # 理論上被資料牆擋,保底
    return views


@app.get("/api/projects/<int:pid>")
@VIEW
def get_project(pid):
    db = get_db()
    p = fetch_project(db, pid)
    if p is None:
        return jsonify({"error": "找不到專案"}), 404
    if hide_not_awarded(db) and p.get("status") == "not_awarded":
        return jsonify({"error": "找不到專案"}), 404
    # 團隊即資料牆 + 分包:主包團隊 或 分包給我團隊 才可見
    vis = visible_team_ids(db)
    if vis is not None:
        subs = set(subcontract_teams(db, pid))
        if p.get("master_team_id") not in vis and not (vis & subs):
            return jsonify({"error": "找不到專案"}), 404
    return jsonify(strip_invisible(p, db, p.get("viewing_team_id", "__all__")))


@app.post("/api/projects")
@EDIT_GLOBAL
def create_project():
    data = request.get_json(silent=True) or {}
    db = get_db()
    # 權限資安:新建專案的欄位可寫性依「目標團隊」的角色判定 (不可跨團隊越權)
    target_team = data.get("team_id")
    if target_team is not None:
        target_team = int(target_team)
    blocked = writable_fields(db, target_team)
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
    mteam = fields.get("team_id")
    upsert_budgets(db, pid, data.get("budgets"), mteam)
    ms, err = validate_milestones(data.get("milestones"))
    if err:
        return jsonify({"error": err}), 400
    upsert_milestones(db, pid, ms, mteam)
    if "members" in data and "participants" not in blocked:
        mem, _e = validate_members(db, pid, data["members"], mteam)
        upsert_members(db, pid, mem, mteam)
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
    # 分包視角判定:分包團隊只能改自己的獨立欄位,共享欄位一律唯讀 (硬規則)
    master_team = old["team_id"]
    # as_team:前端明確指定「正在編輯哪個團隊視角的資料」。這是修正嚴重資料
    # bug 的關鍵 —— 沒有它,管理者/雙棲使用者編輯分包那筆會被誤判為主包視角,
    # 把主包資料全部蓋掉。viewing_team 會對 as_team 做權限校驗。
    want = data.get("as_team", _UNSET)
    vteam, is_sub = viewing_team(db, pid, master_team, want)
    # 權限資安關鍵:欄位可寫性只依「使用者在此專案所屬團隊的角色」判定,
    # 不可用使用者在其他團隊的角色 (跨團隊越權)。分包視角用分包團隊。
    blocked = writable_fields(db, vteam)
    for k in list(data.keys()):
        if k in blocked:
            data.pop(k)
    # 若本次同時變更 team_id (指派/改主包團隊),主包視角的獨立資料
    # (里程碑/認列/成員) 應寫到「新的」team_id,否則會遺留在舊 team_id 讀不到
    if not is_sub and "team_id" in data:
        new_team = data.get("team_id")
        if new_team is not None:
            new_team = int(new_team)
        if new_team != vteam:
            vteam = new_team
    SHARED_ONLY_FOR_MASTER = (   # 這些共享欄位分包不可改
        "name", "year", "status", "contract_no", "part_no", "so_number",
        "start_date", "end_date", "kickoff_date", "warranty_years",
        "contract_scan", "nda_date", "nda_scan", "team_id")
    # awarded_amount (決標金額)、participants (其他參與者)、notes、
    # notify_days_before、里程碑、認列、成員:分包各自獨立,不在唯讀集合。
    # 決標金額分包獨立時寫入 override 的 awarded_amount 欄位 (見下方)。
    if is_sub:
        for k in list(data.keys()):
            if k in SHARED_ONLY_FOR_MASTER:
                data.pop(k)   # 分包送共享欄位 → 忽略 (唯讀)
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
    # 分包視角:備註、提醒天數、其他參與者、決標金額 寫入該團隊 override
    # (各自獨立),不動主包 projects
    if is_sub:
        ov_notes = fields.pop("notes", "__none__")
        ov_nd = fields.pop("notify_days_before", "__none__")
        ov_pt = fields.pop("participants", "__none__")
        ov_aa = fields.pop("awarded_amount", "__none__")
        if (ov_notes != "__none__" or ov_nd != "__none__"
                or ov_pt != "__none__" or ov_aa != "__none__"):
            kw = {}
            if ov_notes != "__none__":
                kw["notes"] = ov_notes
            if ov_nd != "__none__":
                kw["notify_days_before"] = ov_nd
            if ov_pt != "__none__":
                kw["participants"] = ov_pt
            if ov_aa != "__none__":
                kw["awarded_amount"] = ov_aa
            upsert_override(db, pid, vteam, **kw)
            changes["override"] = ["(team)", vteam]
    if fields:
        sets = ", ".join(f"{f} = ?" for f in fields)
        db.execute(
            f"UPDATE projects SET {sets},"
            " updated_at = datetime('now','localtime') WHERE id = ?",
            list(fields.values()) + [pid],
        )
    if "budgets" in data:
        upsert_budgets(db, pid, data["budgets"], vteam)
        changes["budgets"] = ["(replaced)", data["budgets"]]
    if "milestones" in data:
        ms, err = validate_milestones(data["milestones"])
        if err:
            return jsonify({"error": err}), 400
        upsert_milestones(db, pid, ms, vteam)
        changes["milestones"] = ["(replaced)", ms]
    if "members" in data and "participants" not in blocked:
        mem, _e = validate_members(db, pid, data["members"], vteam)
        upsert_members(db, pid, mem, vteam)
        changes["members"] = ["(replaced)", mem]
    if changes:
        write_audit(db, "update", "projects", pid, changes)
    db.commit()
    BACKUP.mark_dirty()
    # 触发点5:结案通知 — 专案 status 改为 closed 的当下即时发送
    new_status = fields.get("status", old["status"])
    if new_status == "closed" and old["status"] != "closed":
        proj = db.execute("SELECT id, name, team_id FROM projects"
                          " WHERE id = ?", (pid,)).fetchone()
        # 線上版要有 team_id 才有收件人;單機版沒有團隊,但通知仍該進鈴鐺,
        # 所以單機版放行 (resolve_recipients 會回本機收件人)。
        if proj and (proj["team_id"] or config.IS_STANDALONE):
            recipients = resolve_recipients(db, proj["team_id"], "project_closed")
            if recipients:
                subject = f"[專案結案] {proj['name']} 已結案"
                body = (f"專案「{proj['name']}」已結案。\n\n"
                        f"結案時間:{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                        f"系統網址:{SYSTEM_URL}\n")
                # 结案通知走即时寄送 (比照系统通知,但收件人来自专案矩阵)
                dry = config.NOTIFY_DRYRUN
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


# ==================== 分包關係端點 ====================
def _can_manage_subcontract(db, pid):
    """管理者 或 主包團隊的 pm 才能設定/解除分包"""
    u = getattr(g, "user", None)
    if u is None or u["role"] == "admin":
        return True
    proj = db.execute("SELECT team_id FROM projects WHERE id = ?", (pid,)).fetchone()
    if not proj or proj["team_id"] is None:
        return False
    return db.execute(
        "SELECT 1 FROM team_members WHERE team_id = ? AND user_id = ? AND role = 'pm'",
        (proj["team_id"], u["id"])).fetchone() is not None


@app.get("/api/projects/<int:pid>/subcontracts")
@VIEW
def get_subcontracts(pid):
    db = get_db()
    rows = db.execute(
        "SELECT sc.team_id, sc.active, t.name FROM project_subcontracts sc"
        " JOIN teams t ON t.id = sc.team_id WHERE sc.project_id = ?"
        " ORDER BY sc.active DESC, t.name", (pid,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.put("/api/projects/<int:pid>/subcontracts")
@EDIT_PID
def set_subcontracts(pid):
    """設定分包團隊 (複選)。管理者或主包 pm 可操作。
    傳入的 team_ids 為要生效的分包團隊;未列出的既有分包→軟性斷開 (active=0)。"""
    db = get_db()
    if not _can_manage_subcontract(db, pid):
        return jsonify({"error": "僅管理者或主包團隊的專案經理可設定分包"}), 403
    proj = db.execute("SELECT team_id FROM projects WHERE id = ? AND deleted = 0",
                      (pid,)).fetchone()
    if not proj:
        return jsonify({"error": "找不到專案"}), 404
    master = proj["team_id"]
    want = set(int(t) for t in (request.get_json(silent=True) or {}).get("team_ids", []))
    want.discard(master)                          # 主包不能分包給自己
    valid_teams = {r["id"] for r in db.execute("SELECT id FROM teams")}
    want &= valid_teams
    existing = {r["team_id"]: r["active"] for r in db.execute(
        "SELECT team_id, active FROM project_subcontracts WHERE project_id = ?", (pid,))}
    # 要生效的:新增或接回 (active=1)
    for tid in want:
        if tid in existing:
            db.execute("UPDATE project_subcontracts SET active = 1"
                       " WHERE project_id = ? AND team_id = ?", (pid, tid))
        else:
            db.execute("INSERT INTO project_subcontracts (project_id, team_id, active)"
                       " VALUES (?, ?, 1)", (pid, tid))
    # 既有但不在 want 的:軟性斷開 (保留資料)
    for tid, act in existing.items():
        if tid not in want and act == 1:
            db.execute("UPDATE project_subcontracts SET active = 0"
                       " WHERE project_id = ? AND team_id = ?", (pid, tid))
    write_audit(db, "update", "project_subcontracts", pid, {"team_ids": sorted(want)})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"active": sorted(want)})


@app.delete("/api/projects/<int:pid>/subcontracts/<int:team_id>")
@VIEW
def delete_my_subcontract_data(pid, team_id):
    """分包團隊的 pm 在『已軟性斷開』後,清除自己團隊在此專案的分包足跡。
    僅刪該團隊的獨立資料 (認列/里程碑/成員/override + 分包關係列),不動主包專案。"""
    db = get_db()
    u = getattr(g, "user", None)
    # 權限:該分包團隊的 pm (或管理者)
    is_admin = u is not None and u["role"] == "admin"
    is_sub_pm = u is not None and db.execute(
        "SELECT 1 FROM team_members WHERE team_id = ? AND user_id = ? AND role = 'pm'",
        (team_id, u["id"])).fetchone() is not None
    if not (is_admin or is_sub_pm):
        return jsonify({"error": "僅該分包團隊的專案經理可刪除自己的分包資料"}), 403
    sc = db.execute("SELECT active FROM project_subcontracts"
                    " WHERE project_id = ? AND team_id = ?", (pid, team_id)).fetchone()
    if sc is None:
        return jsonify({"error": "查無此分包關係"}), 404
    if sc["active"] == 1:
        return jsonify({"error": "請先由主包解除分包 (軟性斷開) 後才能刪除"}), 400
    # 清除該團隊的分包足跡
    db.execute("DELETE FROM milestones WHERE project_id = ? AND team_id = ?", (pid, team_id))
    db.execute("DELETE FROM budget_allocations WHERE project_id = ? AND team_id = ?", (pid, team_id))
    db.execute("DELETE FROM project_members WHERE project_id = ? AND team_id = ?", (pid, team_id))
    db.execute("DELETE FROM project_team_overrides WHERE project_id = ? AND team_id = ?", (pid, team_id))
    db.execute("DELETE FROM project_subcontracts WHERE project_id = ? AND team_id = ?", (pid, team_id))
    write_audit(db, "delete", "project_subcontracts", pid, {"team_id": team_id, "purged": True})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"purged_team": team_id})
# ================================================================


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
        # 複製預算:年度複製只複製專案本身,不複製分包關係,所以新專案是純主包,
        # 預算全歸新專案的主包 team_id。只取原專案主包 (team_id = 原專案 team_id)
        # 的預算列來複製 —— 若連分包預算一起塞成主包 team_id,同一年度會出現
        # 重複鍵而撞 UNIQUE。team_id 用 IS 比對,才涵蓋主包 team_id 為 NULL 的情況
        # (單機版、或尚未指派團隊的專案)。
        #
        # 早期版本這裡漏了 team_id (只選 year, amount),複製出來的預算列 team_id
        # 變 NULL,與專案主包結構不一致,下次開機的分包遷移把它補成主包 id 後
        # 撞上既有列 → UNIQUE 崩潰、資料庫開不起來。這就是那個 bug 的源頭。
        db.execute(
            "INSERT INTO budget_allocations (project_id, team_id, year, amount)"
            " SELECT ?, ?, year, amount FROM budget_allocations"
            " WHERE project_id = ? AND team_id IS ?",
            (new_id, r["team_id"], r["id"], r["team_id"]),
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
    """前端據此決定要不要顯示登入畫面 (公開端點,不含機密)。

    mode 供前端隱藏「單機版用不到的功能」(如訊息通知 —— 單機版不寄信)。
    純新增欄位,線上版回 "online",前端行為與新增前相同。
    """
    return jsonify({"auth_enabled": auth_core.AUTH_ENABLED,
                    "google_client_id": auth_core.OAUTH_CLIENT_ID,
                    "mode": config.MODE})


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
@limiter.limit("10 per minute; 40 per hour")
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
@limiter.limit("10 per minute; 40 per hour")
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

# 單機版的通知收件人。單機版沒有真正的使用者信箱,這個值只是讓通知在
# recipients 欄位有個非空的佔位,好讓它照原邏輯產生並進鈴鐺 (走 dryrun,
# 不會真的寄信)。前綴 bell: 讓它一眼看得出不是真 email。鈴鐺的可見性判斷
# 對單機版一律放行 (見 _bell_visible_sql),不靠比對這個值。
BELL_LOCAL_RECIPIENT = "bell:standalone"

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
    dry = config.NOTIFY_DRYRUN
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
    # 單機版沒有登入、沒有團隊、沒有角色矩陣 —— 收件人機制整個不成立。但通知
    # 本身 (結案、里程碑到期…) 仍該讓那個使用者看到。回一個固定的本機收件人,
    # 讓通知照原邏輯產生、走 dryrun 進鈴鐺。線上版走下面原本的矩陣邏輯,不受影響。
    if config.IS_STANDALONE:
        return [BELL_LOCAL_RECIPIENT]
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
    """回傳待發通知 [{dedup_key, project_id, subject, body, team_id, recipients}]
    - 主包里程碑 (team_id=主包):用主包 notify_days_before,發給主包+所有 active 分包團隊
    - 分包里程碑 (team_id=分包):用該分包 override 的 notify_days_before,只發該分包團隊"""
    out = []
    # 線上版只掃有團隊的專案 (收件人來自團隊矩陣);單機版沒有團隊,但里程碑
    # 提醒仍要進鈴鐺,故單機版不加 team_id 限制。
    where = "deleted = 0"
    if not config.IS_STANDALONE:
        where += " AND team_id IS NOT NULL"
    projs = db.execute(
        "SELECT id, name, team_id, notify_days_before, year"
        f" FROM projects WHERE {where}").fetchall()

    def emit(p, m, lead, team_ids_to_notify, tag):
        due_txt = m["date"]
        for tid in team_ids_to_notify:
            recipients = resolve_recipients(db, tid, "milestone_due")
            if not recipients:
                continue
            dedup = f"milestone_due:{p['id']}:{tid}:{m['date']}:{m['name']}"
            subject = f"[專案提醒] {p['name']} {tag}里程碑「{m['name']}」將於 {lead} 天後到期"
            body = (f"專案:{p['name']}\n"
                    f"里程碑:{tag}{m['name']}\n"
                    f"到期日:{due_txt} (民國 {roc_str(m['date'])})\n"
                    f"距今:{lead} 天\n")
            out.append({"dedup_key": dedup, "project_id": p["id"],
                        "subject": subject, "body": body,
                        "team_id": tid, "recipients": recipients})

    for p in projs:
        master = p["team_id"]
        subs = subcontract_teams(db, p["id"])       # active 分包團隊
        # --- 主包里程碑 ---
        if p["notify_days_before"] is not None:
            for m in db.execute(
                    "SELECT date, name FROM milestones"
                    " WHERE project_id = ? AND team_id IS ?", (p["id"], master)):
                due = parse_iso(m["date"])
                if due is None:
                    continue
                lead = (due - today).days
                if lead != p["notify_days_before"]:
                    continue
                emit(p, m, lead, [master] + subs, "")   # 主包+所有分包團隊
        # --- 各分包里程碑 ---
        for st in subs:
            ov = fetch_override(db, p["id"], st)
            nd = ov["notify_days_before"] if ov else None
            if nd is None:
                continue
            for m in db.execute(
                    "SELECT date, name FROM milestones"
                    " WHERE project_id = ? AND team_id IS ?", (p["id"], st)):
                due = parse_iso(m["date"])
                if due is None:
                    continue
                lead = (due - today).days
                if lead != nd:
                    continue
                emit(p, m, lead, [st], "[分包] ")         # 只發該分包團隊
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
def _bell_uid():
    """鈴鐺已讀狀態綁定的使用者 id。

    單機版沒有登入 (g.user 不存在),全機只有一個人,用固定的 0 代表。
    線上版一律有登入使用者 (VIEW 已擋),取其真實 id。
    """
    u = getattr(g, "user", None)
    return u["id"] if u else 0


def _bell_visible_sql():
    """回傳 (WHERE 片段, 參數):限定「這位使用者看得到」的通知。

    規則 (與寄信一致,見需求討論):
      - 管理者、或單機版本機使用者 → 看得到全部
      - 其他人 → 只看 recipients 裡含自己 email / notify_email 的那些
    recipients 是逗號分隔的 email 字串,用 LIKE 包逗號比對,避免 a@x.com
    誤中 aa@x.com;頭尾各補一個逗號讓邊界一致。
    """
    u = getattr(g, "user", None)
    if u is None or u["role"] == "admin":
        return "1", []
    db = get_db()
    row = db.execute("SELECT email, notify_email FROM users WHERE id = ?",
                     (u["id"],)).fetchone()
    mails = [m for m in (row["email"], row["notify_email"]) if m] if row else []
    if not mails:
        return "0", []          # 沒有任何信箱 → 看不到任何專案/系統通知
    clauses = ["(',' || replace(recipients,' ','') || ',') LIKE ?" for _ in mails]
    params = [f"%,{m},%" for m in mails]
    return "(" + " OR ".join(clauses) + ")", params


@app.get("/api/notify/bell")
@VIEW
def notify_bell():
    """鈴鐺清單。預設只回「這位使用者未確認」的通知;history=1 時連已確認的
    也一起回 (清單上方的『顯示歷史訊息』)。每則附 read 欄位供前端分辨。"""
    db = get_db()
    uid = _bell_uid()
    show_history = request.args.get("history") == "1"
    vis_sql, vis_args = _bell_visible_sql()
    sys_keys = [t["key"] for t in SYSTEM_NOTIFY_TYPES]
    sys_ph = ",".join("?" * len(sys_keys))
    # 只納管「實際成立」的通知:sent (線上真的寄了) 與 dryrun (單機版記錄但沒寄)。
    # failed / skipped 不是給使用者看的提醒,是維運紀錄,留在 history 端點。
    sql = (f"SELECT n.id, n.ntype, n.project_id, n.subject, n.status,"
           f" n.created_at, r.read_at,"
           f" CASE WHEN n.ntype IN ({sys_ph}) THEN 'system' ELSE 'project' END"
           f" AS scope"
           f" FROM notifications n"
           f" LEFT JOIN notification_reads r"
           f" ON r.notif_id = n.id AND r.user_id = ?"
           f" WHERE n.status IN ('sent','dryrun') AND {vis_sql}")
    args = list(sys_keys) + [uid] + vis_args
    if not show_history:
        sql += " AND r.read_at IS NULL"
    sql += " ORDER BY n.id DESC LIMIT 200"
    rows = db.execute(sql, args).fetchall()
    out = [dict(r) for r in rows]
    for o in out:
        o["read"] = o.pop("read_at") is not None
    unread = sum(1 for o in out if not o["read"])
    return jsonify({"items": out, "unread": unread})


@app.post("/api/notify/bell/<int:nid>/ack")
@VIEW
def notify_bell_ack(nid):
    """確認單一通知 (寫入這位使用者的已讀)。冪等:重複確認不報錯。"""
    db = get_db()
    exists = db.execute("SELECT 1 FROM notifications WHERE id = ?",
                        (nid,)).fetchone()
    if not exists:
        return jsonify({"error": "找不到通知"}), 404
    db.execute("INSERT OR IGNORE INTO notification_reads (notif_id, user_id)"
               " VALUES (?, ?)", (nid, _bell_uid()))
    db.commit()
    return jsonify({"ok": True})


@app.post("/api/notify/bell/ack-all")
@VIEW
def notify_bell_ack_all():
    """一鍵確認這位使用者目前看得到的所有未讀 —— 只確認「看得到的」,
    絕不替他清掉不屬於他的通知 (可見性條件與清單完全一致)。"""
    db = get_db()
    uid = _bell_uid()
    vis_sql, vis_args = _bell_visible_sql()
    db.execute(
        f"INSERT OR IGNORE INTO notification_reads (notif_id, user_id)"
        f" SELECT n.id, ? FROM notifications n"
        f" WHERE n.status IN ('sent','dryrun') AND {vis_sql}",
        [uid] + vis_args)
    n = db.total_changes
    db.commit()
    return jsonify({"ok": True, "acked": n})


@app.get("/api/notify/history")
@VIEW
def notify_history():
    # scope=project 只回專案類通知;scope=system 只回系統類;預設全部
    scope = request.args.get("scope")
    sys_keys = [t["key"] for t in SYSTEM_NOTIFY_TYPES]
    sql = ("SELECT id, ntype, project_id, subject, recipients, status, detail,"
           " created_at FROM notifications")
    args = []
    if scope == "system":
        ph = ",".join("?" * len(sys_keys))
        sql += f" WHERE ntype IN ({ph})"
        args = sys_keys
    elif scope == "project":
        ph = ",".join("?" * len(sys_keys))
        sql += f" WHERE ntype NOT IN ({ph})"
        args = sys_keys
    sql += " ORDER BY id DESC LIMIT 200"
    rows = get_db().execute(sql, args).fetchall()
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
    token = config.NOTIFY_TOKEN
    given = request.headers.get("X-Notify-Token", "")
    is_admin_user = getattr(g, "user", None) and g.user["role"] == "admin"
    # 常數時間比較,消除 token 比對的 timing side-channel
    token_ok = bool(token) and hmac.compare_digest(given, token)
    if not is_admin_user and not token_ok:
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
    result = run_scanners(db, now.date())
    db.commit()
    BACKUP.mark_dirty()
    return jsonify(result)


def run_scanners(db, today):
    """跑所有排程掃描 (目前只有里程碑到期),把成立的通知記錄進 notifications。

    抽成獨立函式,讓 notify/run 端點與單機版的「啟動時掃一次」共用同一套邏輯
    與去重規則。回傳統計 dict。呼叫端負責 commit 與 mark_dirty。
    """
    dry = config.NOTIFY_DRYRUN
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
    return result


def startup_scan():
    """單機版啟動時掃一次里程碑到期。

    線上版的掃描靠外部排程 (GitHub Actions 等) 定時打 notify/run;單機版離線、
    沒有排程器,所以改成「每次開程式時自己掃一次」—— 使用者每天開來用的時候,
    當天該提醒的里程碑就會進鈴鐺。去重靠 dedup_key,重掃不會製造重複 (見
    run_scanners 與 _record)。只在單機版執行;線上版呼叫此函式是無害的 no-op。
    """
    if not config.IS_STANDALONE:
        return
    with app.app_context():
        db = get_db()
        try:
            result = run_scanners(db, datetime.now().date())
            db.commit()
            BACKUP.mark_dirty()
            app.logger.info("啟動掃描完成: %s", result)
        except Exception as e:
            app.logger.warning("啟動掃描失敗 (不影響服務): %s", e)


def _record(db, ntype, item, status, detail):
    # upsert:同一 dedup_key 若已有記錄 (如先前失敗/乾跑),更新其狀態與時間;
    # 否則新增。確保「重試成功」能正確覆蓋為 sent,去重判斷才準確。
    row = db.execute("SELECT id, status FROM notifications WHERE dedup_key = ?",
                     (item["dedup_key"],)).fetchone()
    if row:
        # created_at 只在狀態真的改變時才刷新。單機版每次啟動都重掃,若無條件
        # 更新時間,一則兩週前的里程碑提醒會被刷成「今天開機的時刻」—— 使用者
        # 會覺得時間錯亂。狀態沒變 (dryrun→dryrun) 就保留原本的產生時間。
        if row["status"] == status:
            db.execute(
                "UPDATE notifications SET detail = ?, recipients = ?,"
                " subject = ? WHERE id = ?",
                (detail, ",".join(item["recipients"]), item["subject"], row["id"]))
        else:
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


@app.get("/api/teams/<int:tid>/members")
@VIEW
def team_member_list(tid):
    """某團隊的 active 成員清單 (供專案參與人勾選;顯示公司姓名)"""
    rows = get_db().execute(
        "SELECT u.id, u.company_name, u.name, u.email, tm.role"
        " FROM team_members tm JOIN users u ON u.id = tm.user_id"
        " WHERE tm.team_id = ? AND u.status = 'active' ORDER BY u.id", (tid,)
    ).fetchall()
    ROLE_TXT = {"pm": "專案經理", "dept_head": "部門主管",
                "sales": "業務", "dev": "開發人員"}
    return jsonify([{
        "user_id": r["id"],
        "name": r["company_name"] or r["name"] or r["email"],
        "role": ROLE_TXT.get(r["role"], r["role"])} for r in rows])


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


@app.put("/api/teams/<int:tid>")
@ADMIN
def rename_team(tid):
    """團隊更名 (僅管理者)。所有關聯 (專案/成員/分包/通知) 皆以 team_id
    連結,更名不影響任何既有資料,僅顯示名稱改變。"""
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name:
        return jsonify({"error": "團隊名稱為必填"}), 400
    db = get_db()
    row = db.execute("SELECT * FROM teams WHERE id = ?", (tid,)).fetchone()
    if row is None:
        return jsonify({"error": "找不到團隊"}), 404
    dup = db.execute("SELECT 1 FROM teams WHERE name = ? AND id != ?",
                     (name, tid)).fetchone()
    if dup:
        return jsonify({"error": "團隊名稱已存在"}), 400
    db.execute("UPDATE teams SET name = ? WHERE id = ?", (name, tid))
    write_audit(db, "update", "teams", tid,
                {"name": [row["name"], name]})
    db.commit()
    BACKUP.mark_dirty()
    return jsonify({"id": tid, "name": name})


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
    # 改自己时:允许联络资讯与团队归属,但仍挡 role/status (避免自我降权/停用误锁)
    if is_self:
        blocked = [k for k in ("role", "status") if k in fields]
        for k in blocked:
            fields.pop(k)
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
    # debug 預設關;本機要熱重載/除錯設 PM_DEBUG=1 再跑。
    # 絕不可在對外環境開啟 (Werkzeug debugger 會暴露互動式 RCE console)。
    app.run(host="127.0.0.1", port=5000, debug=config.DEBUG)
