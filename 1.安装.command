#!/bin/bash
# ================= 邮聊 · Mac 安装脚本（点一次就好） =================
clear
echo "=============================================="
echo "        邮聊 安装向导（点一次就好）"
echo "=============================================="
echo ""

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "【1/3】给程序发「执行许可」..."
chmod -R +x "$DIR"/*.command 2>/dev/null
chmod +x "$DIR/邮聊.app/Contents/MacOS/"* 2>/dev/null
echo "      ✅ 完成"
echo ""

echo "【2/3】撕掉「外来隔离」标签（Mac 对外来程序认生，撕掉就不拦了）..."
xattr -dr com.apple.quarantine "$DIR" 2>/dev/null
echo "      ✅ 完成"
echo ""

echo "【3/3】检查 Python 3（邮聊的发动机）..."
PY=""
for c in python3 /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "      ❌ 这台 Mac 还没有 Python 3"
  echo ""
  echo "      现在帮你弹出安装窗（点「安装」等它装完）；"
  echo "      装完后，再双击一次本安装脚本就可以了。"
  xcode-select --install 2>/dev/null
  read -r -p "按回车键结束..." _dummy
  exit 1
fi
echo "      ✅ $($PY --version 2>&1)"
echo ""

echo "=============================================="
echo "   🎉 安装完成！"
echo "=============================================="
echo ""
echo "   以后使用：双击文件夹里的「邮聊」图标即可"
echo "   （第一次打开会弹出黑色运行窗口 —— 正常，别关它）"
echo ""
read -r -p "现在就帮你打开邮聊？按回车键启动（或直接关掉本窗口）..." _dummy2
open "$DIR/邮聊.app"
