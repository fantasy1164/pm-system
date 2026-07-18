-- 專案期程預算管理系統 資料庫 Schema
-- 年度採民國紀年 (例: 115),日期一律 ISO 格式 (YYYY-MM-DD)

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 使用者 (第四階段 OAuth 啟用後,新登入者預設 pending)
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL UNIQUE,
    notify_email TEXT,
    company_name TEXT,                             -- 通知收件信箱 (公司信箱,與登入 gmail 分開)
    name        TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'dev'
                CHECK (role IN ('admin', 'pm', 'dept_head', 'sales', 'dev')),
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'active', 'disabled')),
    can_edit    INTEGER NOT NULL DEFAULT 0,   -- 全域編輯授權 (由管理者開啟)
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 專案總表 (對應試算表的一列)
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    year          INTEGER NOT NULL,               -- 所屬年度 (民國,例 115)
    status        TEXT NOT NULL DEFAULT 'ongoing'
                  CHECK (status IN ('ongoing', 'not_awarded', 'closed')),
                  -- ongoing=進行中, not_awarded=未成案, closed=已結案
    contract_no   TEXT NOT NULL DEFAULT '',       -- 契約號
    part_no       TEXT NOT NULL DEFAULT '',       -- 料號
    so_number     TEXT NOT NULL DEFAULT '',       -- SO number
    name          TEXT NOT NULL,                  -- 案名
    start_date    TEXT,                           -- 履約起 (YYYY-MM-DD)
    end_date      TEXT,                           -- 履約迄 (YYYY-MM-DD)
    participants  TEXT NOT NULL DEFAULT '',       -- 參與人員:未註冊系統者的自由文字 (已註冊成員見 project_members)
    awarded_amount INTEGER,                       -- 決標金額 (NULL=未決標)
    kickoff_date  TEXT,                           -- 啟動會議日期
    warranty_years INTEGER,                       -- 保固年數 (NULL=無/未填)
    notify_days_before INTEGER,                   -- 里程碑到期前 N 天提醒 (NULL=不提醒)
    team_id       INTEGER REFERENCES teams(id),   -- 團隊歸屬 (NULL=未指定)
    contract_scan INTEGER NOT NULL DEFAULT 0,     -- 已收到合約掃檔
    nda_date      TEXT,                           -- 保密文件簽署日期
    nda_scan      INTEGER NOT NULL DEFAULT 0,     -- 保密文件掃描檔
    notes         TEXT NOT NULL DEFAULT '',       -- 備註
    sort_order    INTEGER NOT NULL DEFAULT 0,     -- 表內排序
    copied_from   INTEGER REFERENCES projects(id),-- 年度複製來源 (NULL=非複製)
    deleted       INTEGER NOT NULL DEFAULT 0,     -- 軟刪除
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_projects_year ON projects (year, deleted, sort_order);

-- 團隊
CREATE TABLE IF NOT EXISTS teams (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);

-- 使用者的團隊歸屬與其在該團隊中的角色 (使用者可屬多團隊)
CREATE TABLE IF NOT EXISTS team_members (
    team_id  INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role     TEXT NOT NULL DEFAULT 'dev'
             CHECK (role IN ('pm', 'dept_head', 'sales', 'dev')),
    PRIMARY KEY (team_id, user_id)
);

-- 角色 × 欄位 權限矩陣 (未設定 = writable)
CREATE TABLE IF NOT EXISTS field_perms (
    role   TEXT NOT NULL,
    field  TEXT NOT NULL,
    level  TEXT NOT NULL CHECK (level IN ('invisible', 'readonly', 'writable')),
    PRIMARY KEY (role, field)
);

-- 系統層級設定 (key-value;如通知掃描頻率)
CREATE TABLE IF NOT EXISTS app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- 團隊通知收件矩陣 (每團隊 × 通知類型 × 角色 → 是否發送)
CREATE TABLE IF NOT EXISTS team_notify_matrix (
    team_id  INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    ntype    TEXT NOT NULL,              -- 通知類型,如 milestone_due
    role     TEXT NOT NULL,              -- pm/dept_head/sales/dev
    enabled  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (team_id, ntype, role)
);

