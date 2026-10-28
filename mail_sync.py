# -*- coding: utf-8 -*-
"""邮聊 - 邮件收发：IMAP 实时收取（IDLE 优先，失败自动降级轮询）+ SMTP 发送。"""
import email
import imaplib
import os
import smtplib
import socket
import ssl
import threading
import time
from email import policy
from email.header import decode_header, make_header
from email.mime.text import MIMEText
from email.utils import parseaddr, parsedate_to_datetime

import db
import oauth_ms

IDLE_TIMEOUT = 25 * 60        # IDLE 单次最长等待
MAX_BODY_CHARS = 12000
FALLBACK_AFTER_ERRORS = 3     # IDLE 连续失败几次后自动改成轮询


def _decode_str(s):
    if not s:
        return ""
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return s


def html_to_text(html):
    import re
    import html as htmllib
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    txt = re.sub(r"(?i)<br\s*/?>", "\n", txt)
    txt = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", txt)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = htmllib.unescape(txt)
    txt = re.sub(r"[ \t\u00a0]+", " ", txt)
    txt = re.sub(r"\n\s*\n\s*\n+", "\n\n", txt)
    return txt.strip()


def parse_message(raw_bytes):
    """把一封原始邮件解析成邮聊的消息字典。"""
    msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    frm_name, frm_addr = parseaddr(_decode_str(msg.get("From", "")))
    to_name, to_addr = parseaddr(_decode_str(msg.get("To", "")))
    subject = _decode_str(msg.get("Subject", ""))

    ts = time.time()
    date_hdr = msg.get("Date")
    if date_hdr:
        try:
            dt = parsedate_to_datetime(date_hdr)
            if dt.tzinfo is None:
                dt = dt.astimezone()
            ts = dt.timestamp()
        except Exception:
            pass

    body, body_html = "", ""
    try:
        plain = msg.get_body(preferencelist=("plain",))
        if plain:
            body = plain.get_content()
    except Exception:
        pass
    try:
        html_part = msg.get_body(preferencelist=("html",))
        if html_part:
            body_html = html_part.get_content()
    except Exception:
        pass
    if not body and body_html:
        body = html_to_text(body_html)
    if not body and not body_html:
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body = part.get_content()
                    break
                except Exception:
                    continue
    body = (body or "").strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n…(正文过长已截断)"

    msg_id = msg.get("Message-ID") or msg.get("Message-Id") or f"gen-{int(ts*1000)}-{frm_addr}"
    return {
        "msg_id": msg_id.strip(),
        "email": (frm_addr or to_addr or "unknown").lower(),
        "from_name": frm_name,
        "from_addr": (frm_addr or "").lower(),
        "to_addr": (to_addr or "").lower(),
        "direction": "in",
        "subject": subject,
        "body": body,
        "ts": ts,
        "read": 0,
        "meta": __import__("json").dumps({
            "from_name": frm_name, "from_addr": (frm_addr or "").lower(),
            "to": (to_addr or "").lower(), "in_reply_to": msg.get("In-Reply-To") or "",
        }),
    }


# ---------- 连接 ----------
def _get_token(cfg):
    """按登录方式拿到认证凭据：password 直接返回密码，oauth2 返回（自动续期的）令牌。"""
    if (cfg.get("auth_method") or "password") == "oauth2":
        token = oauth_ms.ensure_access_token(cfg.get("oauth") or {})
        return ("oauth2", token)
    return ("password", cfg.get("password") or "")


def connect_imap(cfg):
    host, port = cfg["imap_host"], int(cfg.get("imap_port") or 993)
    ctx = ssl.create_default_context()
    imap = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=30)
    method, secret = _get_token(cfg)
    if method == "oauth2":
        # 注意：imaplib 会自动 base64 —— 这里必须给“原始串”，
        # 预先编码会导致双重 base64，微软报 BAD Command Argument Error. 12
        raw = oauth_ms.xoauth2_raw(cfg["email"], secret)
        imap.authenticate("XOAUTH2", lambda _: raw)
    else:
        imap.login(cfg["email"], secret)
    imap.select("INBOX")
    return imap


