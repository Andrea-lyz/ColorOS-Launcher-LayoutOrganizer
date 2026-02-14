#!/usr/bin/env python3
"""
ColorOS 桌面图标分类整理工具 — PyInstaller 打包脚本
================================================

用法:
  1. 确保已安装依赖:
       pip install pyinstaller requests openai

  2. 下载 Android SDK Platform-Tools 并解压到本项目下:
       https://developer.android.com/tools/releases/platform-tools
     解压后保证目录结构为:
       Backup/
         platform-tools/
           adb.exe
           AdbWinApi.dll
           AdbWinUsbApi.dll

  3. 运行打包:
       python build_exe.py

  产出物:
       dist/ColorOS图标分类整理工具.exe   (单文件，内含 ADB + Python + requests + openai)

注意事项:
  · 打出来的 exe 约 15~25 MB（取决于 Python 版本和 UPX 压缩）
  · 运行时会将 ADB、Python 模块解压到临时目录 (_MEIPASS)
  · 首次启动约需几秒钟解压
"""

import os
import sys
import shutil
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 检查 platform-tools ──────────────────────────────────────────────────
PT_DIR = os.path.join(SCRIPT_DIR, "platform-tools")
ADB_EXE = os.path.join(PT_DIR, "adb.exe")

if not os.path.isfile(ADB_EXE):
    print("❌ 未找到 platform-tools/adb.exe")
    print()
    print("请先下载 Android SDK Platform-Tools 并解压到本项目目录:")
    print("  https://developer.android.com/tools/releases/platform-tools")
    print()
    print("解压后确保存在:")
    print(f"  {ADB_EXE}")
    sys.exit(1)

# ── 需要内嵌的 ADB 文件 ──────────────────────────────────────────────────
# 最小必须文件 (Windows):  adb.exe  AdbWinApi.dll  AdbWinUsbApi.dll
ADB_FILES = []
for name in os.listdir(PT_DIR):
    fp = os.path.join(PT_DIR, name)
    if os.path.isfile(fp):
        ADB_FILES.append(fp)

print(f"✅ 找到 platform-tools，共 {len(ADB_FILES)} 个文件")

# ── 检查 PyInstaller ─────────────────────────────────────────────────────
try:
    import PyInstaller
    print(f"✅ PyInstaller {PyInstaller.__version__}")
except ImportError:
    print("❌ 未安装 PyInstaller，正在安装 …")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

# ── 检查 requests ────────────────────────────────────────────────────────
try:
    import requests
    print(f"✅ requests {requests.__version__}")
except ImportError:
    print("❌ 未安装 requests，正在安装 …")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])

# ── 构造 PyInstaller 参数 ────────────────────────────────────────────────
ENTRY = os.path.join(SCRIPT_DIR, "layout_patch_main.py")
ICON = os.path.join(SCRIPT_DIR, "icon.ico")  # 可选图标

# --add-data 打包附属 Python 脚本
DATA_SCRIPTS = [
    os.path.join(SCRIPT_DIR, "fetch_categories.py"),
    os.path.join(SCRIPT_DIR, "reorganize_layout_oneclick.py"),
]

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--console",          # 控制台程序（需要交互输入）
    "--clean",
    "--name", "ColorOS图标分类整理工具",
]

# 添加图标（如果有）
if os.path.isfile(ICON):
    cmd += ["--icon", ICON]

# 添加 ADB 文件
for f in ADB_FILES:
    # 格式: --add-data "源路径;目标目录"  (Windows 用 ;)
    cmd += ["--add-data", f"{f};platform-tools"]

# 添加附属 Python 脚本
for f in DATA_SCRIPTS:
    if os.path.isfile(f):
        cmd += ["--add-data", f"{f};."]

# 隐式导入
cmd += [
    "--hidden-import", "requests",
    "--hidden-import", "sqlite3",
    "--hidden-import", "xml.etree.ElementTree",
    "--hidden-import", "tarfile",
    "--hidden-import", "json",
    "--hidden-import", "urllib3",
    "--hidden-import", "charset_normalizer",
    "--hidden-import", "certifi",
    "--hidden-import", "idna",
    "--hidden-import", "openai",
    "--hidden-import", "httpx",
    "--hidden-import", "anyio",
    "--hidden-import", "sniffio",
    "--hidden-import", "distro",
    "--hidden-import", "pydantic",
]

# 入口文件
cmd.append(ENTRY)

print()
print("=" * 60)
print("  开始打包 …")
print("=" * 60)
print()
print("命令:", " ".join(cmd))
print()

r = subprocess.run(cmd, cwd=SCRIPT_DIR)

if r.returncode == 0:
    dist = os.path.join(SCRIPT_DIR, "dist", "ColorOS图标分类整理工具.exe")
    if os.path.isfile(dist):
        size_mb = os.path.getsize(dist) / 1024 / 1024
        print()
        print("=" * 60)
        print(f"  ✅ 打包成功！")
        print(f"  📦 {dist}")
        print(f"  📏 大小: {size_mb:.1f} MB")
        print("=" * 60)
    else:
        print("⚠️  打包命令执行完毕但未找到输出文件")
else:
    print(f"❌ 打包失败，退出码: {r.returncode}")
    sys.exit(1)