-- 已發送通知記錄 (去重 + 歷史)
CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ntype        TEXT NOT NULL,          -- 通知類型
    dedup_key    TEXT NOT NULL UNIQUE,   -- 去重鍵 (同一事件只發一次)
    project_id   INTEGER,
    subject      TEXT NOT NULL,
    recipients   TEXT NOT NULL,          -- 逗號分隔的收件 email
    status       TEXT NOT NULL DEFAULT 'sent',  -- sent/dryrun/failed
    detail       TEXT,                   -- 失敗原因或乾跑備註
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications (created_at DESC);

-- 每位使用者對每則通知的「已確認」狀態 (鈴鐺清單用)。
--
-- 為什麼另開一張表,而不是在 notifications 加一個 read 欄位:一則通知會發給
-- 多個人,已讀是「每人各自」的 —— A 確認了不代表 B 確認了。用 (通知, 使用者)
-- 的關聯表才表達得出來。沒有列 = 未讀;有列 = 已讀 (連同確認時間)。
--
-- 單機版沒有登入,g.user 為 None,一律以 user_id = 0 代表「本機唯一使用者」。
CREATE TABLE IF NOT EXISTS notification_reads (
    notif_id    INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,      -- 0 = 單機版本機使用者
    read_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (notif_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_notif_reads_user ON notification_reads (user_id);

-- 專案里程碑
CREATE TABLE IF NOT EXISTS milestones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    team_id     INTEGER REFERENCES teams(id),      -- 分包:主包/各分包團隊各自的里程碑
    date        TEXT NOT NULL,                    -- YYYY-MM-DD
    name        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ms_project ON milestones (project_id, date);

-- 專案參與成員 (勾選自團隊的已註冊成員 + 各自備註;
-- 未註冊系統的參與者仍存在 projects.participants 自由文字欄位)
CREATE TABLE IF NOT EXISTS project_members (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    team_id     INTEGER,                          -- 分包:主包/各分包團隊各自的參與成員
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, team_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_pm_project ON project_members (project_id);

-- 各年度預估認列 (一個跨年度專案會有多筆)
CREATE TABLE IF NOT EXISTS budget_allocations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    team_id     INTEGER,                          -- 分包:主包/各分包團隊各自的認列
    year        INTEGER NOT NULL,                 -- 認列年度 (民國)
    amount      INTEGER NOT NULL DEFAULT 0,       -- 預估認列金額
    UNIQUE (project_id, team_id, year)
);

-- 單一專案的編輯授權 (管理者指派;全域 can_edit=1 者不需逐案授權)
CREATE TABLE IF NOT EXISTS project_editors (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, user_id)
);

-- 跨團隊分包關係 (方向A:主包專案分包給多個團隊)
-- active=1 生效;=0 軟性斷開 (保留分包團隊已填資料,可再接回)
CREATE TABLE IF NOT EXISTS project_subcontracts (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    team_id     INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (project_id, team_id)
);
CREATE INDEX IF NOT EXISTS idx_subc_project ON project_subcontracts (project_id);
CREATE INDEX IF NOT EXISTS idx_subc_team ON project_subcontracts (team_id);

-- 分包團隊在專案上的獨立單值欄位 (決標金額、備註;主包的仍存 projects 表)
CREATE TABLE IF NOT EXISTS project_team_overrides (
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    team_id        INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    awarded_amount INTEGER,
    notes          TEXT NOT NULL DEFAULT '',
    notify_days_before INTEGER,       -- 分包獨立的里程碑提醒天數
    participants   TEXT NOT NULL DEFAULT '',   -- 分包獨立的其他參與者
    PRIMARY KEY (project_id, team_id)
);

-- 異動紀錄
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT NOT NULL,                    -- 操作者 email (登入功能上線前為 local-dev)
    action      TEXT NOT NULL,                    -- create / update / delete
    entity      TEXT NOT NULL,                    -- projects / users / ...
    entity_id   INTEGER NOT NULL,
    changes     TEXT NOT NULL DEFAULT '{}',       -- JSON: {欄位: [舊值, 新值]}
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log (entity, entity_id);
