# Third-Party Software Notices

本專案使用第三方開源軟體與外部平台服務。

本文件用於整理主要第三方元件資訊，不會取代各元件原始授權條款。各元件的著作權、商標及其他權利仍歸其原作者或權利人所有。

## 掃描範圍

本清單依據下列內容整理：

- `backend/requirements.txt`
- `standalone/requirements.txt`
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
| Gunicorn | Python WSGI HTTP Server（線上版） | MIT |
| Waitress | Python WSGI HTTP Server（單機版） | ZPL 2.1 |
| PyJWT | JSON Web Token 實作 | MIT |
| google-auth | Google 身分驗證 | Apache License 2.0 |
| Werkzeug | WSGI 工具與 ProxyFix | BSD-3-Clause |

Waitress 僅供單機版使用（`standalone/requirements.txt`），線上版不使用。其授權為 Zope Public License 2.1，屬 OSI 認可的寬鬆授權，惟含商標與姓名使用限制條款，散布時應保留其原始授權聲明。

上述套件可能另行安裝各自的間接依賴。重新散布原始碼、執行檔、Container Image 或其他封裝成果前，應重新產生並檢查完整依賴與授權清單。

## 單機版單一 exe 的散布內容

單機版可打包為單一執行檔（`standalone/build/build.bat` 或 GitHub Actions 的 `Build standalone exe`）。這個執行檔與「散布原始碼」不同：它把 Python 直譯器、原生函式庫與全部相依套件一起嵌入並交付給使用者，因此散布它等同散布下列全部元件。

**本執行檔不含任何 copyleft 授權的元件。** 下表以 Windows 版執行檔實際包含的內容為準。

| 元件 | 用途 | 授權 |
|---|---|---|
| CPython 執行期與標準函式庫 | Python 直譯器 | PSF-2.0 |
| PyInstaller bootloader | 單檔 exe 的自解壓啟動器 | GPL-2.0（含 Bootloader Exception，見下） |
| Tcl/Tk | 啟動畫面動畫與控制面板的視窗 | Tcl/Tk License（BSD 類） |
| Flask、Werkzeug、Jinja2、itsdangerous、click、MarkupSafe | Web 框架與相依 | BSD-3-Clause |
| blinker | 訊號機制 | MIT |
| Waitress | WSGI 伺服器 | ZPL 2.1 |
| Flask-Limiter、limits、Deprecated、wrapt、ordered-set | API 速率限制與相依 | MIT／BSD-2-Clause |
| PyJWT | JSON Web Token | MIT |
| packaging、typing-extensions、setuptools | 間接依賴 | Apache-2.0／BSD-2-Clause／PSF-2.0／MIT |
| SQLite、zlib | 由 CPython 內含 | Public Domain／zlib License |

上述皆為寬鬆授權，惟多數（BSD、Apache、ZPL、MIT）仍要求「以二進位形式散布時須隨附著作權聲明」。因此本文件與 `LICENSE` 一併打包進執行檔，使用者可由系統匣圖示的「關於與授權」開啟——單機版是離線的，聲明不能只留在 GitHub 上。

### PyInstaller 的 Bootloader Exception

PyInstaller 本身採 GPL-2.0，但其 `COPYING.txt` 明載 Bootloader Exception：授權人給予「無限制的許可」，允許將編譯後的 bootloader 嵌入其他程式並散布該組合物，且不因此產生任何 GPL 限制。故本執行檔不因使用 PyInstaller 而需採 GPL 授權，亦無提供 bootloader 原始碼之義務。

此為明文例外條款，非解釋空間。（附帶一提：常見替代方案 Nuitka 自 4.x 起改採 **AGPL-3.0**，其執行期會鏈入產出的執行檔，對本專案而言風險反而更高；cx_Freeze 無法產生單一檔案。）

### 刻意排除的元件

下列套件雖列於 `backend/requirements.txt`，但**不會**打包進單機版執行檔：

| 排除的元件 | 授權 | 排除理由 |
|---|---|---|
| certifi | **MPL-2.0（檔案級 copyleft）** | 由 Requests 帶入；單機版不連任何外部服務 |
| Requests、urllib3、idna、charset-normalizer | Apache-2.0／MIT／BSD | 單機版不對外連線 |
| google-auth、pyasn1、pyasn1-modules、six、cryptography（內含 OpenSSL） | Apache-2.0／BSD | 單機版不做 Google 登入、不用 Google Drive |
| Gunicorn | MIT | 單機版使用 Waitress |
| Pillow | MIT-CMU | 僅在重新產生圖示與啟動畫面時使用（`standalone/build/`），不進執行檔 |

排除的安全性來自 `backend/config.py` 末端的無條件覆寫（`AUTH_ENABLED=False`、`DRIVE_MODE="local"`、`NOTIFY_DRYRUN=True`，任何環境變數皆無法變更），且後端對上述套件的 `import` 全部位於函式內（延遲載入），在單機模式永遠不會被執行。

### 系統匣圖示

系統匣圖示以 Python 標準庫的 `ctypes` 直接呼叫 Windows API 實作（`standalone/tray_win32.py`），不使用任何第三方套件。先前版本使用的 pystray 採 LGPL-3.0，會使執行檔沾附 copyleft 義務，已移除。

### 啟動畫面的字型

啟動畫面中的中文為**點陣化後的字形影像**，執行檔與 Repository 均未內含任何字型檔。隨附的 `standalone/build/splash_*.png` 以 Noto Sans CJK TC（SIL Open Font License 1.1）產生；OFL 不限制以字型產生的文件或影像輸出。若在 Windows 上重新執行 `make_splash.py`，將改以系統既有字型（如微軟正黑體）產生，其字形影像的使用仍受該字型授權條款規範。

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
