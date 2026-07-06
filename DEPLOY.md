# 部署指引 (第五階段)

部署順序刻意安排為「後端先活、資料先通、前端最後」,每步都可驗證。

## 0. 前置整理
- repo 結構:`/frontend`、`/backend`、`/.github/workflows`、`render.yaml`、`.gitignore`
- 確認任何憑證都沒進 git (機密全部走 Render 環境變數)

## 1. 推上 GitHub
GitHub 建新 repo (免費帳號 Pages 需 public) → 本機
`git init` → `git add .` → `git commit` → `git push`。

## 2. Render 建服務 (Blueprint)
Render → New + → Blueprint → 連 GitHub 選本 repo →
會讀 render.yaml 自動帶入設定,逐一填入 sync:false 的機密變數
(PM_CORS_ORIGIN 先填 `https://<你的帳號>.github.io`)。
建立後到服務的 Environment 手動加 **PM_BOOTSTRAP=1** (僅首次)。

## 3. 驗證後端
開 `https://<服務名>.onrender.com/api/health` → 應為
`"status":"ok"`、detail 顯示「首次部署:以空庫初始化」。

## 4. 首次備份 + 移除 BOOTSTRAP
先用瀏覽器 console 或暫時把本機前端指到正式 API
(`localStorage.setItem("pm_api","https://<服務名>.onrender.com/api")`)
→ 登入 (你是 PM_ADMIN_EMAIL,直接是管理者) → 建第一筆專案 →
到 Drive `pm-system-backups` 確認出現 pmdb_*.sqlite →
**回 Render 刪掉 PM_BOOTSTRAP** (服務會自動重啟並改走還原路徑)。

## 5. 前端上線
- `frontend/index.html` 把 `PROD_API` 改成你的 Render 網址 (結尾 /api)
- repo Settings → Pages → Source 選「**GitHub Actions**」
- push → Actions 跑完 → 網址為 `https://<帳號>.github.io/<repo名>/`

## 6. 收斂來源
- GCP「網頁應用程式」OAuth 用戶端 → 已授權 JavaScript 來源
  **新增** `https://<帳號>.github.io` (localhost 那筆留著方便日後開發)
- Render 確認 PM_CORS_ORIGIN = `https://<帳號>.github.io` (不含路徑)

## 7. 驗收清單
- [ ] 手機 + PC 開 Pages 網址 → 冷啟動顯示「系統喚醒中」→ 進登入頁
- [ ] 管理者登入可編輯;新同事登入 → 待核准 → 核准後唯讀 → 授權後可編輯
- [ ] 編輯後 1 分鐘內 Drive 出現新備份
- [ ] Render 手動 Restart 服務 → 資料完好 (走還原路徑)

## 日常維運
- 冷啟動 30–60 秒屬正常;可用 UptimeRobot 每 10 分鐘 ping /api/health 保持喚醒,
  但注意免費額度 750 小時/月,同帳號跑第二個常駐服務會超額
- 還原失敗 (health=failed) → 依訊息到 Drive 移除壞檔重啟
- 新年度:一月時管理者按「＋新年度」即可
