# -*- coding: utf-8 -*-
"""邮聊 - 本地服务器：网页界面 + REST API + SSE 实时推送。

启动：python server.py        （会自动打开窗口）
"""
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ai_service
import db
import mail_sync
import oauth_ms
import timeutil

# 控制台/重定向输出统一按 UTF-8 处理，避免中文/表情在日志里报编码错误
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 打包成 exe 后：界面资源从包里读，用户数据（配置/数据库）放 exe 旁边，方便备份迁移
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))    # 数据跟着 exe 走
    RESOURCE_DIR = getattr(sys, "_MEIPASS", APP_DIR)               # exe 内打包的资源
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = APP_DIR
STATIC_DIR = os.path.join(RESOURCE_DIR, "static")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULT_CONFIG = {
    "app": {"port": 8765, "app_name": "邮聊"},
    "mail": {
        "enabled": False,
        "auth_method": "password",
        "email": "",
        "display_name": "",
        "password": "",
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "check_interval": 30,
        "use_idle": True,
        "initial_fetch": 20,
        "history_limit": 0,
        "auto_summary": False,
        "auto_translate": True,
        "notify": True,
        "oauth": {
            "client_id": "",
            "tenant": "organizations",
            "refresh_token": "",
            "access_token": "",
            "expires_at": 0,
        },
    },
    "ai": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "temperature": 0.4,
        "prompts": dict(ai_service.DEFAULT_PROMPTS),
    },
    "calendar": {
        "timezone": "UTC+8",
        "utc_offset_hours": 8,
        "week_start": 1,
        "remind_hours": 24,
        "auto_extract": True,
        "ai_backfill": False,
    },
    "ui": {
        "theme": "dark",
        "quick_replies": ["收到，谢谢！", "好的，我确认一下再回复您。", "已阅，按计划执行。", "抱歉，这个时间我不方便，能否换一个？"],
        "show_original": True,
        "long_press_hint": True,
    },
}

_lock = threading.Lock()
_config = None
mail_status = {"status": "未启动", "mode": "未启动", "ts": 0}
translate_cache = {}


# ---------------- 配置 ----------------
def _merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    global _config
    with _lock:
        if _config is None:
            cfg = DEFAULT_CONFIG
            if os.path.exists(CONFIG_PATH):
                try:
                    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                        cfg = _merge(DEFAULT_CONFIG, json.load(f))
                except Exception:
                    cfg = DEFAULT_CONFIG
            _config = cfg
        return _config


def save_config(patch):
    global _config
    cfg = _merge(load_config(), patch or {})
    with _lock:
        _config = cfg
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    return cfg


def mail_cfg():
    return load_config()["mail"]


def ai_cfg():
    return load_config()["ai"]


def cal_cfg():
    return load_config()["calendar"]


# ---------------- SSE 推送 ----------------
class Broker:
    def __init__(self):
        self.subs = []
        self.lock = threading.Lock()

    def publish(self, event):
        payload = "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        with self.lock:
            for q in list(self.subs):
                q.put(payload)

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)


broker = Broker()

# 学校账号（OAuth2）登录流程的内存状态
oauth_flow = {
    "state": "idle",        # idle | pending | ok | error
    "message": "",
    "user_code": "",
    "verification_uri": "",
    "device_code": "",
    "expires_at": 0,
    "interval": 5,
    "client_id": "",
    "tenant": "",
    "next_poll": 0,
}
redirect_variant = {"i": 0}   # 回跳方式轮换计数（每次“开始登录”换下一种）


