# -*- coding: utf-8 -*-
"""以 Gmail API 寄送通知信。

沿用與 persistence.py 相同的憑證 (GOOGLE_CLIENT_ID / SECRET / REFRESH_TOKEN),
但該 refresh token 需含 gmail.send scope (重新授權取得)。
寄件者 = 授權的 Google 帳號本身;收件人 = 各使用者的通知信箱。

環境變數:
  PM_MAIL_FROM_NAME   寄件者顯示名稱 (預設「專案管理系統」)
  PM_NOTIFY_DRYRUN    "1" 時不真的寄 (app.py 端控制,mailer 本身總是真寄)
"""
import base64
import os
import time
from email.mime.text import MIMEText
from email.utils import formataddr

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SCOPE_HINT = "https://www.googleapis.com/auth/gmail.send"


class GmailMailer:
    def __init__(self):
        import requests  # 延遲載入
        self.rq = requests
        self.client_id = os.environ["GOOGLE_CLIENT_ID"]
        self.client_secret = os.environ["GOOGLE_CLIENT_SECRET"]
        self.refresh_token = os.environ["GOOGLE_REFRESH_TOKEN"]
        self.from_name = os.environ.get("PM_MAIL_FROM_NAME", "專案管理系統")
        self._token = None
        self._token_exp = 0
        self._from_addr = None

    def _tok(self):
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = self.rq.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token",
        }, timeout=30)
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._token_exp = time.time() + int(d.get("expires_in", 3600))
        return self._token

    def _from(self):
        """寄件地址 = 授權帳號的 email,向 Gmail profile 查一次後快取"""
        if self._from_addr:
            return self._from_addr
        r = self.rq.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": "Bearer " + self._tok()}, timeout=30)
        r.raise_for_status()
        self._from_addr = r.json()["emailAddress"]
        return self._from_addr

    def send(self, recipients, subject, body):
        """寄一封信給多位收件人 (以 To 併列)。失敗會拋例外。"""
        if not recipients:
            return
        msg = MIMEText(body, "plain", "utf-8")
        msg["To"] = ", ".join(recipients)
        msg["From"] = formataddr((self.from_name, self._from()))
        msg["Subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        r = self.rq.post(SEND_URL, headers={
            "Authorization": "Bearer " + self._tok(),
            "Content-Type": "application/json",
        }, json={"raw": raw}, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"Gmail API {r.status_code}: {r.text[:300]}")
        return r.json().get("id")


_mailer = None


def get_mailer():
    global _mailer
    if _mailer is None:
        _mailer = GmailMailer()
    return _mailer
