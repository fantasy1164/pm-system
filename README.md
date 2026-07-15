# 專案期程預算管理系統

一套以網頁取代人工維護「專案期程、預算總表與甘特圖」的輕量化管理系統。

系統支援多人即時檢視、Google 帳號登入、團隊資料隔離、角色與欄位級權限、專案期程追蹤、預算認列、保固管理、通知及異動稽核。

## 專案狀態

本專案為開放原始碼專案，採用 [MIT License](LICENSE) 授權。

原始碼可依 MIT License 自由使用、修改、散布及商業使用。使用或散布本專案時，應保留原著作權與授權聲明。

本 Repository 僅包含系統程式碼、文件與示範結構，不包含正式環境中的：

- 公司專案或預算資料
- SQLite 正式資料庫
- Google OAuth 憑證
- JWT 密鑰
- Google Refresh Token
- Gmail 或 Google Drive 存取憑證
- 其他個人資料或公司機密

## 作者與維護者

原始作者：**John Wang (`fantasy1164`)**

Copyright © 2026 John Wang and contributors.

作者資訊亦放置於前端部署目錄的 [`frontend/humans.txt`](frontend/humans.txt)。第三方套件與服務的著作權、商標及其他權利仍歸各自權利人所有。

## 功能特色

- 年度專案進度總表
- 自動產生甘特圖
- 保固專案總表
- 專案里程碑管理
- 跨年度預算認列
- 團隊與分包關係管理
- Google 帳號登入
- JWT Session 驗證
- 管理者、專案經理、部門主管、業務及開發人員角色
- 欄位級可見、唯讀與可寫權限
- 團隊資料隔離
- 異動稽核紀錄
- 系統及專案通知設定
- Gmail 通知
- Google Drive 版本化資料庫備份
- 深色與淺色主題
- 響應式桌面及行動裝置介面

## 系統架構

```text
使用者瀏覽器
    │
    │ Google Sign-In / Google ID Token
    ▼
GitHub Pages
frontend/index.html
    │
    │ HTTPS / JSON / JWT
    ▼
Render Web Service
Flask API + Gunicorn
    │
    ├── SQLite
    │   └── 系統工作資料庫
    │
    ├── Google Drive API
    │   └── 版本化資料庫備份與開機還原
    │
    └── Gmail API
        └── 系統通知信件
```

正式環境的工作資料庫位於 Render 執行環境，Google Drive 版本化快照則作為資料持久化與還原來源。

系統啟動時會先還原最新可用的資料庫快照。若還原失敗，服務將維持不可用狀態並回傳 HTTP 503，不會靜默建立空白資料庫對外服務。

## 技術架構

| 層級 | 技術 |
|---|---|
| 前端 | HTML、CSS、原生 JavaScript |
| 後端 | Python 3.12、Flask |
| 資料庫 | SQLite |
| 正式 WSGI Server | Gunicorn |
| 登入 | Google Identity Services |
| Google Token 驗證 | google-auth |
| 系統 Session | PyJWT |
| API 限流 | Flask-Limiter |
| HTTP Client | Requests |
| Proxy Header 處理 | Werkzeug ProxyFix |
| 前端部署 | GitHub Pages |
| 後端部署 | Render |
| 備份 | Google Drive API |
| 通知 | Gmail API |

前端未使用 npm、React、Vue、Angular 或其他 JavaScript Framework。

## 開源套件

本專案直接使用下列開源套件：

| 套件 | 用途 | 授權 |
|---|---|---|
| Flask | Web API Framework | BSD-3-Clause |
| Flask-Limiter | API Rate Limiting | MIT |
| Requests | HTTP Client | Apache-2.0 |
| Gunicorn | WSGI HTTP Server | MIT |
| PyJWT | JWT 編碼與驗證 | MIT |
| google-auth | Google ID Token 驗證 | Apache-2.0 |
| Werkzeug | WSGI 工具與 ProxyFix | BSD-3-Clause |

各第三方元件仍由其原作者或權利人持有著作權，並依各自的授權條款使用。

詳見 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## 第三方服務

本系統可與下列外部服務整合：

- Google Identity Services
- Google OAuth
- Google Drive API
- Gmail API
- GitHub Pages
- Render

這些服務不是本專案所有，其使用方式、可用性、商標、費率、配額及服務條款均由各服務提供者決定。

部署者應自行申請帳號、建立憑證，並遵守相應的服務條款及資料保護規範。

## 安全模型

系統以「後端為唯一授權權威」為原則。

前端所做的按鈕隱藏、欄位遮罩及畫面限制，只是使用者介面控制，不是安全邊界。所有資料讀取、修改、刪除及管理操作都必須由後端重新驗證。

主要安全設計包括：