def _oauth_watchdog(client_id, tenant, device_code, expires_at, interval):
    """后台线程：用户在网页上登录成功后自动把令牌领回来保存。

    和界面完全解耦 —— 用户关掉弹窗、切走页面都不影响登录完成。
    网络抖动会一直自动重试，直到登录码过期才放弃。
    """
    hard_keys = ("取消了登录", "登录码过期", "客户端 ID 不对", "没打开「允许公共客户端流」",
                 "登录码」方式不被支持")
    while time.time() < expires_at:
        time.sleep(max(3, interval))
        try:
            r = oauth_ms.poll_device_token(client_id, device_code, tenant)
        except oauth_ms.OAuthError as e:
            # 网络问题：不放弃，标个状态继续试
            oauth_flow.update({"message": f"网络波动，自动重试中…（{str(e)[:40]}）"})
            continue
        if r["status"] == "ok":
            save_config({"mail": {
                "auth_method": "oauth2", "enabled": True,
                "oauth": {
                    "client_id": client_id,
                    "tenant": tenant,
                    "refresh_token": r.get("refresh_token") or "",
                    "access_token": r["access_token"],
                    "expires_at": time.time() + r.get("expires_in", 3600),
                }}})
            oauth_flow.update({"state": "ok", "message": "登录成功！邮件正在同步…", "user_code": ""})
            set_mail_status("登录成功，正在连接邮箱…")
            broker.publish({"type": "config"})
            return
        if r["status"] == "error":
            msg = r.get("message") or "登录失败"
            if any(k in msg for k in hard_keys):
                oauth_flow.update({"state": "error", "message": msg})
                return
            # 软错误（如还需点同意/临时拒绝）：继续试到过期
            oauth_flow.update({"message": f"微软说「{msg[:50]}」，自动重试中…"})
    if oauth_flow.get("state") == "pending":
        oauth_flow.update({"state": "error", "message": "登录码过期了，请重新点「开始登录」。"})


def _save_tokens(client_id, tenant, tokens):
    """把拿到的令牌写进配置，并通知界面。"""
    save_config({"mail": {
        "auth_method": "oauth2", "enabled": True,
        "oauth": {
            "client_id": client_id,
            "tenant": tenant,
            "refresh_token": tokens.get("refresh_token") or "",
            "access_token": tokens["access_token"],
            "expires_at": time.time() + tokens.get("expires_in", 3600),
        }}})
    oauth_flow.update({"state": "ok", "message": "登录成功！邮件正在同步…", "user_code": ""})
    set_mail_status("登录成功，正在连接邮箱…")
    broker.publish({"type": "config"})


