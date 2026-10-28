# -*- coding: utf-8 -*-
"""邮聊 - Microsoft 365 / 学校邮箱 OAuth2 登录（设备码流程 + XOAUTH2）。

为什么需要它：微软对学校/企业邮箱（Exchange Online）已经关掉了“密码登录”，
只能用 OAuth2。用户在浏览器里登录自己的学校账号，我们只拿到令牌，永远见不到密码。

流程：点“开始登录” → 拿到一串登录码 → 用户去 microsoft.com/link 输入 → 拿到令牌 → 自动续期。
"""
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# 测试可用环境变量 MAILCHAT_OAUTH_BASE 指向替身服务；正常运行时用微软官方地址
DEFAULT_BASE = os.environ.get("MAILCHAT_OAUTH_BASE") or "https://login.microsoftonline.com"
# Exchange Online 的收发信权限 + 离线续期权限
SCOPES = ("openid profile offline_access "
          "https://outlook.office.com/IMAP.AccessAsUser.All "
          "https://outlook.office.com/SMTP.Send")


class OAuthError(Exception):
    pass


def endpoints(tenant, base=DEFAULT_BASE):
    root = base.rstrip("/") + "/" + (tenant or "organizations").strip()
    return root + "/oauth2/v2.0/devicecode", root + "/oauth2/v2.0/token"


def _post(url, fields, timeout=30):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"error": "http_%s" % e.code}
        return e.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise OAuthError("连不上微软登录服务器（超时或网络不通）。稍等一分钟再试，或检查网络。")


# ---------- 友好的中文报错 ----------
ERROR_HINTS = [
    ("invalid_client", "客户端身份校验没过（换令牌被拒）。已自动试过“带密钥/不带密钥”两种方式。请看后面的微软原始错误。"),
    ("unauthorized_client", "这个客户端不被允许这种登录方式。建议用「① Thunderbird」（内置密钥），并把微软原始错误发给技术支持。"),
    ("authorization_pending", "还在等你在网页上登录…"),
    ("slow_down", "稍等一下，马上就好…"),
    ("expired_token", "登录码过期了，请重新点「开始登录」。"),
    ("authorization_declined", "你在网页上取消了登录。"),
    ("consent_required", "学校还没批准这个应用 —— 先换一个「公共客户端 ID」再试（①→⑤），都不行再找学校 IT。"),
    ("interaction_required", "学校要求管理员批准或多因素验证 —— 换一个公共客户端 ID 试试，或按网页提示操作。"),
    ("invalid_grant", "登录已失效，请重新点「开始登录」。"),
    ("unsupported_grant_type", "「登录码」方式不被支持 —— 请回到邮聊改用「开始登录（浏览器授权）」。"),
]
AADSTS_HINTS = [
    ("AADSTS65002", "这个公共客户端没被授权碰邮箱（微软“第一方预授权”限制）。请换一个按钮再试 —— ① Office/Outlook 最可能成功，②③ 是第三方客户端不受这个限制。"),
    ("AADSTS65001", "这个应用还没被学校批准。换一个按钮再试（①→④ 顺序）；都不行就把问题发给学校 IT 请他们点一下同意。"),
    ("AADSTS90072", "账号不在这个应用允许的范围里，请确认用的是学校邮箱登录，或换一个公共客户端 ID 再试。"),
    ("AADSTS7000218", "应用注册里没打开「允许公共客户端流」—— 如果是自己注册的应用请去打开它；如果用的是 ①~⑤ 公共客户端就换下一个试试。"),
    ("AADSTS700016", "客户端 ID 不对，或这个应用不在你的学校里 —— 请检查「应用程序(客户端) ID」是不是复制完整了，或换一个公共客户端 ID。"),
    ("AADSTS700038", "客户端 ID 不对（微软找不到这个应用）—— 请回到「应用注册 → 概述」重新复制，或改用 ①~⑤ 公共客户端按钮。"),
    ("AADSTS50126", "在微软页面上账号或密码输错了，重来一次。"),
    ("AADSTS50076", "你的账号开了两步验证，请按网页提示完成。"),
    ("AADSTS50079", "需要先在网页上注册/更新安全验证方式，按提示操作即可。"),
    ("AADSTS53003", "学校的条件访问策略拦下了这个登录 —— 换一个公共客户端 ID 再试，还不行就只能请学校 IT 放行。"),
    ("AADSTS70011", "应用申请的权限有误，请检查应用注册的 API 权限，或换一个公共客户端 ID。"),
    ("AADSTS9002313", "登录请求格式有误，请重新点「开始登录」。"),
]


