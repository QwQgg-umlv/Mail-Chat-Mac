# -*- coding: utf-8 -*-
"""邮聊 - 本地数据库（SQLite）：联系人、邮件消息、日历事件、同步状态。"""
import os
import sqlite3
import sys
import threading
import time

if getattr(sys, "frozen", False):
    _DATA_DIR = os.path.dirname(os.path.abspath(sys.executable))   # exe 旁边 = 用户数据
else:
    _DATA_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DATA_DIR, "mailchat.db")
_lock = threading.Lock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts(
  email TEXT PRIMARY KEY,
  name TEXT,
  color TEXT,
  starred INTEGER DEFAULT 0,
  pinned INTEGER DEFAULT 0,
  muted INTEGER DEFAULT 0,
  note TEXT DEFAULT '',
  last_ts REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  msg_id TEXT,
  email TEXT,
  direction TEXT,
  subject TEXT,
  body TEXT,
  summary TEXT,
  translation TEXT DEFAULT '',
  ts REAL,
  read INTEGER DEFAULT 1,
  is_demo INTEGER DEFAULT 0,
  meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_msg_email ON messages(email, ts);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT,
  start_ts REAL,
  end_ts REAL,
  all_day INTEGER DEFAULT 0,
  location TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  source_msg_id INTEGER,
  source_email TEXT DEFAULT '',
  confirmed INTEGER DEFAULT 0,
  is_demo INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""


def get_conn():
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def init_db():
    get_conn()
    _ensure_columns()


# ---------- kv ----------
def kv_get(key, default=None):
    row = get_conn().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(key, value):
    conn = get_conn()
    conn.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))
    conn.commit()


# ---------- contacts ----------
AVATAR_COLORS = ["#07C160", "#2E6BE6", "#F5A623", "#E64340", "#8A5CD6",
                 "#12B2A6", "#D6488B", "#5B8DEF", "#B26A2E", "#3AA655"]


def color_for(email):
    h = sum(ord(c) for c in (email or "?"))
    return AVATAR_COLORS[h % len(AVATAR_COLORS)]


