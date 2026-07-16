# 專案管理系統 — 單機版

在單一台 Windows 電腦上離線使用,不需要 Google 帳號、不需要網路、不需要架站。

與線上版共用同一份程式碼,差別只在啟動時的模式設定。

有兩種取得方式,擇一即可:

| | 單一 exe (推薦給一般使用者) | 原始碼 + install.bat (開發者) |
| --- | --- | --- |
| 需要安裝 Python | **否** | 是 |
| 需要安裝步驟 | **無,點兩下就用** | 執行一次 install.bat |
| 取得方式 | GitHub Releases 下載 | git clone / 下載 ZIP |
| 更新方式 | 換新的 exe | git pull |

---

## 單一 exe 版

### 使用者怎麼用

1. 把 `專案管理系統.exe` 放到任何**可寫入的資料夾**(桌面、文件、D 槽都可以;
   不建議放 `Program Files` —— 放了也能跑,但資料會改存到
   `%LOCALAPPDATA%\pm-system`,比較難找)。
2. **點兩下**。黑色視窗出現、瀏覽器自動打開系統,就這樣。
   - 第一次執行會自動在 exe 旁邊建立 `data\`(資料庫)與 `backups\`(備份)
   - 之後每次執行都是同一套流程 —— 沒有「安裝」與「啟動」之分
   - 系統已經在執行時再點一次,只會把瀏覽器帶到既有畫面,不會重複啟動
3. **關閉黑色視窗即停止系統**。

第一次執行時 Windows SmartScreen 可能跳出藍色警告(未簽章 exe 的正常現象),
點「**其他資訊**」→「**仍要執行**」即可,之後不會再問。

要備份或搬家:整個資料夾(exe + `data\` + `backups\`)複製走就好。
要更新程式:用新的 exe 覆蓋舊的,`data\` 不受影響。

### 開發者怎麼打包

exe 是建置產物,不進 git。兩種產生方式:

**方式一:GitHub 代打(推薦,開發機不必是 Windows)**

- 手動:GitHub → Actions → **Build standalone exe** → Run workflow,
  完成後在該次執行的 Artifacts 下載
- 發佈:打 tag 推上去(例 `git tag standalone-v1.0 && git push --tags`),
  自動建立 Release 並附上 exe,使用者直接從 Releases 頁面下載

**方式二:本機打包(需要 Windows + Python 3.9+)**

雙擊 `standalone/build/build.bat`,產物在 `standalone/dist/專案管理系統.exe`。

圖示在 `standalone/build/app.ico`,想換造型改 `make_icon.py` 重新產生即可。

---

## 原始碼版:安裝

只需要做一次,而且**只有這一步需要網路**(pip 要下載套件)。

1. 安裝 [Python 3.12](https://www.python.org/downloads/)
   安裝畫面請務必勾選 **Add Python to PATH**。
2. 取得程式:下載本專案的 ZIP 並解壓,或 `git clone`。
3. 進入 `standalone` 資料夾,**雙擊 `install.bat`**。

看到「安裝完成」就結束了。

## 啟動

**雙擊 `start.bat`**,瀏覽器會自動開啟系統。

- 網址是 `http://127.0.0.1:5000`(只綁本機,同一台電腦以外連不進來,不需要開防火牆)
- 若 5000 被其他程式佔用,會自動改用 5001、5002…,實際網址以視窗上顯示的為準
- **關閉黑色視窗即停止服務**。資料已存在硬碟上,不會遺失

從此之後完全離線,拔掉網路線也照常運作。

## 資料放在哪

| 內容 | 路徑 |
| --- | --- |
| 資料庫 | `standalone/data/pm.sqlite` |
| 備份快照 | `standalone/backups/pmdb_*.sqlite` |

這兩個資料夾與程式碼分離,而且不會進 git —— 更新程式不會動到你的資料。

**要備份整個系統,複製 `standalone/data/pm.sqlite` 這個檔案就夠了。**

## 還原備份

系統每次寫入後會自動產生版本化快照(保留最近 30 份),放在 `standalone/backups/`。
檔名的時間戳越大越新。

還原步驟:

1. 關閉系統(關掉黑色視窗)
2. 把要還原的 `standalone/backups/pmdb_YYYYMMDD_HHMMSS.sqlite`
   複製到 `standalone/data/`,更名為 `pm.sqlite` 覆蓋原檔
3. 重新執行 `start.bat`

> 單機版**不會**在開機時自動還原快照 —— 這是刻意的。
> 你硬碟上的 `pm.sqlite` 才是唯一的真實資料,自動還原會覆蓋掉你上次的編輯。
> 快照只在你主動要求時才派上用場。

## 更新程式

```
git pull
```

然後重新執行 `start.bat`。你的資料不受影響(在 `standalone/data/`,不在 git 管轄範圍)。

若更新後啟動失敗,通常是新增了套件,重跑一次 `install.bat` 即可。

---

## 單機版與線上版的差異

| | 線上版 | 單機版 |
| --- | --- | --- |
| Google 帳號登入 | 有 | **無**(開啟即用) |
| 使用者、角色、權限 | 有 | **無**(單人使用) |
| 團隊資料隔離 | 有 | **無** |
| Gmail 通知信 | 會寄送 | **不寄送** |
| Google Drive 備份 | 有 | **改為本機資料夾** |
| 多人同時使用 | 可以 | 否 |
| 需要網路 | 是 | **否**(安裝時除外) |

專案、期程、甘特圖、預算認列、保固、里程碑等核心功能兩邊完全相同。

## 安全性說明

單機版**沒有登入,也沒有權限控管**。任何能打開這台電腦的人都能讀寫全部資料。

這是單機版的設計前提,不是缺陷 —— 它的保護邊界是**作業系統帳號與這台電腦本身**,
而不是應用程式。若資料具敏感性,請確保:

- 電腦本身有設定開機密碼
- `standalone/data/` 不要放在會自動同步到雲端的資料夾(OneDrive、Google Drive 等)
- 需要多人協作與權限控管時,請改用線上版

服務只綁定 `127.0.0.1`,不對區域網路開放,同網段的其他電腦連不進來。

---

## 疑難排解

**雙擊 `install.bat` 閃一下就關掉**
用命令提示字元進入 `standalone` 資料夾後執行 `install.bat`,才看得到錯誤訊息。

**顯示 `Python not found`**
Python 沒裝,或安裝時沒勾「Add Python to PATH」。重新安裝並勾選該選項。

**顯示 `Not installed yet`**
還沒跑過 `install.bat`,或安裝中途失敗。重跑一次 `install.bat`。

**套件安裝失敗**
多半是網路問題。公司網路若有 Proxy,可能需要另外設定 pip 的 proxy。

**瀏覽器沒有自動開啟**
不影響服務。手動開啟視窗上顯示的網址即可。

**想指定 port**
執行前設定環境變數 `PM_PORT`,例如 `set PM_PORT=8000` 後再跑 `start.bat`。