class _CallbackHandler(BaseHTTPRequestHandler):
    """浏览器授权后的回跳页：收下 code，显示一页“可以关掉了”。"""

    def log_message(self, *a):
        pass

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.queue.put({
            "code": (q.get("code") or [""])[0],
            "state": (q.get("state") or [""])[0],
            "error": (q.get("error") or [""])[0],
            "error_description": (q.get("error_description") or [""])[0],
        })
        ok = bool((q.get("code") or [""])[0])
        body = ("<html><head><meta charset='utf-8'><title>邮聊</title></head>"
                "<body style='font-family:Microsoft YaHei,sans-serif;padding:50px;text-align:center'>"
                + ("<h2>✅ 授权完成！</h2><p style='font-size:18px'>可以关掉这个页面，回到「邮聊」，"
                   "剩下的它会自动完成。</p>" if ok else
                   "<h2>❌ 授权没有完成</h2><p style='font-size:18px'>请回到「邮聊」重新点「开始登录」。</p>")
                + "</body></html>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _authcode_watchdog(client_id, tenant, redirect_uri, verifier, state, httpd, deadline):
    """等浏览器回跳 → 用 code 换令牌 → 入库。"""
    try:
        while time.time() < deadline:
            try:
                r = httpd.queue.get(timeout=5)
            except Exception:
                continue
            if r.get("error"):
                oauth_flow.update({"state": "error",
                                   "message": oauth_ms.friendly_error(
                                       {"error": r["error"], "error_description": r.get("error_description", "")})})
                return
            if r.get("code") and (not state or r.get("state") == state):
                try:
                    tokens = oauth_ms.exchange_code(client_id, tenant, r["code"], redirect_uri, verifier)
                except oauth_ms.OAuthError as e:
                    oauth_flow.update({"state": "error", "message": str(e)})
                    return
                _save_tokens(client_id, tenant, tokens)
                return
        if oauth_flow.get("state") == "pending":
            oauth_flow.update({"state": "error", "message": "授权超时（5分钟），请重新点「开始登录」。"})
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass


def set_mail_status(status, mode=None):
    mail_status["status"] = status
    mail_status["mode"] = mode or mail_status.get("mode", "")
    mail_status["ts"] = time.time()
    broker.publish({"type": "status", "status": status, "mode": mail_status["mode"]})


def on_new_message(parsed):
    broker.publish({"type": "mail", "email": parsed["email"],
                    "subject": parsed["subject"], "ts": parsed["ts"]})
    cfg = load_config()
    # 自动 AI 摘要（可关）
    if cfg["mail"].get("auto_summary") and ai_service.is_configured(cfg["ai"]):
        try:
            s = ai_service.summarize(cfg["ai"], parsed["subject"], parsed["body"])
            row = db.get_conn().execute(
                "SELECT id FROM messages WHERE msg_id=? ORDER BY id DESC LIMIT 1",
                (parsed["msg_id"],)).fetchone()
            if row:
                db.set_summary(row["id"], s)
                broker.publish({"type": "summary", "email": parsed["email"], "msg_id": row["id"]})
        except Exception:
            pass
    # 自动把邮件里的时间放进日历（待确认）——source_msg_id 用数字行号，方便日历点回邮件
    if cfg["calendar"].get("auto_extract"):
        row = db.get_conn().execute(
            "SELECT id FROM messages WHERE msg_id=? ORDER BY id DESC LIMIT 1",
            (parsed["msg_id"],)).fetchone()
        add_events_from_text(parsed["subject"], parsed["body"], parsed["email"],
                             row["id"] if row else None, confirmed=0, use_ai=False)
    # 自动翻译成中文（后台异步跑，不挡收信；中文邮件跳过）
    if cfg["mail"].get("auto_translate"):
        threading.Thread(target=_auto_translate_msg, args=(parsed,), daemon=True).start()


def _auto_translate_msg(parsed):
    """把新邮件翻成中文存起来：免费翻译引擎优先（不花 AI 额度），失败再用 AI。"""
    try:
        row = db.get_conn().execute(
            "SELECT id, body FROM messages WHERE msg_id=? ORDER BY id DESC LIMIT 1",
            (parsed["msg_id"],)).fetchone()
        if not row:
            return
        text = (row["body"] or parsed["body"] or "").strip()
        if not text or ai_service.looks_chinese(text):
            return
        trans = ai_service.auto_translate(text)
        if not trans:
            cfg = load_config()
            if ai_service.is_configured(cfg.get("ai")):
                trans = ai_service.translate(cfg["ai"], text)
        if trans:
            db.save_translation(row["id"], trans)
            broker.publish({"type": "translated", "email": parsed["email"], "msg_id": row["id"]})
    except Exception:
        pass


def iso_to_ts(s, offset_hours=8):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone(timedelta(hours=offset_hours)))
        return d.timestamp()
    except Exception:
        try:
            return float(s)
        except Exception:
            return None


def add_events_from_text(subject, body, source_email, source_msg_id=None,
                         confirmed=0, use_ai=False, is_demo=0):
    """规则提取 + （可选）AI 提取，写入日历。返回新增数量。"""
    off = cal_cfg().get("utc_offset_hours", 8)
    now = timeutil.tz_now(off)
    cands = timeutil.extract_time_candidates(body, subject, off, now)
    if use_ai and ai_service.is_configured(ai_cfg()):
        try:
            items, _err = ai_service.extract_events(
                ai_cfg(), subject, body, now.isoformat())
            for it in items:
                cands.append({
                    "title": it["title"],
                    "start_ts": iso_to_ts(it.get("start"), off),
                    "end_ts": iso_to_ts(it.get("end"), off),
                    "all_day": 1 if it.get("all_day") else 0,
                    "location": it.get("location", ""),
                    "notes": it.get("notes", ""),
                })
        except Exception:
            pass
    added = 0
    seen = set()
    for c in cands:
        if not c.get("start_ts"):
            continue
        key = (round(c["start_ts"] / 600), c.get("title"))
        if key in seen:
            continue
        seen.add(key)
        db.insert_event({
            "title": c.get("title") or subject or "日程",
            "start_ts": c.get("start_ts"),
            "end_ts": c.get("end_ts") or (c.get("start_ts") + 3600),
            "all_day": 1 if c.get("all_day") else 0,
            "location": c.get("location", ""),
            "notes": c.get("notes", ""),
            "source_msg_id": source_msg_id,
            "source_email": source_email or "",
            "confirmed": confirmed,
            "is_demo": is_demo,
        })
        added += 1
    return added


