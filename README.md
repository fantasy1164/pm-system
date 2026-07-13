# 專案期程預算管理系統

以網頁取代手動維護的「專案期程／預算總表 + 甘特圖」。多人即時檢視，管理者授權後才可編輯；每年度一份總表，甘特圖隨資料自動重繪。

- **前端**：單檔 `frontend/index.html`（原生 JS，無框架），部署於 GitHub Pages
- **後端**：Flask + SQLite，部署於 Render
- **持久化**：SQLite 以版本化快照備份至 Google Drive；開機自動還原
- **登入**：Google OAuth + 本系統 JWT，五種角色、欄位級權限

---

## 目錄

- [系統架構](#系統架構)
- [安全模型](#安全模型)
- [權限與角色](#權限與角色)
- [資料表](#資料表)
- [API 一覽](#api-一覽)
- [環境變數](#環境變數)
- [本機開發](#本機開發)
- [部署](#部署)
- [日常維運](#日常維運)

---

## 系統架構

```
 使用者瀏覽器
     │  Google Sign-In → ID token
     ▼
 GitHub Pages (frontend/index.html)  ──fetch(帶 JWT)──►  Render (Flask API)
                                                              │
                                        ┌─────────────────────┼─────────────────────┐
                                        ▼                     ▼                     ▼
                                   SQLite (本機磁碟)     Google Drive           Gmail API
                                   工作資料庫            版本化備份/還原         通知信
```

設計上刻意「後端無狀態化到磁碟以外」：Render 免費方案的磁碟是 **ephemeral**（重新部署或重啟即清空），因此 **真正的資料持久化靠 Google Drive 備份**，不是靠磁碟。開機時先從 Drive 還原最新快照才對外服務；還原失敗一律回 503、絕不以空庫接請求。

免費 instance idle 15 分鐘會休眠，本專案以外部排程每 10 分鐘 ping `/api/health` 保溫，使其趨近常駐。注意這仍是免費方案、磁碟仍為 ephemeral —— 保溫只避免休眠，不等於磁碟持久。

---

## 安全模型

因系統與公司資料暴露於公網，存取控制以「後端為唯一權威」為原則，前端只負責顯示。

- **認證**：前端 Google Sign-In 取得 ID token → 後端向 Google 驗簽並比對 audience → 簽發本系統 JWT（HS256，30 分鐘）。JWT 內僅存 `uid`，角色與授權每次請求都從 DB 重讀，因此管理者調整權限即時生效、無需重新登入。剩餘效期低於 25 分鐘時於回應標頭 `X-New-Token` 滑動換發。
- **傳輸/CORS**：`Access-Control-Allow-Origin` 只回應與 `PM_CORS_ORIGIN` 相符的來源；**未設定時預設不發送該標頭（擋跨域），不會自動全開**。CORS 僅為瀏覽器端防線，真正的存取控制在 JWT。
- **速率限制**：以 Flask-Limiter 依「真實用戶端 IP」限流（透過 ProxyFix 取 `X-Forwarded-For`，避免所有人共用 Render 代理 IP 互相拖累）。預設每 IP 240 次/分；`/api/auth/google`、`/api/auth/register` 另加嚴格上限 10 次/分、40 次/時；`/api/health` 豁免（供保溫 ping 與喚醒輪詢）。
- **注入防護**：所有 SQL 走參數化查詢；可寫入欄位以白名單（`PROJECT_FIELDS`）過濾，未列入的欄位一律忽略。
- **XSS**：前端使用者字串一律經 `textContent` 或 `esc()`（跳脫 `& < > " '`）輸出。
- **越權（IDOR）**：單筆讀取有「團隊即資料牆」；編輯/刪除逐案驗證團隊歸屬；欄位可寫性只依「使用者在該專案所屬團隊的角色」判定，不可套用其他團隊的角色。
- **機密管理**：所有憑證走 Render 環境變數，不進 git。`debug` 模式預設關閉，須明確設 `PM_DEBUG=1` 才開（避免 Werkzeug 互動式 debugger 暴露 RCE）。
- **外部排程端點**：`/api/notify/run` 以 `PM_NOTIFY_TOKEN` 保護，採常數時間比較（`hmac.compare_digest`）。

> **靜態前端的先天限制**：前端是公開靜態檔，任何邏輯都可被繞過。上述所有保護都在後端；前端的隱藏按鈕、欄位遮罩僅為體驗，不是安全邊界。

---

## 權限與角色

- **角色**：`admin`（管理者）、`pm`（專案經理）、`dept_head`（部門主管）、`sales`（業務人員）、`dev`（開發人員）
- **帳號狀態**：`pending`（待核准）→ `active`（啟用）／`disabled`（停用）
- **檢視**：任何 `active` 使用者，但僅能看到自己所屬團隊（含分包給自己的）的專案
- **編輯**：`admin`，或該專案所屬團隊的成員；欄位層級再由權限矩陣（可見／唯讀／可寫）細分
- **使用者／團隊／權限管理**：僅 `admin`
- **首位管理者**：`PM_ADMIN_EMAIL` 指定的信箱首次登入即自動成為 active admin；此設定持續有效（事後補設也會自我修復晉升）
- **一般新使用者**：Google 登入後需補公司信箱與姓名 → 建立 `pending` 帳號 → 寄出申請提醒給本人與管理者 → 管理者核准後啟用
- **跨團隊分包**：主包團隊維護共享欄位（案名、期程、決標金額等）；分包團隊只能維護自己的獨立欄位（里程碑、認列、成員、備註等），共享欄位對分包唯讀

---

## 資料表

| 表 | 用途 |
|---|---|
| `users` | 使用者、角色、狀態、通知信箱 |
| `projects` | 專案主檔（年度、案名、期程、決標金額、保固、所屬團隊…） |
| `teams` / `team_members` | 團隊與成員（成員在團隊內帶角色） |
| `milestones` | 里程碑（含 `team_id`，支援分包獨立） |
| `budget_allocations` | 各年度預估認列 |
| `project_members` | 專案參與人員 |
| `project_subcontracts` | 專案 ↔ 分包團隊關係（可軟性斷開） |
| `project_team_overrides` | 分包團隊對備註／提醒天數／參與者的獨立覆寫 |
| `field_perms` | 角色 × 欄位的權限矩陣 |
| `team_notify_matrix` | 團隊 × 通知類型 × 角色的通知開關 |
| `notifications` | 通知歷史（含去重鍵） |
| `app_settings` | 系統設定（掃描頻率、上次掃描時間…） |
| `audit_log` | 異動紀錄（誰、何時、改了什麼） |

---

## API 一覽

需 JWT 者標註權限層級：**檢視**＝任何 active 使用者、**編輯**＝逐案編輯權、**管理**＝僅 admin。

| 方法 | 路徑 | 權限 | 說明 |
|---|---|---|---|
| GET | `/api/health` | 公開 | 健康檢查（含還原階段） |
| POST | `/api/auth/google` | 公開（限流） | Google 登入，回 JWT 或註冊/待核准狀態 |
| POST | `/api/auth/register` | 公開（限流） | 補資料完成註冊，建 pending 帳號 |
| GET | `/api/auth/config` | 公開 | 前端所需的登入設定（是否啟用、client id） |
| GET | `/api/auth/me` | 檢視 | 目前使用者與可編輯團隊 |
| GET | `/api/years` | 檢視 | 年度清單 |
| GET | `/api/projects` | 檢視 | 某年度專案（依資料牆過濾） |
| GET | `/api/projects/{pid}` | 檢視 | 單筆專案 |
| POST | `/api/projects` | 編輯（全域） | 新增專案 |
| PUT | `/api/projects/{pid}` | 編輯（逐案） | 更新專案 |
| DELETE | `/api/projects/{pid}` | 編輯（逐案） | 軟刪除專案 |
| GET | `/api/projects/{pid}/subcontracts` | 檢視 | 專案的分包關係 |
| PUT | `/api/projects/{pid}/subcontracts` | 編輯（逐案） | 設定分包團隊 |
| DELETE | `/api/projects/{pid}/subcontracts/{team_id}` | 分包 pm/admin | 清除自己團隊的分包足跡（需先軟性斷開） |
| POST | `/api/years/{new_year}/init` | 管理 | 一鍵複製建立新年度 |
| GET/PUT | `/api/perms` | 管理 | 讀取／設定權限矩陣 |
| GET/POST | `/api/teams` | 檢視／管理 | 團隊清單／建立 |
| PUT/DELETE | `/api/teams/{tid}` | 管理 | 更名／刪除團隊 |
| GET | `/api/teams/{tid}/members` | 檢視 | 團隊成員 |
| GET/PUT/DELETE | `/api/users` `/api/users/{uid}` | 管理 | 使用者維護 |
| GET | `/api/notify/types` | 檢視 | 通知類型定義 |
| GET/PUT | `/api/notify/system` | 檢視／管理 | 系統層通知設定 |
| GET/PUT | `/api/notify/matrix/{team_id}` | 檢視／團隊 pm | 團隊通知矩陣 |
| GET/DELETE | `/api/notify/history` | 檢視／管理 | 通知歷史 |
| GET/PUT | `/api/notify/settings` | 檢視／管理 | 掃描頻率等 |
| POST | `/api/notify/run` | Notify token 或 admin | 觸發通知掃描（外部排程用） |
| GET | `/api/audit` | 檢視 | 異動紀錄 |

---

## 環境變數

標「機密」者不得進 git，一律走 Render 環境變數（`render.yaml` 中標 `sync:false`，建立服務時逐一輸入）。

### 認證

| 變數 | 必填 | 說明 |
|---|---|---|
| `PM_AUTH_ENABLED` | 是 | `1` 啟用登入；未設沿用 `X-User` 開發模式 |
| `PM_JWT_SECRET` | 是（機密） | JWT 簽章密鑰，隨機長字串 |
| `GOOGLE_OAUTH_CLIENT_ID` | 是（機密） | 「網頁應用程式」OAuth 用戶端 id（登入用） |
| `PM_ADMIN_EMAIL` | 是（機密） | 首位管理者信箱 |
| `PM_AUTH_TEST_MODE` | 否 | `1` 接受 `test:` 假 token，**僅測試，勿於正式環境設** |

### CORS 與限流

| 變數 | 必填 | 說明 |
|---|---|---|
| `PM_CORS_ORIGIN` | 建議 | 允許的前端來源，逗號分隔。**未設＝不發 Allow-Origin（擋跨域）**；填 `*` 才全開 |

> 速率限制的門檻寫死在程式中（240/分；登入 10/分、40/時），如需調整改 `app.py` 的 `default_limits` 與各端點 `@limiter.limit`。

### 持久化（Drive 備份/還原）

| 變數 | 必填 | 說明 |
|---|---|---|
| `PM_SYNC_ENABLED` | 是 | `1` 啟用還原/備份（本機開發可不設） |
| `PM_DRIVE_MODE` | 是 | `google`（正式）或 `local`（本機模擬） |
| `GOOGLE_CLIENT_ID` | 是（機密） | 「電腦版」OAuth 用戶端 id（Drive/Gmail 用，非登入那顆） |
| `GOOGLE_CLIENT_SECRET` | 是（機密） | 同上密鑰 |
| `GOOGLE_REFRESH_TOKEN` | 是（機密） | 含 `drive.file`（與 `gmail.send`）scope 的 refresh token |
| `PM_DRIVE_FOLDER_ID` | 是（機密） | 備份存放的 Drive 資料夾 id |
| `PM_BOOTSTRAP` | 僅首次 | `1` 允許「完全無備份」時以空庫初始化，**首次備份成功後務必移除** |
| `PM_BACKUP_DEBOUNCE` | 否 | 寫入後延遲幾秒再備份（預設 10） |
| `PM_BACKUP_KEEP` | 否 | 保留備份份數（預設 30） |
| `PM_DB_PATH` | 否 | SQLite 檔案路徑（預設 `backend/pm.sqlite`） |

### 通知（Gmail）

| 變數 | 必填 | 說明 |
|---|---|---|
| `PM_NOTIFY_TOKEN` | 建議（機密） | 保護 `/api/notify/run` 的排程 token |
| `PM_NOTIFY_DRYRUN` | 否 | `1`（預設）只掃描不寄信；接上 Gmail 後改 `0` |
| `PM_MAIL_FROM_ADDR` | 否 | 寄件信箱（授權帳號 email），設了可免向 Gmail profile 查詢 |
| `PM_MAIL_FROM_NAME` | 否 | 寄件者顯示名稱（預設「專案管理系統」） |

### 開發

| 變數 | 說明 |
|---|---|
| `PM_DEBUG` | `1` 開 Flask debug（**僅本機**，對外絕不可開） |
| `PM_LOCAL_DRIVE_DIR` | local 模式下模擬 Drive 的資料夾 |

---

## 本機開發

需求：Python 3.12、Windows（或 macOS/Linux，指令自行調整）。

```powershell
# 後端
cd backend
python -m venv .venv
. .venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python seed_demo.py                 # 灌入示範資料（可選）
python app.py                       # http://127.0.0.1:5000
```

```powershell
# 前端（另開一個終端）
cd frontend
python -m http.server 8080          # http://localhost:8080
```

前端會自動判斷：從 `localhost` / `127.0.0.1` 開啟時連本機後端 5000 埠，其餘連線上 Render（見 `index.html` 內 `PROD_API`）。

本機預設不啟用登入（`PM_AUTH_ENABLED` 未設），以 `X-User` 記錄操作者。若要在本機測登入流程，設 `PM_AUTH_ENABLED=1`、`PM_JWT_SECRET=任意字串`、`PM_AUTH_TEST_MODE=1`，並把前端跨埠來源加進 `PM_CORS_ORIGIN`（如 `http://localhost:8080,http://127.0.0.1:8080`）。要看 Flask 熱重載/除錯畫面再加 `PM_DEBUG=1`。

---

## 部署

順序刻意安排為「後端先活、資料先通、前端最後」，每步都有可核對的成功畫面。詳細逐步見 `DEPLOY.md`；摘要如下。

1. **推上 GitHub**：建 public repo（免費帳號 Pages 需 public；程式碼公開但資料在 token 保護的 API 後）→ `git init/add/commit/push`。
2. **Render 建服務**：New → Blueprint → 指向本 repo，讀 `render.yaml` 自動帶入設定，逐一填入 `sync:false` 的機密變數（`PM_CORS_ORIGIN` 先填 `https://<帳號>.github.io`）。首次額外手動加 `PM_BOOTSTRAP=1`。
3. **驗證後端**：開 `https://<服務>.onrender.com/api/health`，應為 `status: ok`。
4. **首次備份 + 移除 BOOTSTRAP**：登入（你即 `PM_ADMIN_EMAIL`，直接是 admin）→ 建第一筆資料 → 確認 Drive 出現 `pmdb_*.sqlite` → **回 Render 刪掉 `PM_BOOTSTRAP`**（否則哪天 Drive 憑證失效，系統會靜默以空庫重啟）。
5. **前端上線**：`frontend/index.html` 的 `PROD_API` 改成你的 Render 網址（結尾 `/api`）→ repo Settings → Pages → Source 選「GitHub Actions」→ push（`frontend/**` 有異動即自動發布）。
6. **收斂來源**：GCP 網頁 OAuth 用戶端的「已授權 JavaScript 來源」新增 `https://<帳號>.github.io`；確認 Render 的 `PM_CORS_ORIGIN` 為同一網域（不含路徑）。
7. **保溫（可選）**：以 UptimeRobot 之類每 10 分鐘 ping `/api/health`，避免 idle 休眠。注意免費 750 小時/月額度，同帳號別再跑第二個常駐服務。

---

## 日常維運

- **冷啟動**：未保溫時 idle 15 分鐘休眠，喚醒需 30–60 秒（前端會顯示「系統喚醒中」）。
- **還原失敗**：health 顯示 `failed` 時依訊息到 Drive 移除壞檔後重啟；系統不會以空庫上線。
- **備份確認**：編輯後約 10 秒（`PM_BACKUP_DEBOUNCE`）內 Drive 應出現新版本快照，只增不覆蓋，保留最近 `PM_BACKUP_KEEP` 份。
- **Pages 部署失敗**：遇 `Multiple artifacts named "github-pages"` 或 `Deployment failed` 多為 GitHub 端狀況，改用 **Run workflow** 觸發全新 run，並查 [githubstatus.com](https://www.githubstatus.com/)，不要一直 Re-run。
- **新年度**：一月時 admin 按「＋新年度」即可複製建立。
- **通知上線**：確認 Gmail scope 與寄件設定後，把 `PM_NOTIFY_DRYRUN` 改為 `0`，並替 `/api/notify/run` 設好 `PM_NOTIFY_TOKEN` 供外部排程呼叫。

---

## 授權與免責

內部工具，未附開源授權；所有專案與預算資料屬公司機密，請確保部署環境與存取控制符合公司資安規範。
