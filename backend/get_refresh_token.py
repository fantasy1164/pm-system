# -*- coding: utf-8 -*-
"""一次性工具:在你自己的電腦上執行,取得 Google Drive 的 refresh token。

前置作業 (GCP Console,一次性):
  1. 建立專案 → 啟用「Google Drive API」
  2. 「OAuth 同意畫面」→ External → 填 App 名稱 → 發布狀態切成「正式版」
     (維持「測試中」的話 refresh token 七天就會過期!)
  3. 「憑證」→ 建立 OAuth 用戶端 ID → 應用程式類型選「電腦版應用程式」
  4. 記下 client_id 與 client_secret

用法:
  python get_refresh_token.py <client_id> <client_secret>

流程:自動開瀏覽器 → 用你的 Google 帳號授權 (scope 僅 drive.file,
只能存取本 App 自己建立的檔案) → 本機接回授權碼 → 換取並印出 refresh token。
把印出的三個值設到 Render 環境變數即可。
"""
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

PORT = 8765
REDIRECT = f"http://localhost:{PORT}"
SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

code_holder = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code_holder["code"] = (qs.get("code") or [None])[0]
        code_holder["state"] = (qs.get("state") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h3>授權完成,請回到終端機查看結果。此頁可關閉。</h3>"
                         .encode("utf-8"))

    def log_message(self, *a):
        pass


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    client_id, client_secret = sys.argv[1], sys.argv[2]
    state = secrets.token_urlsafe(16)

    srv = http.server.HTTPServer(("localhost", PORT), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT,
        "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
    })
    print("開啟瀏覽器進行授權…\n若未自動開啟,請手動貼上:\n" + url + "\n")
    webbrowser.open(url)

    import time
    while "code" not in code_holder:
        time.sleep(0.3)
    if code_holder.get("state") != state:
        print("state 不符,可能遭到攔截,請重試")
        sys.exit(1)

    data = urllib.parse.urlencode({
        "code": code_holder["code"], "client_id": client_id,
        "client_secret": client_secret, "redirect_uri": REDIRECT,
        "grant_type": "authorization_code",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data)) as r:
        tok = json.loads(r.read())

    rt = tok.get("refresh_token")
    if not rt:
        print("沒有拿到 refresh_token (通常是先前已授權過)。"
              "請到 https://myaccount.google.com/permissions 移除本 App 授權後重跑。")
        sys.exit(1)

    # 先把最重要的憑證印出來,後面建資料夾失敗也不會遺失
    print("已取得憑證 (先記下,遺失需重新授權):\n")
    print(f"  GOOGLE_CLIENT_ID={client_id}")
    print(f"  GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"  GOOGLE_REFRESH_TOKEN={rt}\n")

    # 以剛取得的 access token 建立備份資料夾
    # (drive.file scope 下,App 自建的資料夾才保證可見可寫)
    meta = json.dumps({"name": "pm-system-backups",
                       "mimeType": "application/vnd.google-apps.folder"}).encode()
    req = urllib.request.Request(
        "https://www.googleapis.com/drive/v3/files",
        data=meta, method="POST",
        headers={"Authorization": "Bearer " + tok["access_token"],
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            folder = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        print(f"建立備份資料夾失敗 (HTTP {e.code})。Google 回覆:\n{detail}\n")
        print("最常見原因:Google Drive API 未啟用、或啟用在別的 GCP 專案。")
        print("到 Console →「API 和服務」→「程式庫」啟用 Google Drive API,")
        print("等 1~5 分鐘生效。上面三個憑證仍有效,重跑本工具即可")
        print("(重跑會再走一次瀏覽器授權並產生新的 refresh token,用最新的那組)。")
        sys.exit(1)

    print("成功!已在你的 Drive 建立「pm-system-backups」資料夾。")
    print("第四個環境變數:\n")
    print(f"  PM_DRIVE_FOLDER_ID={folder['id']}")


if __name__ == "__main__":
    main()