# ---------------- 全量导入 + 日历整理 ----------------
backfill_state = {"running": False, "text": "", "use_ai": False}


def _progress(text, done=False):
    backfill_state["text"] = text
    broker.publish({"type": "progress", "text": text, "done": done})


def run_backfill(use_ai=None):
    """后台任务：① 把全部历史邮件收进来 ② 把每封邮件里的时间整理进日历。"""
    if backfill_state["running"]:
        return False
    if use_ai is None:
        use_ai = bool(cal_cfg().get("ai_backfill"))
    backfill_state.update(running=True, use_ai=use_ai)

    def job():
        try:
            cfg = mail_cfg()
            ready = ((cfg.get("auth_method") or "password") == "oauth2"
                     and (cfg.get("oauth") or {}).get("refresh_token")) or cfg.get("password")
            if cfg.get("enabled") and cfg.get("email") and ready:
                limit = int(cfg.get("history_limit") or 0)
                _progress(f"📥 正在导入历史邮件…（{'全部' if not limit else '最近 ' + str(limit) + ' 封'}）")

                def prog(i, total, new):
                    _progress(f"📥 正在导入历史邮件 {i}/{total}（新增 {new} 封）")
                try:
                    new_count, total = mail_sync.import_history(cfg, prog, limit)
                    _progress(f"📥 邮件导入完成：收件箱共 {total} 封，新导入 {new_count} 封")
                except Exception as e:
                    _progress(f"⚠️ 邮件导入中断：{str(e)[:80]}，继续整理已有的邮件…")

            # 把每封邮件里提到的时间整理进日历
            pending = db.messages_pending_events()
            total2 = len(pending)
            added = 0
            use_ai_now = use_ai and ai_service.is_configured(ai_cfg())
            for i, m in enumerate(pending):
                try:
                    added += add_events_from_text(m["subject"], m["body"], m["email"], m["id"],
                                                  confirmed=0, use_ai=use_ai_now, is_demo=m.get("is_demo"))
                except Exception:
                    pass   # 单封邮件出错不影响整体整理
                db.mark_events_done(m["id"])
                if i % 5 == 0 or i == total2 - 1:
                    _progress(f"📅 正在整理进日历 {i + 1}/{total2}（已找到 {added} 个日程）")
            db.kv_set("backfill_done", "1")
            _progress(f"✅ 全部完成：{total2} 封邮件已整理，日历新增 {added} 个日程（橙色=待确认，点确认即可）", done=True)
            broker.publish({"type": "events"})
            broker.publish({"type": "mail", "email": ""})
        except Exception as e:
            _progress(f"❌ 整理失败：{str(e)[:100]}", done=True)
        finally:
            backfill_state["running"] = False

    threading.Thread(target=job, daemon=True).start()
    return True