- Google ID Token 驗簽及 Audience 檢查
- 系統 JWT 簽發與到期驗證
- JWT 僅保存使用者識別碼
- 每次請求重新查詢資料庫中的角色與權限
- 團隊資料隔離
- 專案級與欄位級存取控制
- SQL 參數化查詢
- 可寫入欄位白名單
- 使用者字串輸出跳脫
- CORS 來源限制
- API Rate Limiting
- 外部通知端點 Token 保護
- 機密資訊使用環境變數管理
- 正式環境預設停用 Flask Debug Mode
- 資料庫完整性檢查
- 版本化備份及還原失敗保護

本專案不保證部署後自動符合任何特定公司的資安政策、ISO 27001、個資法、GDPR 或其他法令及標準。部署者應依實際環境自行完成安全審查與設定。

## 權限與角色

| 角色 | 說明 |
|---|---|
| `admin` | 系統管理者 |
| `pm` | 專案經理 |
| `dept_head` | 部門主管 |
| `sales` | 業務人員 |
| `dev` | 開發人員 |

帳號狀態：

| 狀態 | 說明 |
|---|---|
| `pending` | 等待管理者核准 |
| `active` | 已啟用 |
| `disabled` | 已停用 |

管理者可以設定不同角色對各類欄位的權限：

- `invisible`：不可見
- `readonly`：唯讀
- `writable`：可編輯

一般使用者只能檢視自己所屬團隊，以及分包給該團隊的專案。

## 主要資料表

| 資料表 | 用途 |
|---|---|
| `users` | 使用者、角色、狀態及通知信箱 |
| `projects` | 專案主檔 |
| `teams` | 團隊資料 |
| `team_members` | 團隊成員及團隊內角色 |
| `milestones` | 專案里程碑 |
| `budget_allocations` | 各年度預估認列 |
| `project_members` | 專案參與人員 |
| `project_subcontracts` | 主包與分包團隊關係 |
| `project_team_overrides` | 分包團隊的獨立欄位資料 |
| `field_perms` | 角色與欄位權限矩陣 |
| `team_notify_matrix` | 團隊通知矩陣 |
| `notifications` | 通知歷史 |
| `app_settings` | 系統設定 |
| `audit_log` | 異動稽核紀錄 |

## 專案結構

```text
pm-system/
├── backend/
│   ├── app.py
│   ├── auth_core.py
│   ├── persistence.py
│   ├── schema.sql
│   ├── seed_demo.py
│   ├── requirements.txt
│   └── wsgi.py
├── frontend/
│   ├── index.html
│   └── humans.txt
├── .github/
│   └── workflows/
├── DEPLOY.md
├── render.yaml
├── THIRD_PARTY_NOTICES.md
├── LICENSE
└── README.md
```

## 本機開發

### 系統需求

- Python 3.12
- Git
- 現代瀏覽器

### 啟動後端

Windows PowerShell：

```powershell
cd backend

python -m venv .venv
. .venv\Scripts\activate

python -m pip install --upgrade pip
pip install -r requirements.txt

python seed_demo.py
python app.py
```

macOS 或 Linux：

```bash
cd backend

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

python seed_demo.py
python app.py
```

後端預設網址：`http://127.0.0.1:5000`

### 啟動前端

另開一個終端：

```powershell
cd frontend
python -m http.server 8080
```

前端網址：`http://localhost:8080`

從 `localhost` 或 `127.0.0.1` 開啟時，前端預設連線至本機後端。從 GitHub Pages 開啟時，前端會使用 `frontend/index.html` 中設定的正式 API 網址。

## 環境變數

所有密鑰、Token、帳號資料及正式環境識別資訊都不得提交至 Git。

### 登入與驗證

| 變數 | 必填 | 說明 |
|---|---|---|
| `PM_AUTH_ENABLED` | 正式環境必填 | 設為 `1` 啟用登入 |
| `PM_JWT_SECRET` | 是，機密 | JWT 簽章密鑰 |
| `GOOGLE_OAUTH_CLIENT_ID` | 是 | Google 網頁應用程式 OAuth Client ID |
| `PM_ADMIN_EMAIL` | 是，機密 | 初始管理者 Google 帳號 |
| `PM_AUTH_TEST_MODE` | 否 | 測試模式，正式環境不得啟用 |

### CORS

| 變數 | 必填 | 說明 |
|---|---|---|
| `PM_CORS_ORIGIN` | 建議必填 | 允許的前端來源，可使用逗號分隔 |

未設定 `PM_CORS_ORIGIN` 時，後端不會自動允許任意跨來源請求。

### Google Drive 備份

| 變數 | 必填 | 說明 |
|---|---|---|
| `PM_SYNC_ENABLED` | 正式環境必填 | 設為 `1` 啟用備份及還原 |
| `PM_DRIVE_MODE` | 是 | `google` 或 `local` |
| `GOOGLE_CLIENT_ID` | Google 模式必填 | Google OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | Google 模式必填，機密 | Google OAuth Client Secret |
| `GOOGLE_REFRESH_TOKEN` | Google 模式必填，機密 | Google OAuth Refresh Token |
| `PM_DRIVE_FOLDER_ID` | Google 模式必填 | Google Drive 備份資料夾 ID |
| `PM_BOOTSTRAP` | 僅首次部署 | 允許無備份時初始化空白資料庫 |
| `PM_BACKUP_DEBOUNCE` | 否 | 寫入後延遲備份秒數 |
| `PM_BACKUP_KEEP` | 否 | 保留備份數量 |
| `PM_DB_PATH` | 否 | SQLite 資料庫路徑 |