def upsert_contact(email, name=None, ts=None):
    if not email:
        return
    conn = get_conn()
    row = conn.execute("SELECT * FROM contacts WHERE email=?", (email,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO contacts(email,name,color,last_ts) VALUES(?,?,?,?)",
                     (email, name or email.split("@")[0], color_for(email), ts or 0))
    else:
        if name and name != email:
            conn.execute("UPDATE contacts SET name=? WHERE email=?", (name, email))
        if ts:
            conn.execute("UPDATE contacts SET last_ts=MAX(last_ts,?) WHERE email=?", (ts, email))
    conn.commit()


def update_contact(email, **fields):
    allowed = {"name", "starred", "pinned", "muted", "note"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    vals.append(email)
    conn = get_conn()
    conn.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE email=?", vals)
    conn.commit()


def get_contact(email):
    row = get_conn().execute("SELECT * FROM contacts WHERE email=?", (email,)).fetchone()
    return dict(row) if row else None


# ---------- messages ----------
def insert_message(msg):
    """msg: dict(email,direction,subject,body,ts,msg_id,read,is_demo,meta)。返回是否为新消息。"""
    conn = get_conn()
    msg_id = msg.get("msg_id")
    if msg_id:
        dup = conn.execute("SELECT id FROM messages WHERE msg_id=? AND direction=?",
                           (msg_id, msg.get("direction", "in"))).fetchone()
        if dup:
            return False
    conn.execute(
        "INSERT INTO messages(msg_id,email,direction,subject,body,summary,ts,read,is_demo,meta)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (msg_id, msg.get("email"), msg.get("direction", "in"), msg.get("subject", ""),
         msg.get("body", ""), msg.get("summary", ""), msg.get("ts", time.time()),
         1 if msg.get("read", 1) else 0, 1 if msg.get("is_demo") else 0,
         msg.get("meta") or "{}"))
    conn.commit()
    return True


def set_summary(msg_id, summary):
    conn = get_conn()
    conn.execute("UPDATE messages SET summary=? WHERE id=?", (summary, msg_id))
    conn.commit()


def save_translation(msg_id, translation):
    _ensure_columns()
    conn = get_conn()
    conn.execute("UPDATE messages SET translation=? WHERE id=?", (translation, msg_id))
    conn.commit()


def list_messages(email, limit=200, before_id=None):
    conn = get_conn()
    sql = ("SELECT * FROM messages WHERE email=?"
           + (" AND id<?" if before_id else "")
           + " ORDER BY ts ASC, id ASC LIMIT ?")
    args = [email]
    if before_id:
        args.append(before_id)
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def get_message(msg_id):
    row = get_conn().execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return dict(row) if row else None


def mark_read(email):
    conn = get_conn()
    conn.execute("UPDATE messages SET read=1 WHERE email=?", (email,))
    conn.commit()


def mark_all_read():
    conn = get_conn()
    conn.execute("UPDATE messages SET read=1 WHERE direction='in' AND read=0")
    conn.commit()


def search_messages(q, scope="all", limit=60):
    """Outlook 式搜索：按范围（全部/发件人/主题/正文）搜邮件，返回带摘要的结果。"""
    if not q:
        return []
    like = f"%{q}%"
    col = {"from": "m.email", "subject": "m.subject", "body": "m.body"}.get(scope)
    if col:
        where, args = f"{col} LIKE ?", [like]
    else:
        where = "(m.subject LIKE ? OR m.body LIKE ? OR m.email LIKE ?)"
        args = [like, like, like]
    sql = (f"SELECT m.id, m.email, m.subject, m.body, m.ts, m.direction, m.read, "
           f"c.name AS from_name FROM messages m "
           f"LEFT JOIN contacts c ON c.email = m.email "
           f"WHERE {where} ORDER BY m.ts DESC LIMIT ?")
    rows = get_conn().execute(sql, args + [limit]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        body = (d.pop("body") or "").replace("\n", " ").replace("\r", " ")
        i = body.lower().find(q.lower())
        start = max(0, i - 16) if i >= 0 else 0
        d["snippet"] = body[start:start + 90]
        d["hit_at"] = i
        out.append(d)
    return out


def _ensure_columns():
    """老数据库升级：补上缺的列（events_done=是否已整理进日历，translation=译文）。"""
    conn = get_conn()
    for ddl in ("ALTER TABLE messages ADD COLUMN events_done INTEGER DEFAULT 0",
                "ALTER TABLE messages ADD COLUMN translation TEXT DEFAULT ''"):
        try:
            conn.execute(ddl)
            conn.commit()
        except sqlite3.OperationalError:
            pass
    # 纠错：日程的“来源邮件”必须是 messages 的数字行号（日历点回邮件靠它）；
    # 老版本误存过 Message-ID 字符串，这里自动换算成行号。
    try:
        conn.execute("""
            UPDATE events SET source_msg_id = (
                SELECT m.id FROM messages m WHERE m.msg_id = events.source_msg_id
                ORDER BY m.id DESC LIMIT 1)
            WHERE source_msg_id IS NOT NULL AND typeof(source_msg_id)='text'
        """)
        conn.commit()
    except sqlite3.OperationalError:
        pass


def messages_pending_events():
    _ensure_columns()
    rows = get_conn().execute(
        "SELECT id,email,subject,body,is_demo FROM messages WHERE IFNULL(events_done,0)=0 ORDER BY ts ASC").fetchall()
    return [dict(r) for r in rows]


def mark_events_done(msg_id):
    _ensure_columns()
    conn = get_conn()
    conn.execute("UPDATE messages SET events_done=1 WHERE id=?", (msg_id,))
    conn.commit()


def unread_count(email=None):
    conn = get_conn()
    if email:
        row = conn.execute("SELECT COUNT(*) c FROM messages WHERE email=? AND read=0 AND direction='in'",
                           (email,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) c FROM messages WHERE read=0 AND direction='in'").fetchone()
    return row["c"]


def delete_message(msg_id):
    conn = get_conn()
    conn.execute("DELETE FROM messages WHERE id=?", (msg_id,))
    conn.commit()


# ---------- chats ----------
def list_chats():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM contacts").fetchall()
    chats = []
    for r in rows:
        c = dict(r)
        last = conn.execute(
            "SELECT subject,body,ts,direction,read FROM messages WHERE email=? "
            "ORDER BY ts DESC, id DESC LIMIT 1", (c["email"],)).fetchone()
        if not last:
            continue
        c["last_subject"] = last["subject"]
        c["last_body"] = last["body"]
        c["last_ts"] = last["ts"]
        c["last_direction"] = last["direction"]
        c["unread"] = unread_count(c["email"])
        chats.append(c)
    chats.sort(key=lambda x: (-(x.get("pinned") or 0), -(x.get("last_ts") or 0)))
    return chats


# ---------- events ----------
def insert_event(ev):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO events(title,start_ts,end_ts,all_day,location,notes,source_msg_id,source_email,confirmed,is_demo)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (ev.get("title", "未命名日程"), ev.get("start_ts"), ev.get("end_ts"),
         1 if ev.get("all_day") else 0, ev.get("location", ""), ev.get("notes", ""),
         ev.get("source_msg_id"), ev.get("source_email", ""),
         1 if ev.get("confirmed") else 0, 1 if ev.get("is_demo") else 0))
    conn.commit()
    return cur.lastrowid


def update_event(event_id, **fields):
    allowed = {"title", "start_ts", "end_ts", "all_day", "location", "notes", "confirmed"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    vals.append(event_id)
    conn = get_conn()
    conn.execute(f"UPDATE events SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()


def delete_event(event_id):
    conn = get_conn()
    conn.execute("DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()


def list_events(from_ts=None, to_ts=None):
    conn = get_conn()
    sql = "SELECT * FROM events WHERE 1=1"
    args = []
    if from_ts is not None:
        sql += " AND (end_ts IS NULL AND start_ts>=? OR end_ts>=?)"
        args += [from_ts, from_ts]
    if to_ts is not None:
        sql += " AND start_ts<=?"
        args.append(to_ts)
    sql += " ORDER BY start_ts ASC"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def get_event(event_id):
    row = get_conn().execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    return dict(row) if row else None


# ---------- demo data ----------
def has_real_data():
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) c FROM messages WHERE is_demo=0").fetchone()
    return row["c"] > 0


def seed_demo():
    """载入演示数据（明确标注 is_demo=1，随时可一键清除）。"""
    if kv_get("demo_seeded") == "1" or has_real_data():
        return False
    import json
    now = time.time()
    samples = [
        ("zhang.wei@company.com", "张伟", "产品评审会改到周四下午3点", [
            ("in", "产品评审会改到周四下午3点",
             "你好，\n\n原定明天上午的产品评审会改到 2026年10月15日(周四) 下午3点，地点在 3楼会议室A。\n\n请提前准备好各自的方案文档，会上需要过一遍Q4的排期。\n\n谢谢！\n张伟", 3600 * 26),
            ("out", "Re: 产品评审会改到周四下午3点",
             "收到！我会准时到，方案文档我周三下班前发群里。", 3600 * 25),
            ("in", "Re: 产品评审会改到周四下午3点",
             "好的，另外记得带上上次的用户调研数据，老板想看看。", 3600 * 3),
        ]),
        ("billing@cloudservice.com", "云服务账单", "您的10月账单已生成", [
            ("in", "您的10月账单已生成",
             "尊敬的用户：\n\n您的10月账单已生成，应付金额 ¥268.00，请于 2026年10月20日 前完成支付。\n\n如已支付请忽略本邮件。\n\nCloud Service 财务部", 3600 * 50),
        ]),
        ("amy@university.edu", "Amy Chen", "Re: 论文初稿意见", [
            ("in", "Re: 论文初稿意见",
             "Hi,\n\nI've read your draft. The methodology section is solid, but please revise the related work part before Friday.\n\nLet's have a 30-minute call tomorrow 10:00 AM to discuss.\n\nBest,\nAmy", 3600 * 8),
        ]),
    ]
    for email, name, subject, msgs in samples:
        upsert_contact(email, name, now)
        for direction, subj, body, ago in msgs:
            insert_message({"email": email, "direction": direction, "subject": subj,
                            "body": body, "ts": now - ago, "read": 1, "is_demo": 1,
                            "msg_id": f"demo-{email}-{int(now - ago)}"})
    insert_event({"title": "产品评审会", "start_ts": now + 3600 * 30, "end_ts": now + 3600 * 31.5,
                  "location": "3楼会议室A", "notes": "带上用户调研数据（来自张伟的邮件）",
                  "source_email": "zhang.wei@company.com", "is_demo": 1, "confirmed": 1})
    insert_event({"title": "与 Amy 电话讨论论文", "start_ts": now + 3600 * 12, "end_ts": now + 3600 * 12.5,
                  "notes": "讨论 related work 修改", "source_email": "amy@university.edu", "is_demo": 1,
                  "confirmed": 1})
    insert_event({"title": "云服务账单缴费", "start_ts": now + 3600 * 90, "all_day": 1,
                  "notes": "¥268.00", "source_email": "billing@cloudservice.com", "is_demo": 1,
                  "confirmed": 1})
    kv_set("demo_seeded", "1")
    return True


def clear_demo():
    conn = get_conn()
    conn.execute("DELETE FROM messages WHERE is_demo=1")
    conn.execute("DELETE FROM events WHERE is_demo=1")
    # 清掉只含演示消息的联系人
    conn.execute("DELETE FROM contacts WHERE email NOT IN (SELECT DISTINCT email FROM messages)")
    conn.commit()
    kv_set("demo_seeded", "0")