def _auto_backfill_watch():
    """连接成功后自动做一次全量导入+日历整理（用户要求：连接即自动处理）。"""
    while True:
        time.sleep(8)
        try:
            if backfill_state["running"] or db.kv_get("backfill_done") == "1":
                continue
            st = str(mail_status.get("status") or "")
            if "已连接" in st or "登录成功" in st or "正在连接" in st:
                run_backfill()
        except Exception:
            continue


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "MailChat/1.0"
    # 必须用 HTTP/1.1：SSE 会保持长连接，若用 HTTP/1.0 浏览器会把已关闭的
    # 旧连接放回复用池，导致 POST 请求（如“开始登录”）发进死连接而卡住。
    protocol_version = "HTTP/1.1"

    # ---- 基础 ----
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _page(self, path):
        # 只有一套界面（桌面版）：/ 和 /static/*
        if path.startswith("/static/"):
            base, rel = STATIC_DIR, path[len("/static/"):]
        else:
            base, rel = STATIC_DIR, path.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(base, rel))
        if not full.startswith(os.path.normpath(base)):
            self.send_error(403)
            return
        if not os.path.isfile(full):
            self.send_error(404)
            return
        ctype = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8", "svg": "image/svg+xml",
                 "json": "application/json; charset=utf-8",
                 "png": "image/png", "ico": "image/x-icon"}.get(
            full.rsplit(".", 1)[-1].lower(), "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = broker.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=15)
                except queue.Empty:
                    payload = ": ping\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            broker.unsubscribe(q)

    # ---- 路由 ----
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = dict(urllib.parse.parse_qsl(parsed.query))
        try:
            if path == "/api/stream":
                return self._sse()
            if path == "/api/chats":
                return self._json({"chats": db.list_chats(), "unread_total": db.unread_count(),
                                   "demo": db.kv_get("demo_seeded") == "1" and not db.has_real_data()})
            if path == "/api/messages":
                email_addr = (qs.get("email") or "").lower()
                msgs = db.list_messages(email_addr, int(qs.get("limit") or 300))
                contact = db.get_contact(email_addr)
                return self._json({"messages": msgs, "contact": contact})
            if path == "/api/events":
                from_ts = float(qs["from"]) if qs.get("from") else None
                to_ts = float(qs["to"]) if qs.get("to") else None
                return self._json({"events": db.list_events(from_ts, to_ts)})
            if path == "/api/search":
                return self._json({"results": db.search_messages(
                    (qs.get("q") or "").strip(), qs.get("scope") or "all", int(qs.get("limit") or 60))})
            if path == "/api/config":
                cfg = load_config()
                return self._json({"config": cfg,
                                   "mail_status": mail_status,
                                   "ai_configured": ai_service.is_configured(cfg["ai"])})
            if path == "/api/status":
                return self._json({"mail": mail_status,
                                   "ai_configured": ai_service.is_configured(ai_cfg()),
                                   "unread": db.unread_count(),
                                   "demo": db.kv_get("demo_seeded") == "1" and not db.has_real_data(),
                                   "backfill": backfill_state,
                                   "now": time.time()})
            if path == "/api/oauth/status":
                # 只读状态（令牌由后台线程领取），保证接口永远秒回
                return self._json({"state": oauth_flow["state"], "message": oauth_flow["message"],
                                   "user_code": oauth_flow["user_code"],
                                   "verification_uri": oauth_flow["verification_uri"],
                                   "verification_uri_complete": oauth_flow.get("verification_uri_complete", "")})
            return self._page(path)
        except BrokenPipeError:
            pass
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._body()
        try:
            if path == "/api/config":
                cfg = save_config(body.get("config") or body)
                broker.publish({"type": "config"})
                return self._json({"ok": True, "config": cfg,
                                   "ai_configured": ai_service.is_configured(cfg["ai"])})

            if path == "/api/mail/test":
                ok, msg = mail_sync.test_connection(body)
                return self._json({"ok": ok, "message": msg})

            if path == "/api/oauth/start":
                client_id = (body.get("client_id") or "").strip()
                tenant = (body.get("tenant") or "organizations").strip()
                mode = (body.get("mode") or "browser")
                if not client_id:
                    return self._json({"error": "请先填「应用程序(客户端) ID」（点上面按钮自动填）。"}, 400)

                if mode == "browser":
                    # 浏览器授权（authorization_code + PKCE）：唯一对第三方客户端都通的路
                    sock = socket.socket()
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                    sock.close()
                    # 回跳地址锁定为「保留端口的 http」：实测微软会原样跳回、能自动接收。
                    # （其它格式要么报 50011，要么被升级成 https 导致打不开）
                    redirect_uri = f"http://localhost:{port}"
                    verifier, challenge = oauth_ms.pkce_pair()
                    st = os.urandom(16).hex()
                    url = oauth_ms.build_auth_url(client_id, tenant, redirect_uri, challenge, st)
                    httpd = MailChatServer(("127.0.0.1", port), _CallbackHandler)
                    httpd.queue = queue.Queue()
                    threading.Thread(target=httpd.serve_forever, daemon=True).start()
                    oauth_flow.update({
                        "state": "pending", "message": "", "user_code": "",
                        "verification_uri": "", "verification_uri_complete": "",
                        "device_code": "", "expires_at": time.time() + 300,
                        "interval": 3, "client_id": client_id, "tenant": tenant,
                        "next_poll": 0, "mode": "browser", "redirect_uri": redirect_uri,
                        "_queue": httpd.queue,
                    })
                    threading.Thread(target=_authcode_watchdog, daemon=True,
                                     args=(client_id, tenant, redirect_uri, verifier, st,
                                           httpd, time.time() + 300)).start()
                    if not os.environ.get("MAILCHAT_OAUTH_BASE"):  # 测试环境不弹真浏览器
                        threading.Timer(0.3, lambda u=url: webbrowser.open(u)).start()
                    return self._json({"ok": True, "mode": "browser", "url": url,
                                       "redirect_uri": redirect_uri})

                # 备用：登录码方式（device_code，仅部分客户端支持）
                try:
                    d = oauth_ms.start_device_flow(client_id, tenant)
                except oauth_ms.OAuthError as e:
                    oauth_flow.update({"state": "error", "message": str(e)})
                    return self._json({"error": str(e)}, 400)
                oauth_flow.update({
                    "state": "pending", "message": "",
                    "user_code": d["user_code"],
                    "verification_uri": d["verification_uri"],
                    "verification_uri_complete": d.get("verification_uri_complete") or "",
                    "device_code": d["device_code"],
                    "expires_at": time.time() + d["expires_in"],
                    "interval": d["interval"],
                    "client_id": client_id, "tenant": tenant,
                    "next_poll": time.time() + d["interval"],
                })
                # 后台自动领取令牌：用户关弹窗也不影响登录完成
                threading.Thread(target=_oauth_watchdog, daemon=True,
                                 args=(client_id, tenant, d["device_code"],
                                       time.time() + d["expires_in"], d["interval"])).start()
                return self._json({"ok": True, "user_code": d["user_code"],
                                   "verification_uri": d["verification_uri"],
                                   "verification_uri_complete": d.get("verification_uri_complete") or "",
                                   "expires_in": d["expires_in"]})

            if path == "/api/oauth/logout":
                save_config({"mail": {"oauth": {"refresh_token": "", "access_token": "", "expires_at": 0}}})
                oauth_flow.update({"state": "idle", "message": "已退出登录", "user_code": ""})
                broker.publish({"type": "config"})
                return self._json({"ok": True})

            if path == "/api/oauth/complete":
                # 兜底：浏览器回跳“打不开”时，用户把地址栏整串链接粘过来
                url = (body.get("url") or "").strip()
                parsed_q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                result = {"code": (parsed_q.get("code") or [""])[0],
                          "state": (parsed_q.get("state") or [""])[0],
                          "error": (parsed_q.get("error") or [""])[0],
                          "error_description": (parsed_q.get("error_description") or [""])[0]}
                target_q = oauth_flow.get("_queue")
                if not target_q:
                    return self._json({"error": "当前没有进行中的登录 —— 请先点「开始登录（浏览器授权）」。"}, 400)
                if not result["code"] and not result["error"]:
                    return self._json({"error": "这串链接里没有找到登录凭证。请复制浏览器地址栏的整串链接"
                                                "（以 http 开头、含有 code= 字样），整个粘进来。"}, 400)
                target_q.put(result)
                return self._json({"ok": True, "message": "收到！正在完成登录，几秒后看状态。"})

            if path == "/api/messages/read":
                db.mark_read((body.get("email") or "").lower())
                broker.publish({"type": "read", "email": body.get("email")})
                return self._json({"ok": True})

            if path == "/api/messages/read_all":
                db.mark_all_read()
                broker.publish({"type": "read", "email": ""})
                return self._json({"ok": True, "unread": db.unread_count()})

            if path == "/api/send":
                cfg = mail_cfg()
                to = (body.get("to") or "").strip()
                if not to:
                    return self._json({"error": "收件人为空"}, 400)
                if (cfg.get("auth_method") or "password") == "oauth2":
                    ready = bool(cfg.get("enabled") and (cfg.get("oauth") or {}).get("refresh_token"))
                else:
                    ready = bool(cfg.get("enabled") and cfg.get("password"))
                if not ready:
                    return self._json({"error": "还没有配置好邮箱：请先到「我的 → 邮箱设置」完成登录。"}, 400)
                try:
                    msg_id = mail_sync.send_mail(
                        cfg, to, body.get("subject") or "", body.get("body") or "",
                        body.get("in_reply_to") or "", body.get("references") or "")
                except Exception as e:
                    return self._json({"error": f"发送失败：{e}"}, 502)
                db.upsert_contact(to.lower(), body.get("to_name"), time.time())
                parsed_msg = {"email": to.lower(), "direction": "out",
                              "subject": body.get("subject") or "",
                              "body": body.get("body") or "",
                              "ts": time.time(), "read": 1, "msg_id": msg_id}
                db.insert_message(parsed_msg)
                broker.publish({"type": "mail", "email": to.lower(), "ts": time.time()})
                return self._json({"ok": True, "msg_id": msg_id})

            if path == "/api/messages/delete":
                db.delete_message(int(body.get("id")))
                return self._json({"ok": True})

            if path == "/api/contacts/update":
                email_addr = (body.get("email") or "").lower()
                fields = {k: v for k, v in body.items() if k in ("name", "starred", "pinned", "muted", "note")}
                db.update_contact(email_addr, **fields)
                broker.publish({"type": "contacts"})
                return self._json({"ok": True, "contact": db.get_contact(email_addr)})

            if path == "/api/ai":
                return self._handle_ai(body)

            if path == "/api/events/save":
                eid = body.get("id")
                payload = {k: body.get(k) for k in
                           ("title", "start_ts", "end_ts", "all_day", "location", "notes", "confirmed")}
                payload = {k: v for k, v in payload.items() if v is not None}
                if eid:
                    db.update_event(int(eid), **payload)
                else:
                    payload.setdefault("confirmed", 1)
                    eid = db.insert_event(payload)
                broker.publish({"type": "events"})
                return self._json({"ok": True, "id": eid, "event": db.get_event(int(eid))})

            if path == "/api/events/delete":
                db.delete_event(int(body.get("id")))
                broker.publish({"type": "events"})
                return self._json({"ok": True})

            if path == "/api/events/extract":
                msg_id = int(body.get("message_id"))
                m = db.get_message(msg_id)
                if not m:
                    return self._json({"error": "找不到这封邮件"}, 404)
                use_ai = bool(body.get("use_ai"))
                added = add_events_from_text(m["subject"], m["body"], m["email"], msg_id,
                                             confirmed=0, use_ai=use_ai)
                if use_ai and added == 0 and not ai_service.is_configured(ai_cfg()):
                    return self._json({"error": "AI 还没配置，只能用基础规则识别时间。"})
                broker.publish({"type": "events"})
                return self._json({"ok": True, "added": added})

            if path == "/api/backfill":
                started = run_backfill(bool(body.get("use_ai")))
                return self._json({"ok": True, "running": backfill_state["running"],
                                   "started": started, "text": backfill_state["text"]})

            if path == "/api/demo/seed":
                db.seed_demo()
                broker.publish({"type": "mail", "email": ""})
                return self._json({"ok": True})

            if path == "/api/demo/clear":
                db.clear_demo()
                broker.publish({"type": "mail", "email": ""})
                return self._json({"ok": True})

            return self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # ---- AI ----
    def _handle_ai(self, body):
        cfg = ai_cfg()
        action = body.get("action")
        if action == "summarize":
            m = db.get_message(int(body["message_id"]))
            if not m:
                return self._json({"error": "找不到这封邮件"}, 404)
            if m.get("summary") and not body.get("refresh"):
                return self._json({"ok": True, "result": m["summary"], "cached": True})
            try:
                result = ai_service.summarize(cfg, m["subject"], m["body"])
            except ai_service.AIError as e:
                return self._json({"error": str(e)})
            db.set_summary(m["id"], result)
            return self._json({"ok": True, "result": result})

        if action == "translate":
            key = hash((body.get("text", "")[:2000], body.get("target_lang", "中文")))
            if key in translate_cache:
                return self._json({"ok": True, "result": translate_cache[key], "cached": True})
            result = ""
            # AI 可用就用 AI（质量更好）；没配 AI 或 AI 失败 → 免费翻译引擎，照样能用
            if ai_service.is_configured(cfg):
                try:
                    result = ai_service.translate(cfg, body.get("text", ""),
                                                  body.get("target_lang") or "中文")
                except ai_service.AIError:
                    result = ""
            if not result:
                try:
                    to_zh = (body.get("target_lang") or "中文").startswith("中")
                    result = ai_service.free_translate(body.get("text", ""),
                                                       "zh-CN" if to_zh else "en")
                except Exception:
                    result = ""
            if not result:
                return self._json({"error": "翻译失败：AI 未配置，免费翻译引擎也没连上（检查网络后重试）。"})
            translate_cache[key] = result
            return self._json({"ok": True, "result": result})

        if action == "reply_draft":
            m = db.get_message(int(body["message_id"])) if body.get("message_id") else None
            subject = body.get("subject") or (m["subject"] if m else "")
            text = body.get("body") or (m["body"] if m else "")
            try:
                result = ai_service.draft_reply(cfg, subject, text,
                                                body.get("intent") or "", body.get("extra") or "")
            except ai_service.AIError as e:
                return self._json({"error": str(e)})
            return self._json({"ok": True, "result": result})

        if action == "extract_events":
            m = db.get_message(int(body["message_id"]))
            if not m:
                return self._json({"error": "找不到这封邮件"}, 404)
            off = cal_cfg().get("utc_offset_hours", 8)
            now = timeutil.tz_now(off)
            out = []
            for c in timeutil.extract_time_candidates(m["body"], m["subject"], off, now):
                out.append({"source": "规则", "title": c["title"],
                            "start": timeutil.fmt_ts(c["start_ts"], off, True),
                            "end": timeutil.fmt_ts(c["end_ts"], off, True) if c.get("end_ts") else "",
                            "all_day": bool(c.get("all_day")), "start_ts": c["start_ts"],
                            "end_ts": c.get("end_ts"), "location": c.get("location", ""),
                            "notes": c.get("notes", "")})
            if ai_service.is_configured(cfg):
                try:
                    items, err = ai_service.extract_events(cfg, m["subject"], m["body"], now.isoformat())
                    for it in items:
                        out.append({"source": "AI", "title": it["title"],
                                    "start": it.get("start") or "",
                                    "end": it.get("end") or "",
                                    "all_day": it.get("all_day"),
                                    "start_ts": iso_to_ts(it.get("start"), off),
                                    "end_ts": iso_to_ts(it.get("end"), off),
                                    "location": it.get("location", ""),
                                    "notes": it.get("notes", "")})
                except ai_service.AIError as e:
                    return self._json({"ok": True, "candidates": out, "ai_error": str(e)})
            else:
                return self._json({"ok": True, "candidates": out,
                                   "ai_error": "AI 未配置，下面是基础规则识别出的时间。"})
            return self._json({"ok": True, "candidates": out})

        return self._json({"error": f"未知的 AI 操作：{action}"}, 400)


# ---------------- 启动 ----------------
class MailChatServer(ThreadingHTTPServer):
    """开启 SO_EXCLUSIVEADDRUSE：Windows 默认允许两个实例绑同一端口，
    第二个"假成功"但收不到请求 —— 独占绑定让端口占用检测真正生效。"""

    def server_bind(self):
        try:
            import struct
            self.socket.setsockopt(socket.SOL_SOCKET, 0x0004, struct.pack("i", 1))  # SO_EXCLUSIVEADDRUSE
        except OSError:
            pass
        super().server_bind()


def main():
    db.init_db()
    cfg = load_config()
    if not db.has_real_data() and db.kv_get("demo_seeded") != "1":
        db.seed_demo()

    port = int(cfg["app"].get("port") or 8765)
    httpd = None
    used_port = port
    for p in range(port, port + 15):
        try:
            httpd = MailChatServer(("127.0.0.1", p), Handler)
            used_port = p
            break
        except OSError:
            continue
    if httpd is None:
        print("❌ 端口被占用，无法启动。请关闭占用 8765 端口的程序后重试。")
        sys.exit(1)

    sync = mail_sync.MailSync(mail_cfg, on_new_message, lambda s: None)
    sync.on_status = lambda s: set_mail_status(s, sync.mode)
    sync.start()
    threading.Thread(target=_auto_backfill_watch, daemon=True).start()

    url = f"http://127.0.0.1:{used_port}"
    print("=" * 46)
    print("  邮聊 已启动")
    print(f"  打开地址：{url}")
    print("  关掉这个黑窗口 = 退出邮聊")
    print("=" * 46)

    if "--no-open" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sync.stop()
        print("\n邮聊已退出。")


if __name__ == "__main__":
    main()