def friendly_error(payload):
    err = str(payload.get("error") or "")
    desc = str(payload.get("error_description") or payload.get("error_codes") or "")
    for key, hint in AADSTS_HINTS:
        if key in desc:
            return hint
    for key, hint in ERROR_HINTS:
        if err == key:
            return hint
    if err:
        return f"登录失败（{err}）：{desc[:160]}"
    return "登录失败，请重试。"


# ---------- 1. 开始登录：要一串登录码 ----------
def start_device_flow(client_id, tenant="organizations", base=DEFAULT_BASE):
    """返回 {user_code, verification_uri, device_code, expires_in, interval, message}"""
    device_url, _ = endpoints(tenant, base)
    code, body = _post(device_url, {"client_id": client_id, "scope": SCOPES})
    if code != 200 or not body.get("device_code"):
        raise OAuthError(friendly_error(body))
    return {
        "user_code": body.get("user_code"),
        "verification_uri": body.get("verification_uri") or "https://microsoft.com/link",
        "verification_uri_complete": body.get("verification_uri_complete"),
        "device_code": body.get("device_code"),
        "expires_in": int(body.get("expires_in") or 900),
        "interval": max(3, int(body.get("interval") or 5)),
        "message": body.get("message") or "",
    }


# ---------- 2. 轮询：用户登录成功了吗 ----------
def poll_device_token(client_id, device_code, tenant="organizations", base=DEFAULT_BASE):
    """返回 {"status": "pending|ok|error", ...}"""
    _, token_url = endpoints(tenant, base)
    code, body = _post(token_url, {
        "grant_type": "urn:ietf:params:oauth:flow:device_code",
        "client_id": client_id,
        "device_code": device_code,
    })
    if code == 200 and body.get("access_token"):
        return {"status": "ok",
                "access_token": body["access_token"],
                "refresh_token": body.get("refresh_token") or "",
                "expires_in": int(body.get("expires_in") or 3600),
                "scope": body.get("scope") or ""}
    err = str(body.get("error") or "")
    if err in ("authorization_pending", "slow_down"):
        return {"status": "pending"}
    return {"status": "error", "message": friendly_error(body)}


# ---------- 3. 令牌续期 ----------
def refresh_access_token(client_id, refresh_token, tenant="organizations", base=DEFAULT_BASE):
    """续期令牌。和换令牌一样双路重试：先不带密钥（公开客户端），报 invalid_client 再带。"""
    _, token_url = endpoints(tenant, base)
    secret = _client_secret(client_id)
    attempts = [False] + ([True] if secret else [])
    last = None
    for with_secret in attempts:
        fields = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": SCOPES,
        }
        if with_secret:
            fields["client_secret"] = secret
        code, body = _post(token_url, fields)
        if code == 200 and body.get("access_token"):
            return {"access_token": body["access_token"],
                    "refresh_token": body.get("refresh_token") or refresh_token,
                    "expires_in": int(body.get("expires_in") or 3600)}
        last = body
        err = str(body.get("error") or "")
        if err not in ("invalid_client", "unauthorized_client"):
            break
    detail = f"（微软原始错误：{str((last or {}).get('error') or '')} {str((last or {}).get('error_description') or '')[:120]}）"
    raise OAuthError(friendly_error(last or {}) + detail)


