# 專案期程預算管理系統 (第四階段)

Flask + SQLite 後端 + 單檔前端 (總表 + 自動甘特圖)。
已包含 Drive 還原/備份(③)、Google 登入 + JWT 權限(④)。尚未包含:部署(⑤)。

## 前端啟動

```
cd frontend
python -m http.server 8000     # 然後開 http://localhost:8000
```

後端預設連 http://127.0.0.1:5000/api,若後端位址不同,
在瀏覽器 console 執行 `localStorage.setItem("pm_api", "https://你的位址/api")` 後重整。

前端功能:年度頁籤切換、總表檢視、SVG 甘特圖 (月刻度/季度底紋/今天線/縮放/
啟動會議菱形標記/未成案虛線)、點列或長條開啟編輯視窗、新增/刪除專案、
「＋新年度」一鍵複製跨年度專案、操作者名稱記入異動紀錄。
PC 與手機皆可操作 (手機上甘特圖左欄固定、時間軸橫向捲動)。

## 本機啟動 (Windows)

```
cd backend
pip install -r requirements.txt
python seed_demo.py        # 灌 4 筆虛構示範資料
python app.py              # http://127.0.0.1:5000
```

## 驗證步驟 (PowerShell 或 cmd 皆可,Win10+ 內建 curl)

```
curl http://127.0.0.1:5000/api/health
curl "http://127.0.0.1:5000/api/projects?year=115"
curl -X POST http://127.0.0.1:5000/api/projects -H "Content-Type: application/json" -H "X-User: you@example.com" -d "{\"year\":115,\"name\":\"測試案\",\"start_date\":\"2026-08-01\",\"end_date\":\"2026-10-31\",\"budgets\":[{\"year\":115,\"amount\":500000}]}"
curl -X PUT  http://127.0.0.1:5000/api/projects/5 -H "Content-Type: application/json" -d "{\"end_date\":\"2026-12-31\"}"
curl -X DELETE http://127.0.0.1:5000/api/projects/5
curl http://127.0.0.1:5000/api/audit
curl -X POST http://127.0.0.1:5000/api/years/116/init
```

## API 一覽

| Method | Path | 說明 |
|---|---|---|
| GET | /api/health | 健康檢查 (未來冷啟動還原完成後才回 ok) |
| GET | /api/years | 現有年度清單 |
| GET | /api/projects?year=115 | 專案列表 (含 budgets、自動計算 duration_days) |
| GET | /api/projects/:id | 單筆專案 |
| POST | /api/projects | 新增 (name、year 必填;budgets 為 [{year, amount}]) |
| PUT | /api/projects/:id | 部分更新 (帶 budgets 則整組取代) |
| DELETE | /api/projects/:id | 軟刪除 |
| POST | /api/years/:y/init | 建立新年度:複製跨年度進行中專案 (冪等,重打不重複) |
| GET | /api/audit?limit=50 | 異動紀錄 |

## 設計備忘

- 年度用民國紀年 (115),日期用 ISO (YYYY-MM-DD),民國/西元轉換只在顯示層做
- `X-User` header 僅為第一階段 audit 用途,第四階段以 JWT 取代並強制驗證
- 日期驗證以「合併後的值」檢查起迄順序 (單改一邊也擋得住)
- CORS 目前全開 (`PM_CORS_ORIGIN` 環境變數可鎖定,上線時設為 GitHub Pages 網域)
- 資料庫路徑可用 `PM_DB_PATH` 覆寫 (第三階段 Drive 還原機制會用到)
- projects.status: ongoing=進行中 / not_awarded=未成案 / closed=已結案
- 年度切換採「複製快照」:新年度為獨立複本 (copied_from 記錄來源),修改不影響上一年度定稿;同一來源不會重複複製
- 既有資料庫升級:schema 新增 copied_from 欄位,示範階段直接刪除 pm.sqlite 重跑 seed 即可

## 已知修正紀錄

- X-User header 中文名稱以 encodeURIComponent 編碼傳送 (HTTP header 僅容許
  ISO-8859-1),後端 unquote 解碼——否則 fetch 會直接 TypeError

## 持久化機制 (第三階段,persistence.py)

Render 免費方案磁碟是 ephemeral 的,SQLite 以「開機還原 + 寫入觸發備份」持久化到
私人 Google Drive。保命規則:

1. 開機:下載最新備份 → PRAGMA integrity_check + 核心資料表驗證 → 通過才服務;
   還原失敗一律 503,**絕不 fallback 成空資料庫** (空庫上線會在下次備份時洗掉 Drive 資料)
2. 備份:編輯 commit 後標記 pending,debounce 10 秒後以 VACUUM INTO 產生一致性快照
   上傳;檔名帶時間戳版本化不覆蓋,保留最近 30 份自動清舊
3. SIGTERM (spin down / redeploy):若有未備份變更,強制 flush 一次再退出
4. pending 狀態與喚醒事件分離,debounce 睡眠期間收到 SIGTERM 也不會遺失變更

### 環境變數

