# 部署指引

本專案有兩種部署形態,共用同一份程式碼:

| | 說明 | 章節 |
|---|---|---|
| 線上版 | GitHub Pages + Render,多人協作 | 第 0–7 節 |
| 單機版 | 單台 Windows 電腦,完全離線 | 「單機版部署」一節 |

差異只在啟動時的 `PM_MODE`,沒有第二份程式碼、沒有第二條分支。

---

## 線上版部署

部署順序刻意安排為「後端先活、資料先通、前端最後」,每步都可驗證。

### 0. 前置整理
- repo 結構:`/frontend`、`/backend`、`/standalone`、`/.github/workflows`、`render.yaml`、`.gitignore`
- 確認任何憑證都沒進 git (機密全部走 Render 環境變數)
- `/standalone` 不影響線上部署:`render.yaml` 的 `rootDir: backend` 讓 Render 看不到它,
  Pages workflow 也只認 `frontend/**`

### 1. 推上 GitHub
GitHub 建新 repo (免費帳號 Pages 需 public) → 本機
`git init` → `git add .` → `git commit` → `git push`。

### 2. Render 建服務 (Blueprint)
Render → New + → Blueprint → 連 GitHub 選本 repo →
會讀 render.yaml 自動帶入設定,逐一填入 sync:false 的機密變數
(PM_CORS_ORIGIN 先填 `https://<你的帳號>.github.io`)。
建立後到服務的 Environment 手動加 **PM_BOOTSTRAP=1** (僅首次)。

### 3. 驗證後端
開 `https://<服務名>.onrender.com/api/health` → 應為
`"status":"ok"`、detail 顯示「首次部署:以空庫初始化」。

### 4. 首次備份 + 移除 BOOTSTRAP
先用瀏覽器 console 或暫時把本機前端指到正式 API
(`localStorage.setItem("pm_api","https://<服務名>.onrender.com/api")`)
→ 登入 (你是 PM_ADMIN_EMAIL,直接是管理者) → 建第一筆專案 →
到 Drive `pm-system-backups` 確認出現 pmdb_*.sqlite →
**回 Render 刪掉 PM_BOOTSTRAP** (服務會自動重啟並改走還原路徑)。

### 5. 前端上線
- `frontend/index.html` 把 `PROD_API` 改成你的 Render 網址 (結尾 /api)
- repo Settings → Pages → Source 選「**GitHub Actions**」
- push → Actions 跑完 → 網址為 `https://<帳號>.github.io/<repo名>/`

### 6. 收斂來源
- GCP「網頁應用程式」OAuth 用戶端 → 已授權 JavaScript 來源
  **新增** `https://<帳號>.github.io` (localhost 那筆留著方便日後開發)
- Render 確認 PM_CORS_ORIGIN = `https://<帳號>.github.io` (不含路徑)

### 7. 驗收清單
- [ ] 手機 + PC 開 Pages 網址 → 冷啟動顯示「系統喚醒中」→ 進登入頁
- [ ] 管理者登入可編輯;新同事登入 → 待核准 → 核准後唯讀 → 授權後可編輯
- [ ] 編輯後 1 分鐘內 Drive 出現新備份
- [ ] Render 手動 Restart 服務 → 資料完好 (走還原路徑)

### 日常維運
- 冷啟動 30–60 秒屬正常;可用 UptimeRobot 每 10 分鐘 ping /api/health 保持喚醒,
  但注意免費額度 750 小時/月,同帳號跑第二個常駐服務會超額
- 還原失敗 (health=failed) → 依訊息到 Drive 移除壞檔重啟
- 新年度:一月時管理者按「＋新年度」即可

---

## 單機版部署

不需要 Google 憑證、Render、GitHub Pages,也不需要設定任何環境變數。