`PM_BOOTSTRAP=1` 僅可用於第一次部署。第一次成功備份後應立即移除。

### Gmail 通知

| 變數 | 必填 | 說明 |
|---|---|---|
| `PM_NOTIFY_TOKEN` | 建議必填，機密 | 保護外部通知掃描端點 |
| `PM_NOTIFY_DRYRUN` | 否 | `1` 表示只掃描不寄信 |
| `PM_MAIL_FROM_ADDR` | 否 | 寄件帳號 |
| `PM_MAIL_FROM_NAME` | 否 | 寄件者顯示名稱 |

### 開發環境

| 變數 | 說明 |
|---|---|
| `PM_DEBUG` | 設為 `1` 啟用 Flask Debug，僅限本機 |
| `PM_LOCAL_DRIVE_DIR` | Local Drive 模式使用的本機資料夾 |

## API 概要

### 系統與登入

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/health` | 系統健康與還原狀態 |
| POST | `/api/auth/google` | Google 登入 |
| POST | `/api/auth/register` | 新使用者註冊 |
| GET | `/api/auth/config` | 前端登入設定 |
| GET | `/api/auth/me` | 目前使用者及權限 |

### 專案

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/years` | 年度清單 |
| GET | `/api/projects` | 專案清單 |
| GET | `/api/projects/{id}` | 單一專案 |
| POST | `/api/projects` | 新增專案 |
| PUT | `/api/projects/{id}` | 更新專案 |
| DELETE | `/api/projects/{id}` | 軟刪除專案 |
| POST | `/api/years/{year}/init` | 建立新年度 |

### 團隊、通知與稽核

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET / POST | `/api/teams` | 查詢或建立團隊 |
| GET / PUT / DELETE | `/api/users` | 使用者管理 |
| GET / PUT | `/api/perms` | 欄位權限矩陣 |
| GET / PUT | `/api/projects/{id}/subcontracts` | 分包關係 |
| GET / PUT | `/api/notify/system` | 系統通知設定 |
| GET / PUT | `/api/notify/matrix/{team_id}` | 團隊通知矩陣 |
| GET / DELETE | `/api/notify/history` | 通知歷史 |
| POST | `/api/notify/run` | 執行通知掃描 |
| GET | `/api/audit` | 異動稽核紀錄 |

實際權限及完整輸入輸出格式以後端程式碼為準。

## 部署

詳細部署方式請參閱 [`DEPLOY.md`](DEPLOY.md)。

建議部署順序：

1. 建立 Google OAuth 憑證。
2. 建立 Google Drive 備份資料夾。
3. 將 Repository 連接至 Render Blueprint。
4. 設定 Render 環境變數。
5. 第一次部署時暫時設定 `PM_BOOTSTRAP=1`。
6. 建立第一筆資料並確認 Google Drive 備份成功。
7. 移除 `PM_BOOTSTRAP`。
8. 啟用 GitHub Pages。
9. 設定 Google OAuth Authorized JavaScript Origins。
10. 收斂 `PM_CORS_ORIGIN` 至正式前端網域。

## 資料與隱私

MIT License 適用於本 Repository 中由本專案作者及貢獻者提供的程式碼與文件。

MIT License 不會自動授權：

- 使用者自行輸入的專案資料
- 公司預算及合約資料
- 個人資料
- 商業機密
- OAuth 憑證或 Token
- 第三方商標
- 第三方套件本身的著作權
- Google、GitHub、Render 或其他服務的商標及服務內容

部署者應負責保護資料庫與備份、管理帳號與權限、妥善保存密鑰、定期更新依賴，並評估適用的個資、資安及法令要求。

## 貢獻

歡迎透過 Issue 或 Pull Request 提出錯誤修正、安全改善、文件更新、測試或新功能建議。

除非另有書面約定，提交至本專案的程式碼貢獻，視為同意依本專案相同的 MIT License 提供。貢獻者仍保有其原始貢獻的著作權。

## 安全性問題

請勿在公開 Issue 中張貼真實帳號、Access Token、Refresh Token、OAuth Client Secret、JWT Secret、正式資料庫、公司機密資料或可直接利用的未修補弱點細節。

發現安全性問題時，請透過 GitHub 的私人安全回報機制，或以非公開方式聯絡維護者。

## 授權

本專案採用 MIT License，完整授權內容請參閱 [`LICENSE`](LICENSE)。

## 免責聲明

本軟體依「現況」提供，不附帶任何明示或默示擔保，包括但不限於適售性、特定目的適用性、資料完整性、資料不遺失、服務不中斷、資安合規或法令合規。

作者及貢獻者不對因使用、部署、修改或無法使用本軟體所產生的任何直接或間接損失負責。
