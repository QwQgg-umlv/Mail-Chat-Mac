#!/bin/bash
# ============ 邮聊 for macOS · 双击启动 ============
cd "$(dirname "$0")" || exit 1
clear
echo "=============================================="
echo "  邮聊 正在启动…"
echo "  （启动后会自动打开浏览器，这个窗口别关）"
echo "=============================================="

# 找一个可用的 Python 3
PY=""
for c in python3 /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo ""
  echo "❌ 这台 Mac 还没有安装 Python 3（邮聊靠它运行）"
  echo ""
  echo "安装很简单（二选一）："
  echo "  方法A：点左上角  → 打开「终端」→ 粘贴这行回车："
  echo "         xcode-select --install"
  echo "  方法B：浏览器打开 https://www.python.org/downloads/"
  echo "         下载 macOS 版安装包，双击安装"
  echo ""
  read -r -p "装好以后，按回车键重试..." _dummy
  exec "$0"
fi

echo "✅ Python: $($PY --version 2>&1)"
echo ""
exec "$PY" server.py
