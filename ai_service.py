# -*- coding: utf-8 -*-
"""邮聊 - AI 服务：总结 / 翻译 / 代写回复 / 提取日程。

对接任意 OpenAI 兼容接口（OpenAI、DeepSeek、Moonshot、通义、本地 Ollama 等），
只要在“我的 → AI 设置”里填好 base_url / api_key / model 就能用。
"""
import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PROMPTS = {
    "summary": (
        "你是我的邮件助理。请把下面这封邮件总结成三部分，用中文输出：\n"
        "【一句话】用不超过25个字概括这封邮件讲什么。\n"
        "【要点】用2-4条短句列出关键信息（时间、地点、金额、要求等），每条不超过30字。\n"
        "【要我做什么】如果需要我行动，写清做什么、截止时间；不需要就写“无需行动”。\n"
        "只输出这三部分，不要客套话。"
    ),
    "translate": (
        "你是专业翻译。把用户给的内容翻译成{target_lang}。\n"
        "要求：只输出译文本身；保留人名、公司名、专有名词原文并可在括号里注明；"
        "语气自然、符合邮件语境；不要解释、不要加前缀。"
    ),
    "reply": (
        "你是我的邮件秘书，帮我代写回信。\n"
        "规则：\n"
        "1. 先读懂原邮件，再按照我给的“我的意图”写回信；\n"
        "2. 语言与原邮件一致（对方用英文就写英文，用中文就写中文）；\n"
        "3. 语气得体、简洁、像真人写的，不要生硬的公文腔，不要编造我未提供的事实；\n"
        "4. 只输出邮件正文本身（可带称呼和落款），不要输出主题、解释或引号。"
    ),
    "extract_events": (
        "你是日程提取助手。读下面的邮件，找出其中提到的所有时间安排（会议、截止日期、预约、活动等）。\n"
        "严格输出 JSON 数组，每个元素格式：\n"
        '[{"title":"简短中文标题","start":"ISO8601时间","end":"ISO8601时间或null",'
        '"all_day":false,"location":"地点或空","notes":"补充说明"}]\n'
        "规则：只知道日期不知道几点 → all_day 设为 true，end 为 null；"
        "时间按邮件语境推算具体年份；无法确定的年份用当前年份；没有日程就输出 []。\n"
        "只输出 JSON，不要任何其他文字。"
    ),
}


class AIError(Exception):
    pass


def get_prompts(cfg):
    p = dict(DEFAULT_PROMPTS)
    user_p = (cfg.get("prompts") or {})
    for k in p:
        if user_p.get(k):
            p[k] = user_p[k]
    return p


def is_configured(cfg):
    return bool(cfg.get("base_url") and cfg.get("api_key") and cfg.get("model"))


def chat(cfg, system, user, temperature=None, timeout=90):
    """调用 OpenAI 兼容 /chat/completions，返回文本。"""
    if not is_configured(cfg):
        raise AIError("还没有配置 AI：请到「我的 → AI 设置」填 接口地址、API Key、模型名。")
    base = cfg["base_url"].rstrip("/")
    if not base.endswith("/v1") and "/chat/completions" not in base:
        # 允许用户填 https://api.deepseek.com 这种不带 /v1 的地址
        if base.endswith("/v1/"):
            pass
        else:
            base = base + "/v1"
    url = base + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "temperature": cfg.get("temperature", 0.4) if temperature is None else temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + (cfg.get("api_key") or "")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        if e.code in (401, 403):
            raise AIError(f"AI 接口拒绝了密钥（{e.code}）：请检查 API Key 是否填对。{body}")
        if e.code == 404:
            raise AIError(f"AI 接口地址或模型不对（404）：请检查 接口地址 和 模型名。{body}")
        if e.code == 429:
            raise AIError("AI 接口限流或余额不足（429），稍后再试。" + body)
        raise AIError(f"AI 接口出错（{e.code}）：{body}")
    except urllib.error.URLError as e:
        raise AIError(f"连不上 AI 接口：{e.reason}。请检查网络或 接口地址 是否正确。")
    except Exception as e:
        raise AIError(f"AI 调用失败：{e}")

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise AIError("AI 返回的内容我看不懂，请换个模型试试。")


def summarize(cfg, subject, body):
    prompts = get_prompts(cfg)
    user = f"邮件主题：{subject or '(无主题)'}\n\n邮件正文：\n{body[:6000]}"
    return chat(cfg, prompts["summary"], user)


# ---------- 翻译 ----------
def looks_chinese(text):
    """文本是否已经是中文（是就不用翻译，省事省钱）。"""
    if not text:
        return True
    han = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    alpha = sum(1 for ch in text if ch.isalpha())
    return alpha > 0 and han / alpha > 0.5


def free_translate(text, target="zh-CN"):
    """免费翻译引擎（谷歌翻译公开接口，不需要 key）。

    失败返回 ""，由上层决定是否改用 AI。
    """
    if not text or not text.strip():
        return ""
    chunks = [text[i:i + 4500] for i in range(0, len(text), 4500)][:4]
    out = []
    for chunk in chunks:
        url = ("https://translate.googleapis.com/translate_a/single?client=gtx"
               "&sl=auto&tl=" + urllib.parse.quote(target) + "&dt=t&q=" + urllib.parse.quote(chunk))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        out.append("".join(seg[0] for seg in (data[0] or []) if seg and seg[0]))
    return "\n".join(out).strip()


def auto_translate(text):
    """收到邮件后的自动翻译：免费引擎优先（不花 AI 额度），失败再用 AI。"""
    if looks_chinese(text):
        return ""
    try:
        t = free_translate(text)
        if t:
            return t
    except Exception:
        pass
    return ""


def translate(cfg, text, target="中文"):
    if not text.strip():
        return ""
    prompts = get_prompts(cfg)
    system = prompts["translate"].replace("{target_lang}", target_lang)
    return chat(cfg, system, text[:6000])


def draft_reply(cfg, subject, body, intent, extra=""):
    prompts = get_prompts(cfg)
    user = (f"【原邮件主题】{subject or '(无主题)'}\n"
            f"【原邮件正文】\n{body[:5000]}\n\n"
            f"【我的意图】{intent or '礼貌回复，表示收到'}\n")
    if extra:
        user += f"【补充要求】{extra}\n"
    return chat(cfg, prompts["reply"], user)


def extract_events(cfg, subject, body, now_iso=""):
    """返回 (events_list, error)。events_list 为 dict 列表；解析失败时 error 有值。"""
    import re
    prompts = get_prompts(cfg)
    user = f"现在时间：{now_iso}\n邮件主题：{subject or '(无主题)'}\n\n邮件正文：\n{body[:6000]}"
    raw = chat(cfg, prompts["extract_events"], user, temperature=0.1)
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return [], "AI 没有返回可用的日程格式"
    try:
        items = json.loads(m.group(0))
    except Exception:
        return [], "AI 返回的日程 JSON 解析失败"
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append({
            "title": str(it.get("title") or "未命名日程"),
            "start": it.get("start"),
            "end": it.get("end"),
            "all_day": bool(it.get("all_day")),
            "location": str(it.get("location") or ""),
            "notes": str(it.get("notes") or ""),
        })
    return out, None