| 變數 | 說明 | 預設 |
|---|---|---|
| PM_SYNC_ENABLED | =1 啟用還原/備份 | 0 (本機開發) |
| PM_DRIVE_MODE | local (本機資料夾模擬) / google | local |
| PM_LOCAL_DRIVE_DIR | local 模式的模擬資料夾 | /tmp/pm-fake-drive |
| PM_BOOTSTRAP | =1 允許無任何備份時以空庫初始化 (僅首次部署,之後移除) | - |
| PM_BACKUP_DEBOUNCE / PM_BACKUP_KEEP | 備份延遲秒數 / 保留份數 | 10 / 30 |
| GOOGLE_CLIENT_ID / SECRET / REFRESH_TOKEN, PM_DRIVE_FOLDER_ID | google 模式憑證 | - |

### Google Drive 憑證 (一次性,在自己電腦執行)

1. GCP Console:建專案 → 啟用 Drive API → OAuth 同意畫面 (External,發布為**正式版**,
   測試中狀態的 refresh token 七天過期) → 建「電腦版」OAuth 用戶端
2. `python get_refresh_token.py <client_id> <client_secret>` → 瀏覽器授權
   (scope 僅 drive.file,App 只碰得到自己建立的檔案) → 自動建立備份資料夾並印出
   四個環境變數,填入 Render 即可

### 首次部署順序

設 `PM_SYNC_ENABLED=1` + `PM_DRIVE_MODE=google` + 四個憑證 + `PM_BOOTSTRAP=1` →
啟動後建立第一筆資料觸發首次備份 → 確認 Drive 資料夾出現 pmdb_*.sqlite →
**移除 PM_BOOTSTRAP** (之後任何「找不到備份」都應該視為異常擋下)

### 還原失敗的人工處置

health 會顯示哪個備份檔驗證失敗。確認後到 Drive 把壞檔移走,重啟服務即以前一版還原。
系統刻意不自動回退舊版,避免默默遺失近期編輯。

### 啟動方式

- 本機開發:`python app.py` (同步未啟用,直接用本機 pm.sqlite)
- 本機驗證持久化:`PM_SYNC_ENABLED=1 PM_DRIVE_MODE=local PM_BOOTSTRAP=1 python app.py`,
  備份會出現在 /tmp/pm-fake-drive
- 正式 (Render):`gunicorn -w 1 --threads 8 wsgi:app` —— **必須單一 worker**,
  多 process 會同時寫 SQLite 並重複觸發備份

## 登入與權限 (第四階段,auth_core.py)

流程:前端 Google Sign-In → 後端驗 ID token → 查/建 users →
active 者簽發本系統 JWT (30 分鐘) → 之後每個 API 請求帶 Bearer token,
通過驗證即滑動續期 (剩餘 <25 分時回應 X-New-Token,前端自動替換)。
閒置超過 30 分鐘 token 過期 → 401 → 前端導回登入頁 (前端另有 30 分鐘閒置計時)。

權限:
- 檢視:所有 active 使用者。首次登入者自動建立為 pending,畫面顯示等待核准
- 編輯:admin、can_edit=1 (全域)、或 project_editors 逐案授權 (編輯視窗內由 admin 勾選)
- 使用者管理頁:僅 admin,可核准/停用/刪除、改角色、開關全域編輯
- JWT 只放 uid,角色每次請求讀 DB → 改權限即刻生效
- 管理者不可修改/刪除自己 (防誤鎖)

### 環境變數

| 變數 | 說明 |
|---|---|
| PM_AUTH_ENABLED | =1 啟用登入 (預設 0 = 開發模式沿用 X-User) |
| PM_JWT_SECRET | JWT 簽章密鑰,啟用時必填 (隨機長字串,例 `python -c "import secrets;print(secrets.token_urlsafe(48))"`) |
| GOOGLE_OAUTH_CLIENT_ID | 「網頁應用程式」OAuth 用戶端 id (登入用) |
| PM_ADMIN_EMAIL | 第一位管理者 email,首次登入自動 active admin |
| PM_AUTH_TEST_MODE | =1 接受假 token,僅供整合測試,**正式環境嚴禁設定** |

### 建立登入用 OAuth 用戶端 (與 Drive 備份的「電腦版」用戶端不同顆!)

同一個 GCP 專案 →「憑證」→ 建立 OAuth 用戶端 → 類型選「**網頁應用程式**」:
- 已授權的 JavaScript 來源:`http://localhost:8000` (本機) 與
  `https://<帳號>.github.io` (上線後補)
- 不需要重新導向 URI (前端用 Google Identity Services 彈窗流程)
把用戶端 id 設為 GOOGLE_OAUTH_CLIENT_ID。client secret 用不到。

### 本機驗證登入流程

```
$env:PM_AUTH_ENABLED = "1"
$env:PM_JWT_SECRET   = "隨便一串測試密鑰"
$env:GOOGLE_OAUTH_CLIENT_ID = "<網頁應用程式用戶端id>"
$env:PM_ADMIN_EMAIL  = "你的gmail"
python app.py
```
前端照常 `python -m http.server 8000` → 開 http://localhost:8000 →
出現 Google 登入鈕 → 用 PM_ADMIN_EMAIL 的帳號登入即為管理者。
用第二個 Google 帳號登入會看到「等待核准」,回管理者身分的「使用者」頁核准它。
