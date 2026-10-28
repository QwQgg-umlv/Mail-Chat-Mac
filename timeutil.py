# -*- coding: utf-8 -*-
"""邮聊 - 时间工具：时区换算 + 从邮件正文里“认出”时间（中文/英文常见写法）。"""
import calendar
import re
import time
from datetime import datetime, timedelta, timezone

WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

DATE_PATTERNS = [
    # 2026年10月15日 / 2026-10-15 / 2026/10/15
    (re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?"), "ymd"),
    # 10月15日 / 10-15
    (re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日?(?!\d)"), "md"),
    # 本周三 / 下周三 / 周三 / 星期三
    (re.compile(r"(本周|这周|下周|下下周)?\s*(?:周|星期)([一二三四五六日天])"), "week"),
]

TIME_RE = re.compile(
    r"(上午|早上|中午|下午|晚上|凌晨)?\s*"
    r"([0-2]?\d)\s*[:：点时]\s*([0-5]?\d)?\s*分?"
    r"|(上午|早上|中午|下午|晚上|凌晨)?\s*([0-2]?\d)\s*[:：]\s*([0-5]\d)")
RANGE_RE = re.compile(
    r"([0-2]?\d\s*[:：点时]\s*[0-5]?\d?分?)\s*(?:-|~|—|至|到)\s*([0-2]?\d\s*[:：点时]\s*[0-5]?\d?分?)")
DAY_SHIFT = {"今天": 0, "今晚": 0, "今早": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3}


def tz_now(utc_offset_hours=8):
    return datetime.now(timezone(timedelta(hours=utc_offset_hours)))


def ts_to_local(ts, utc_offset_hours=8):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone(timedelta(hours=utc_offset_hours)))


def local_to_ts(dt, utc_offset_hours=8):
    return dt.replace(tzinfo=timezone(timedelta(hours=utc_offset_hours))).timestamp()


def fmt_time(ts, utc_offset_hours=8):
    """微信风格时间标签：今天 14:32 / 昨天 / 10月5日 / 2026年10月5日"""
    if not ts:
        return ""
    d = ts_to_local(ts, utc_offset_hours)
    now = tz_now(utc_offset_hours)
    if d.date() == now.date():
        return d.strftime("%H:%M")
    if d.date() == (now - timedelta(days=1)).date():
        return "昨天 " + d.strftime("%H:%M")
    if d.year == now.year:
        return f"{d.month}月{d.day}日"
    return f"{d.year}年{d.month}月{d.day}日"


def fmt_ts(ts, utc_offset_hours=8, with_year=False):
    if ts is None:
        return ""
    d = ts_to_local(ts, utc_offset_hours)
    if with_year:
        return d.strftime("%Y-%m-%d %H:%M")
    return d.strftime("%m-%d %H:%M")


def _parse_time_of_day(text):
    """从片段里解析钟点，返回 (hour, minute) 或 None。"""
    m = re.search(
        r"(上午|早上|中午|下午|晚上|凌晨)?\s*([0-2]?\d)\s*[:：点时]\s*([0-5]?\d)?\s*分?", text)
    if not m:
        return None
    half, hh, mm = m.group(1), int(m.group(2)), int(m.group(3) or 0)
    if hh > 23 or mm > 59:
        return None
    if half in ("下午", "晚上") and hh < 12:
        hh += 12
    if half == "中午" and hh < 12:
        hh = 12
    if half in ("上午", "早上", "凌晨") and hh == 12:
        hh = 0
    return hh, mm


def extract_time_candidates(text, subject="", utc_offset_hours=8, now=None):
    """返回可能的日程：[{title,start_ts,end_ts,all_day,notes}]。本地规则识别，够用且不花API。"""
    if not text:
        return []
    now = now or tz_now(utc_offset_hours)
    src = f"{subject}\n{text}"
    results = []

    for pat, kind in DATE_PATTERNS:
        for m in pat.finditer(src):
            base_date = None
            note = ""
            if kind == "ymd":
                y, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
                try:
                    base_date = datetime(y, mo, dd)
                except ValueError:
                    continue
            elif kind == "md":
                mo, dd = int(m.group(1)), int(m.group(2))
                year = now.year
                try:
                    base_date = datetime(year, mo, dd)
                except ValueError:
                    continue
                if base_date.date() < now.date():
                    try:
                        base_date = datetime(year + 1, mo, dd)
                    except ValueError:
                        continue
            else:  # week
                week_word, day_char = m.group(1), m.group(2)
                target = "一二三四五六日天".find(day_char)
                if target < 0:
                    continue
                if target >= 6:      # 「日」「天」都= 周日（注意这个串有 8 个字）
                    target = 6
                current = now.weekday()  # 周一=0
                delta = (target - current) % 7
                if week_word in ("下周",):
                    delta += 7
                elif week_word in ("下下周",):
                    delta += 14
                elif week_word in ("本周", "这周") or week_word is None:
                    if delta == 0 and week_word is None:
                        delta = 7  # 单说“周三”默认指下一个
                base_date = (now + timedelta(days=delta)).replace(
                    hour=0, minute=0, second=0, microsecond=0)
                note = (week_word or "") + WEEK_CN[target]

            # 在匹配之后的一小段文字里找钟点
            window = src[m.end(): m.end() + 60]
            rng = RANGE_RE.search(window)
            tod = _parse_time_of_day(window)
            if rng:
                t1 = _parse_time_of_day(rng.group(1))
                t2 = _parse_time_of_day(rng.group(2))
                if t1 and t2:
                    start = base_date.replace(hour=t1[0], minute=t1[1])
                    end = base_date.replace(hour=t2[0], minute=t2[1])
                    results.append({"title": _guess_title(src, m.start(), subject),
                                    "start_ts": local_to_ts(start, utc_offset_hours),
                                    "end_ts": local_to_ts(end, utc_offset_hours),
                                    "all_day": 0, "notes": note})
                    continue
            if tod:
                start = base_date.replace(hour=tod[0], minute=tod[1])
                end = start + timedelta(hours=1)
                results.append({"title": _guess_title(src, m.start(), subject),
                                "start_ts": local_to_ts(start, utc_offset_hours),
                                "end_ts": local_to_ts(end, utc_offset_hours),
                                "all_day": 0, "notes": note})
            else:
                results.append({"title": _guess_title(src, m.start(), subject),
                                "start_ts": local_to_ts(base_date, utc_offset_hours),
                                "end_ts": local_to_ts(base_date + timedelta(days=1), utc_offset_hours),
                                "all_day": 1, "notes": note})

    # “明天下午3点”这类：没有日期数字，靠相对词
    for word, shift in DAY_SHIFT.items():
        for m in re.finditer(re.escape(word) + r"\s*(上午|早上|中午|下午|晚上|凌晨)?\s*([0-2]?\d)\s*[:：点时]?\s*([0-5]?\d)?", src):
            base_date = (now + timedelta(days=shift)).replace(hour=0, minute=0, second=0, microsecond=0)
            tod = _parse_time_of_day(m.group(0))
            if not tod:
                continue
            start = base_date.replace(hour=tod[0], minute=tod[1])
            results.append({"title": _guess_title(src, m.start(), subject),
                            "start_ts": local_to_ts(start, utc_offset_hours),
                            "end_ts": local_to_ts(start + timedelta(hours=1), utc_offset_hours),
                            "all_day": 0, "notes": word})

    # 去重：同一个开始时间点视为同一个日程（同一封邮件里“周四3点”和“10月15日15:00”是同一件事）
    seen, uniq = set(), []
    for r in results:
        if not r.get("start_ts"):
            continue
        key = round(r["start_ts"] / 1800)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


TIME_TOKENS = [
    r"\d{4}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}\s*日?",
    r"\d{1,2}\s*月\s*\d{1,2}\s*日?",
    r"(上午|早上|中午|下午|晚上|凌晨|清晨)?\s*[0-2]?\d\s*[:：点时]\s*[0-5]?\d?\s*分?",
    r"(本周|这周|下周|下下周)?\s*(?:周|星期)[一二三四五六日天]",
    r"(今天|今晚|今早|明天|明日|后天|大后天|当日|当天)",
    r"(上午|早上|中午|下午|晚上|凌晨|清晨)",
]

NOISE_HEAD = ["原定", "原计划", "定于", "定在", "请记得", "记得", "别忘了", "请", "的", "于", "每"]
NOISE_TRAIL = ["改到", "改为", "改至", "定在", "安排在", "推迟到", "提前到", "将于",
               "约在", "定于", "时间是", "时间", "地点在", "举行", "召开", "开始", "改", "的"]
PREFIXES = ["re:", "fw:", "fw：", "re ", "fw ", "回复:", "回复：", "转发:", "转发：", "答复:", "答复：", "回复 ", "转发 "]


def _clean_clause(text):
    for pat in TIME_TOKENS:
        text = re.sub(pat, " ", text)
    text = re.sub(r"[\s，,。：:、()（）()!！?？…]+", " ", text)
    return text.strip()


def _tidy(t):
    t = (t or "").strip()
    changed = True
    while changed:
        changed = False
        low = t.lower()
        for p in PREFIXES:
            if low.startswith(p) and len(t) - len(p) >= 2:
                t = t[len(p):].strip()
                changed = True
        for w in NOISE_HEAD:
            if t.startswith(w) and len(t) - len(w) >= 2:
                t = t[len(w):].strip()
                changed = True
        for w in NOISE_TRAIL:
            if t.endswith(w) and len(t) - len(w) >= 2:
                t = t[:-len(w)].strip()
                changed = True
    return t


def _good_title(t):
    return 2 <= len(t) <= 24 and "地点" not in t


def _guess_title(src, pos, subject=""):
    """标题优先取邮件主题（最干净），主题不合适时退回时间所在的小句。"""
    subject = (subject or "").strip()
    if subject in ("(无主题)", "无主题", "无标题", "(无标题)"):
        subject = ""
    subj_title = _tidy(_clean_clause(subject))
    if _good_title(subj_title):
        return subj_title[:24]

    line_start = src.rfind("\n", 0, pos) + 1
    line_end = src.find("\n", pos)
    if line_end == -1:
        line_end = len(src)
    line = src[line_start:line_end]
    for sep in ("。", "！", "？", "；", ";", "，", ",", "：", ":"):
        cut = line.find(sep)
        if cut != -1:
            line = line[:cut]
    title = _tidy(_clean_clause(line))
    if not _good_title(title):
        title = subj_title
    if len(title) < 2:
        title = "日程"
    return title[:24]


def next_remind_ts(start_ts, remind_hours=24):
    if not start_ts:
        return None
    return start_ts - remind_hours * 3600
