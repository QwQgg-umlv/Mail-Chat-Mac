# Mail-Chat for macOS 📮💬🍎

**像聊天一样用邮箱** —— 「邮聊」的 macOS 版本。微信式桌面邮箱客户端：邮件收发、自动翻译、AI 助手、日历自动整理。

## 🚀 安装（一共就两步）

1. 下载 **[Releases](../../releases)** 里的 `MailChat-Mac-v1.0.zip`，拷到 Mac **双击解压**
2. 双击 **「1.安装.command」** → 完成后双击 **「邮聊」图标** 🎉

> 详细步骤见包内「Mac使用说明.md」。首次运行若提示"无法打开"，右键 → 打开（仅一次）。

**要求**：macOS 11+，Python 3（`xcode-select --install` 一键安装，脚本会自动引导）。

## ✨ 功能

| 模块 | 说明 |
|---|---|
| 💬 **聊天式收发** | 邮件变成聊天气泡；双击会话跳到最新未读 |
| 🌍 **自动翻译** | 外语邮件自动翻成中文，一键「看原文」 |
| 🤖 **AI 助手** | 摘要 / 帮写回复 / 提取日程（任意 OpenAI 兼容 API） |
| 📅 **日历自动整理** | 邮件里的时间自动进日历，点击跳回原邮件 |
| 🔍 **Outlook 式搜索** | 全文搜索 + 范围筛选 + 关键词高亮 |
| 👥 **联系人索引** | 微信同款字母导轨 + 星标好友 |
| 🎨 **双色主题** | 科技黑 / 清爽白 |
| 🔒 **本地隐私** | 数据只存本机，无网页版、无云同步 |

## 📦 技术栈

- **Python 标准库**（零第三方依赖）：http.server / imaplib / smtplib / sqlite3
- 前端：原生 HTML / CSS / JS（科技风 UI）
- 邮箱：IMAP IDLE 实时推送 + SMTP；Microsoft 365 OAuth2 登录
- macOS 版为源码 + 启动器形态（`邮聊.app` 双击启动，自动打开浏览器）

## 📁 结构

```
1.安装.command     # 一次性安装脚本（发权限/解隔离/查 Python）
邮聊.app/          # 本体（双击启动）
启动邮聊.command   # 实际运行的启动器
server.py …       # 程序源码（Python 标准库）
static/           # 界面
Mac使用说明.md     # 图文安装手册
```

姊妹项目（Windows 版 + exe）：[Mail-Chat](https://github.com/QwQgg-umlv/Mail-Chat)

## 📄 License

MIT