### 安裝
1. 安裝 [Python 3.12](https://www.python.org/downloads/),**務必勾選 Add Python to PATH**
2. 取得原始碼 (`git clone` 或下載 ZIP 解壓)
3. 雙擊 `standalone/install.bat` —— **僅此步驟需要網路** (pip 下載套件)
4. 雙擊 `standalone/start.bat`,瀏覽器會自動開啟

使用者操作說明見 [`standalone/README-standalone.md`](standalone/README-standalone.md)。

### 驗收清單
- [ ] `start.bat` 視窗顯示 `PM_MODE=standalone`,資料庫路徑指向 `standalone\data\pm.sqlite`
- [ ] 瀏覽器自動開啟並顯示專案頁,可新增專案
- [ ] **沒有**「訊息通知」分頁;「團隊管理」分頁內只有團隊,沒有帳號與權限矩陣
- [ ] F12 console:`window.PM_SERVED_BY_FLASK` 為 `1`、`API` 為 `'/api'`
- [ ] 拔掉網路線後所有功能仍正常
- [ ] 關閉視窗再重開,資料完好 (不會被舊快照覆蓋)

### 更新
```
git pull
```
重新執行 `start.bat` 即可。資料在 `standalone/data/`,不受更新影響。
若更新後啟動失敗,通常是新增了套件,重跑一次 `install.bat`。

### 日常維運
- 備份:複製 `standalone/data/pm.sqlite` 即為完整備份
- 系統另會自動產生版本化快照於 `standalone/backups/` (保留最近 30 份)
- 還原:關閉服務 → 將快照複製到 `standalone/data/pm.sqlite` 覆蓋 → 重新啟動
- **單機版開機不會自動還原快照**。硬碟上的資料庫是唯一真實來源,
  自動還原會覆蓋掉使用者上次的編輯

---

## Windows 程式碼簽章

單機版的執行檔目前**沒有簽章**。使用者首次執行會看到 SmartScreen 的藍色警告
(「發行者:不明」、「不是常用下載項目」),需要點「其他資訊 → 仍要執行」。

這不是防毒偵測,而是信譽問題:未簽章 + 下載人數少 = SmartScreen 不認識它。
**任何新的小工具都會遇到,與程式本身是否安全無關。**

### 不簽章時的緩解手段(零成本)

| 做法 | 效果 |
|---|---|
| 發佈 zip 而非裸 exe(已採用) | 下載階段不觸發 exe 的信譽檢查;首次執行仍會警告 |
| 內部檔案伺服器 / USB 發放 | **完全不會出現警告** —— 沒有「從網路下載」的來源標記 |
| 教使用者按「其他資訊 → 仍要執行」 | 每台機器只需一次 |
| 在警告視窗按「回報此為安全的應用程式」 | 對**該次建置**有效;換一版又要重來 |
| 請 IT 加入 Defender 排除或 AppLocker 允許規則 | 公司環境最省事 |

### 要徹底消除警告:簽章

| 選項 | 年費(約) | 效果 | 代價 |
|---|---|---|---|
| **Azure Trusted Signing** | 約 USD 120(月費制) | 有發行者名稱;信譽隨下載量累積 | 需通過 Microsoft 的組織身分驗證;可整合 GitHub Actions |
| **OV 憑證** | USD 200–400 | 有發行者名稱;信譽仍需累積 | 2023 起強制硬體金鑰(USB token/HSM),CI 自動簽章麻煩 |
| **EV 憑證** | USD 400–700 | **SmartScreen 立刻放行** | 硬體金鑰 + 更嚴格的身分審查 |

要點:**只有 EV 憑證能立即消除警告**。OV 與 Trusted Signing 會顯示發行者名稱
(比「不明」好很多),但 SmartScreen 的信譽仍需要累積下載量 —— 對只有數十名使用者的
內部工具,可能永遠累積不起來。

**內部工具的務實建議**:別買憑證,改用內部檔案伺服器發放(沒有網路來源標記,
完全不會有警告),或請 IT 加一條允許規則。憑證留給真的要對外公開發佈時再考慮。

## 維護者須知

新增功能若本質上需要聯網 (呼叫外部 API、寄信、雲端同步),必須:

1. 在 `backend/config.py` 的 standalone 區塊將其強制關閉
2. 在 `backend/test_modes.py` 補上對應檢查
3. 若該功能有 UI 入口,於前端以 `S.standalone` 隱藏

每次改動 `config.py` 後執行:
```
cd backend
.venv\Scripts\python test_modes.py
```
情境 1 (線上正式環境) 有任何一項 FAIL,代表線上部署會受影響。