def ensure_access_token(oauth_cfg, persist=None, base=DEFAULT_BASE):
    """拿到有效的 access_token：没过期就直接用，过期就用 refresh_token 自动续。"""
    now = time.time()
    if oauth_cfg.get("access_token") and float(oauth_cfg.get("expires_at") or 0) - now > 120:
        return oauth_cfg["access_token"]
    if not oauth_cfg.get("refresh_token"):
        raise OAuthError("还没登录学校账号：请先在「我的 → 邮箱设置」里点「开始登录」。")
    if not oauth_cfg.get("client_id"):
        raise OAuthError("还没填客户端 ID：请到「我的 → 邮箱设置」填写。")
    d = refresh_access_token(oauth_cfg["client_id"], oauth_cfg["refresh_token"],
                             oauth_cfg.get("tenant") or "organizations", base)
    oauth_cfg["access_token"] = d["access_token"]
    oauth_cfg["refresh_token"] = d["refresh_token"]
    oauth_cfg["expires_at"] = now + d["expires_in"]
    if persist:
        persist(oauth_cfg)
    return d["access_token"]


# ---------- 5. 浏览器授权流程（authorization_code + PKCE） ----------
# 部分公共客户端（Thunderbird、Apple 等）不支持“登录码”，只支持浏览器授权。
# Thunderbird 的应用密钥由其官方文档公开标注 “notsecret”，此处照用。
KNOWN_SECRETS = {
    "9e5f94bc-e8a4-4e73-b8be-63364c29d753": "TxRBilcHdC6WGBee]fs?QR:SJ8nI[g82",
}


def _client_secret(client_id):
    return KNOWN_SECRETS.get((client_id or "").lower(), "")


def pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(48)).decode("ascii").rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


def build_auth_url(client_id, tenant, redirect_uri, code_challenge, state, base=DEFAULT_BASE):
    root = endpoints(tenant, base)[1].rsplit("/", 1)[0]  # .../oauth2/v2.0
    q = urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
        "scope": SCOPES, "response_mode": "query", "state": state,
        "code_challenge": code_challenge, "code_challenge_method": "S256",
        "prompt": "select_account",
    })
    return f"{root}/authorize?{q}"


def exchange_code(client_id, tenant, code, redirect_uri, code_verifier, base=DEFAULT_BASE):
    """浏览器授权后用 code 换令牌。

    兼容两种应用形态：公开客户端（纯 PKCE，不带密钥）与机密客户端（需密钥）。
    先不带密钥试，报 invalid_client 再带密钥试（密钥无效不会消耗 code）。
    """
    _, token_url = endpoints(tenant, base)
    secret = _client_secret(client_id)
    attempts = [False] + ([True] if secret else [])
    last = None
    for with_secret in attempts:
        fields = {"grant_type": "authorization_code", "client_id": client_id, "code": code,
                  "redirect_uri": redirect_uri, "code_verifier": code_verifier}
        if with_secret:
            fields["client_secret"] = secret
        code_, body = _post(token_url, fields)
        if code_ == 200 and body.get("access_token"):
            return {"access_token": body["access_token"],
                    "refresh_token": body.get("refresh_token") or "",
                    "expires_in": int(body.get("expires_in") or 3600)}
        last = body
        err = str(body.get("error") or "")
        if err not in ("invalid_client", "unauthorized_client"):
            break  # 换带不带密钥都没用的错误，直接报
    detail = f"（微软原始错误：{str((last or {}).get('error') or '')} {str((last or {}).get('error_description') or '')[:120]}）"
    raise OAuthError(friendly_error(last or {}) + detail)


# ---------- 6. XOAUTH2 认证串 ----------
def xoauth2_raw(email, access_token):
    """IMAP 用：原始认证串（imaplib 会自动 base64，切勿预先编码）。"""
    return f"user={email}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")


def xoauth2_string(email, access_token):
    """SMTP 用：base64 后的认证串（SMTP 的 AUTH 命令要求直接给 base64）。"""
    return base64.b64encode(xoauth2_raw(email, access_token)).decode("ascii")