def test_connection(cfg):
    """返回 (ok, 中文说明)。"""
    try:
        imap = connect_imap(cfg)
        try:
            typ, data = imap.status("INBOX", "(MESSAGES)")
            info = data[0].decode("utf-8", "ignore") if data else ""
        except Exception:
            info = ""
        try:
            imap.logout()
        except Exception:
            pass
        way = "学校账号登录(OAuth)" if (cfg.get("auth_method") or "") == "oauth2" else "密码登录"
        return True, f"✅ 连接成功（{way}）！收件箱 {info or '已连接'}"
    except oauth_ms.OAuthError as e:
        return False, f"❌ {e}"
    except imaplib.IMAP4.error as e:
        return False, (f"❌ 登录被拒绝：{e}。\n"
                       "· 个人邮箱：多半是「授权码/应用密码」不对（不是登录密码）。\n"
                       "· 学校/公司邮箱（Microsoft 365）：微软已禁止密码登录，请把「登录方式」改成"
                       "「学校账号登录 OAuth」再试。")
    except (socket.gaierror, socket.timeout, ConnectionError) as e:
        return False, f"❌ 连不上邮箱服务器：{e}。请检查收件服务器地址和网络。"
    except Exception as e:
        return False, f"❌ 连接失败：{e}"


# ---------- IDLE ----------
def _idle_wait(imap, timeout):
    """进入 IDLE 等待新邮件；有动静或超时就退出。失败会抛异常，由上层降级为轮询。"""
    tag = imap._new_tag()
    imap.send(tag + b" IDLE\r\n")
    # 等服务器回 + idling
    buf = b""
    imap.sock.settimeout(30)
    while b"+ idling" not in buf.lower():
        chunk = imap.sock.recv(4096)
        if not chunk:
            raise imaplib.IMAP4.abort("IDLE 连接被关闭")
        buf += chunk
        if b"\r\n" in buf and b"+" not in buf.split(b"\r\n")[0]:
            raise imaplib.IMAP4.abort("服务器不支持 IDLE")

    imap.sock.settimeout(timeout)
    data = b""
    try:
        while True:
            chunk = imap.sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"EXISTS" in data or b"EXPUNGE" in data:
                break
    except socket.timeout:
        pass  # 定时唤醒，正常

    imap.sock.settimeout(30)
    imap.send(b"DONE\r\n")
    # 把 IDLE 的收尾响应读干净，避免 imaplib 状态错乱
    tail = b""
    while tag not in tail:
        chunk = imap.sock.recv(4096)
        if not chunk:
            raise imaplib.IMAP4.abort("IDLE 收尾失败")
        tail += chunk
    return data


# ---------- 同步线程 ----------
class MailSync(threading.Thread):
    """后台线程：配置好邮箱后自动收信，收到新邮件通过 callback 通知（SSE 推给界面）。"""

    def __init__(self, get_cfg, on_new_message, on_status):
        super().__init__(daemon=True, name="mail-sync")
        self.get_cfg = get_cfg
        self.on_new_message = on_new_message
        self.on_status = on_status
        self.stop_event = threading.Event()
        self.mode = "未启动"
        self._idle_errors = 0

    # -- 工具 --
    def _set_status(self, status):
        self.on_status(status)

    def _fetch_new(self, imap, cfg):
        last_uid = int(db.kv_get("last_uid", "0") or 0)
        if last_uid == 0:
            # 首次：只抓最近 N 封，避免一上来拉几年的邮件
            typ, data = imap.uid("search", None, "ALL")
            uids = (data[0] or b"").split()
            take = uids[-int(cfg.get("initial_fetch") or 20):]
        else:
            typ, data = imap.uid("search", None, f"UID {last_uid + 1}:*")
            take = [u for u in (data[0] or b"").split() if int(u) > last_uid]

        new_count = 0
        total = len(take)
        for i, uid in enumerate(take):
            if total > 30 and (i % 20 == 0 or i == total - 1):
                self._set_status(f"📥 正在收邮件 {i + 1}/{total}…")
            typ, msg_data = imap.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            try:
                parsed = parse_message(bytes(raw))
            except Exception:
                continue
            parsed["read"] = 0
            if db.insert_message(parsed):
                new_count += 1
                self.on_new_message(parsed)
            db.upsert_contact(parsed["email"], parsed.get("from_name"), parsed["ts"])
            db.kv_set("last_uid", str(int(uid)))
        return new_count

    def _run_once(self, cfg):
        self._set_status("正在连接邮箱…")
        imap = connect_imap(cfg)
        try:
            idle_ok = bool(cfg.get("use_idle", True)) and self._idle_errors < FALLBACK_AFTER_ERRORS
            self.mode = "实时推送(IDLE)" if idle_ok else "轮询"
            self._set_status(f"已连接（{self.mode}）")   # 连上就报，不等收完
            self._fetch_new(imap, cfg)
            while not self.stop_event.is_set():
                if idle_ok:
                    try:
                        _idle_wait(imap, min(IDLE_TIMEOUT, 60 * 10))
                        self._idle_errors = 0
                    except Exception:
                        self._idle_errors += 1
                        raise
                else:
                    self.stop_event.wait(float(cfg.get("check_interval") or 30))
                self._fetch_new(imap, cfg)
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def run(self):
        while not self.stop_event.is_set():
            cfg = self.get_cfg()
            if (cfg.get("auth_method") or "password") == "oauth2":
                ready = bool((cfg.get("oauth") or {}).get("refresh_token"))
            else:
                ready = bool(cfg.get("password"))
            if not (cfg.get("enabled") and cfg.get("email") and cfg.get("imap_host") and ready):
                self.mode = "未配置"
                self._set_status("未配置邮箱")
                self.stop_event.wait(5)
                continue
            try:
                self._run_once(cfg)
            except Exception as e:
                msg = str(e)
                self.mode = "重连中"
                if ("Basic authentication is disabled" in msg or "LOGIN failed" in msg
                        or "AUTHENTICATE failed" in msg):
                    self._set_status("⚠️ 密码登录已被微软禁用：请到「我的 → 邮箱设置」把登录方式改成「学校账号登录」")
                    self.stop_event.wait(60)
                else:
                    self._set_status(f"连接断开，5秒后重试：{msg[:80]}")
                    self.stop_event.wait(5)

    def stop(self):
        self.stop_event.set()


