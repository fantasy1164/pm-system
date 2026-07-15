# Third-Party Software Notices

本專案使用第三方開源軟體與外部平台服務。

本文件用於整理主要第三方元件資訊，不會取代各元件原始授權條款。各元件的著作權、商標及其他權利仍歸其原作者或權利人所有。

## 掃描範圍

本清單依據下列內容整理：

- `backend/requirements.txt`
- Python 原始碼中的直接 `import`
- `frontend/index.html` 載入的外部服務
- `render.yaml` 部署設定

目前專案未提供完整固定版本的依賴鎖定檔，因此下列清單以直接依賴為主。實際部署環境中的間接依賴與版本，應以安裝結果、Lock File 或 SBOM 為準。

## Python 直接依賴

| 元件 | 用途 | 授權 |
|---|---|---|
| Flask | Web Application Framework | BSD-3-Clause |
| Flask-Limiter | API Rate Limiting | MIT |
| Requests | HTTP Client | Apache License 2.0 |
| Gunicorn | Python WSGI HTTP Server | MIT |
| PyJWT | JSON Web Token 實作 | MIT |
| google-auth | Google 身分驗證 | Apache License 2.0 |
| Werkzeug | WSGI 工具與 ProxyFix | BSD-3-Clause |

上述套件可能另行安裝各自的間接依賴。重新散布原始碼、執行檔、Container Image 或其他封裝成果前，應重新產生並檢查完整依賴與授權清單。

## 外部服務

本系統可與下列第三方服務整合：

| 服務 | 用途 |
|---|---|
| Google Identity Services | 使用者登入 |
| Google OAuth | 授權與 Token 管理 |
| Google Drive API | SQLite 備份與還原 |
| Gmail API | 系統通知信件 |
| GitHub Pages | 前端靜態網站部署 |
| Render | 後端 Web Service 部署 |

上述外部服務不是本專案的一部分，也不因本專案採 MIT License 而改變其服務條款、費率、配額、商標權或其他使用限制。

## 第三方權利聲明

本專案作者不主張擁有第三方套件、API、平台、商標或服務的權利。

使用者重新散布本專案或其衍生成果時，應自行：

1. 確認實際包含的元件及版本。
2. 保留各第三方元件要求的著作權與授權聲明。
3. 隨附適用的第三方授權內容。
4. 遵守各服務提供者的服務條款。
5. 重新執行依賴、弱點及授權掃描。

## 專案本身的授權

本專案自行開發的程式碼與文件採 MIT License。

完整條款請參閱 Repository 根目錄的 [`LICENSE`](LICENSE)。
