# -*- coding: utf-8 -*-
"""灌入示範資料 (虛構內容,僅供驗證 API 與之後的甘特圖渲染)。

用法:  python seed_demo.py        (資料庫已有資料時會先詢問)
      python seed_demo.py --force (直接清空重灌)
"""
import sys

from app import DB_PATH, init_db
import sqlite3

DEMO_PROJECTS = [
    # (year, status, contract_no, so, name, start, end, participants,
    #  awarded, kickoff, notes, budgets)
    (115, "ongoing", "C115-001", "SO-88101", "示範案A 網管系統維護",
     "2026-01-15", "2026-12-31", "王小明, 李大同", 3200000, "2026-01-20",
     "年度維護案", [(115, 3200000)]),
    (115, "ongoing", "C114-007", "SO-87455", "示範案B FPGA模組開發(3年期)",
     "2025-06-01", "2028-05-31", "陳建宏, 林雅婷, 張志豪", 15000000, "2025-06-10",
     "跨年度案,114年決標", [(114, 3000000), (115, 6000000), (116, 6000000)]),
    (115, "ongoing", "C115-012", "SO-88230", "示範案C 衛星地面站整合",
     "2026-03-01", "2027-02-28", "張志豪, 王小明", 8800000, "2026-03-05",
     "", [(115, 5000000), (116, 3800000)]),
    (115, "not_awarded", "", "", "示範案D 智慧監控平台(投標中)",
     "2026-08-01", "2027-07-31", "李大同", None, None,
     "預計7月開標", [(115, 1500000), (116, 2500000)]),
]


def main():
    init_db()
    db = sqlite3.connect(DB_PATH)
    n = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    if n and "--force" not in sys.argv:
        ans = input(f"資料庫已有 {n} 筆專案,清空重灌? (y/N) ")
        if ans.lower() != "y":
            print("已取消")
            return
    db.executescript(
        "DELETE FROM budget_allocations; DELETE FROM project_editors;"
        "DELETE FROM projects; DELETE FROM audit_log;")
    for i, p in enumerate(DEMO_PROJECTS):
        cur = db.execute(
            "INSERT INTO projects (year, status, contract_no, so_number, name,"
            " start_date, end_date, participants, awarded_amount, kickoff_date,"
            " notes, sort_order) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            p[:11] + (i,))
        for by, amt in p[11]:
            db.execute(
                "INSERT INTO budget_allocations (project_id, year, amount)"
                " VALUES (?,?,?)", (cur.lastrowid, by, amt))
    db.execute(
        "INSERT OR IGNORE INTO users (email, name, role, status, can_edit)"
        " VALUES ('admin@example.com', '管理者(示範)', 'admin', 'active', 1)")
    db.commit()
    print(f"完成:已灌入 {len(DEMO_PROJECTS)} 筆示範專案")


if __name__ == "__main__":
    main()