# ---------- 发送 ----------
def send_mail(cfg, to, subject, body, in_reply_to="", references="", from_name=""):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"{from_name or cfg.get('display_name') or cfg['email']} <{cfg['email']}>"
    msg["To"] = to
    msg["Date"] = email.utils.formatdate(localtime=True)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references + " " + in_reply_to).strip()

    port = int(cfg.get("smtp_port") or 587)
    if port == 465:
        server = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=30)
    else:
        server = smtplib.SMTP(cfg["smtp_host"], port, timeout=30)
        server.ehlo()
        try:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        except smtplib.SMTPException:
            pass
    try:
        method, secret = _get_token(cfg)
        if method == "oauth2":
            auth_str = oauth_ms.xoauth2_string(cfg["email"], secret)
            code, resp = server.docmd("AUTH", "XOAUTH2 " + auth_str)
            if code != 235:
                raise smtplib.SMTPAuthenticationError(code, str(resp))
        else:
            server.login(cfg["email"], secret)
        server.sendmail(cfg["email"], [to], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return msg.get("Message-ID") or f"sent-{int(time.time()*1000)}"


# ---------- 历史邮件全量导入 ----------
def import_history(cfg, progress_cb=None, limit=0):
    """把收件箱历史邮件一次性拉进本地（limit=0 表示全部）。

    返回 (新增条数, 总条数)。已存在的邮件会按 Message-ID 自动跳过，可反复执行。
    """
    imap = connect_imap(cfg)
    try:
        typ, data = imap.uid("search", None, "ALL")
        uids = (data[0] or b"").split()
        if limit and len(uids) > limit:
            uids = uids[-int(limit):]
        total = len(uids)
        new_count = 0
        for i, uid in enumerate(uids):
            try:
                typ, msg_data = imap.uid("fetch", uid, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                parsed = parse_message(bytes(raw))
            except Exception:
                continue
            parsed["read"] = 1  # 历史邮件算“已读”，不让红点爆掉
            if db.insert_message(parsed):
                new_count += 1
                db.upsert_contact(parsed["email"], parsed.get("from_name"), parsed["ts"])
            if progress_cb and (i % 5 == 0 or i == total - 1):
                progress_cb(i + 1, total, new_count)
        if uids:
            # 标记“历史已导入到这里”，收信线程就不会重复下载老邮件
            db.kv_set("last_uid", str(int(uids[-1])))
        return new_count, total
    finally:
        try:
            imap.logout()
        except Exception:
            pass
