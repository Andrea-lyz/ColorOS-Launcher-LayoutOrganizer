#!/usr/bin/env python3
"""
ColorOS 桌面布局一键修补工具 — 全流程主控脚本
==============================================
由 layout_patch.bat 或 PyInstaller 打包的 exe 启动，提供交互式菜单:

  0.  环境检测 (Python / ADB / requests / openai)
  1.  从手机拉取备份到本地
  2.  从备份数据库提取应用列表
  3.  多渠道云端自动分类 (Google Play + 应用宝)
  3A. AI 智能分类 (OpenAI 兼容 API)
  4.  交互式处理未分类应用
  5.  生成新布局 → 写入 DB + XML + tar
  6.  推回手机 (完整替换备份)

  S.  查看分类统计
  C.  AI 分类设置 (URL / Key / Model)
  R.  恢复手机上备份为原始状态

依赖:  fetch_categories.py, reorganize_layout_oneclick.py (同目录)
       pip install requests openai  (分别用于云端分类和 AI 分类)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import signal

# ============================================================================
#  UTF-8 输出
# ============================================================================
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

# ============================================================================
#  PyInstaller 打包支持
# ============================================================================

def _is_frozen():
    """是否运行在 PyInstaller 打包的 exe 中"""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def _get_bundle_dir():
    """获取 PyInstaller 解压后的临时资源目录（_MEIPASS），或开发时的脚本目录"""
    if _is_frozen():
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _get_runtime_dir():
    """获取运行时工作目录：打包时用 exe 所在目录，开发时用脚本所在目录"""
    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


# ============================================================================
#  常量
# ============================================================================

# BUNDLE_DIR: PyInstaller 解压后的临时目录（含打包进的 adb、.py 文件）
BUNDLE_DIR = _get_bundle_dir()

# SCRIPT_DIR: 运行时的工作目录（exe 所在位置 或 脚本目录）
SCRIPT_DIR = _get_runtime_dir()

# 内置 ADB 路径 (PyInstaller 打包后在 _MEIPASS/platform-tools/ 下)
BUNDLED_ADB = os.path.join(BUNDLE_DIR, "platform-tools", "adb.exe")

# 最终使用的 ADB 路径
ADB_PATH = BUNDLED_ADB if os.path.isfile(BUNDLED_ADB) else "adb"

# 手机上可能的备份根路径 (取决于 ColorOS 版本)
PHONE_BACKUP_ROOTS = [
    "/storage/emulated/0/Android/data/com.oneplus.backuprestore/Backup",
    "/storage/emulated/0/Android/data/com.coloros.backuprestore/Backup",
]

# 本地工作目录
LOCAL_WORK_DIR = os.path.join(SCRIPT_DIR, "_phone_backup")


# ============================================================================
#  工具函数
# ============================================================================

def color(text, code):
    """ANSI 颜色 (Windows Terminal / WT 支持)"""
    return f"\033[{code}m{text}\033[0m"

def green(t):  return color(t, "32")
def red(t):    return color(t, "31")
def yellow(t): return color(t, "33")
def cyan(t):   return color(t, "36")
def bold(t):   return color(t, "1")


def wait_for_exit(message="按任意键退出…", timeout_sec=10):
    """
    等待用户输入或超时自动退出。
    
    参数:
      message: 提示信息
      timeout_sec: 超时秒数 (Windows 不支持超时，使用轮询方案)
    
    机制:
      - 首先尝试 input() — 用户按回车/任意键时立即退出
      - 如果 input() 卡住，通过 Ctrl+C 可强制退出
      - 作为备选，定时检查是否用户已按下 Ctrl+C
    """
    print(f"\n  {message}")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass


def bold(t):   return color(t, "1")


def print_header(title):
    w = 56
    print()
    print(cyan("═" * w))
    pad = (w - len(title) - 4) // 2
    print(cyan("║") + " " * pad + bold(title) + " " * (w - pad - len(title) - 4) + cyan("  ║"))
    print(cyan("═" * w))


def print_step(n, title):
    print(f"\n{cyan(f'[步骤 {n}]')} {bold(title)}")
    print(cyan("─" * 50))


def run_cmd(args, timeout=30, check=True, capture=True):
    """运行外部命令，返回 CompletedProcess"""
    try:
        r = subprocess.run(
            args,
            capture_output=capture,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if check and r.returncode != 0:
            return None
        return r
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None


def adb(*args, timeout=30):
    """执行 adb 命令（使用内置或系统 ADB）"""
    return run_cmd([ADB_PATH] + list(args), timeout=timeout)


def adb_shell(cmd, timeout=30):
    """执行 adb shell 命令"""
    return run_cmd([ADB_PATH, "shell", cmd], timeout=timeout, check=False)


# ============================================================================
#  环境检测
# ============================================================================

def check_environment():
    """检测 Python / ADB / requests / openai，返回是否全部通过"""
    print_step("0", "环境检测")
    ok = True

    # 打包模式提示
    if _is_frozen():
        print(f"  📦 运行模式: PyInstaller 打包")
        print(f"  📁 资源目录: {BUNDLE_DIR[:60]}...")
    else:
        print(f"  🐍 运行模式: 脚本直接运行")

    # Python 版本
    ver = sys.version.split()[0]
    if sys.version_info >= (3, 8):
        print(f"  ✅ Python {ver}")
    else:
        print(f"  ❌ Python {ver} — 需要 3.8+")
        ok = False

    # ADB
    if os.path.isfile(BUNDLED_ADB):
        print(f"  ✅ ADB (内置: {os.path.basename(BUNDLED_ADB)})")
    r = run_cmd([ADB_PATH, "version"], check=False)
    if r and r.returncode == 0:
        adb_ver = r.stdout.strip().splitlines()[0] if r.stdout else "unknown"
        print(f"  ✅ {adb_ver}")
    else:
        print(f"  ❌ 未找到 adb — 请安装 Android SDK Platform Tools 并添加到 PATH")
        ok = False

    # requests
    try:
        import requests
        print(f"  ✅ requests {requests.__version__}")
    except ImportError:
        print(f"  ⚠️  requests 未安装 (步骤3云端分类需要)")
        if not _is_frozen():
            print(f"     安装: {cyan('pip install requests')}")

    # openai
    try:
        import openai
        print(f"  ✅ openai {openai.__version__}")
    except ImportError:
        print(f"  ⚠️  openai 未安装 (步骤3A AI分类需要)")
        if not _is_frozen():
            print(f"     安装: {cyan('pip install openai')}")

    # fetch_categories.py / reorganize_layout_oneclick.py
    # 打包时这些文件在 BUNDLE_DIR，开发时在 SCRIPT_DIR
    for name in ("fetch_categories.py", "reorganize_layout_oneclick.py"):
        # 优先查 BUNDLE_DIR（打包环境），再查 SCRIPT_DIR（开发环境）
        path = os.path.join(BUNDLE_DIR, name)
        if not os.path.isfile(path):
            path = os.path.join(SCRIPT_DIR, name)
        if os.path.isfile(path):
            print(f"  ✅ {name}")
        else:
            print(f"  ❌ 缺少 {name}")
            ok = False

    return ok


# ============================================================================
#  ADB 连接 + 设备选择
# ============================================================================

def _parse_adb_devices():
    """
    解析 adb devices 输出，返回 [(serial, state), ...]
    state: 'device' | 'unauthorized' | 'offline' | 'no permissions' | ...
    """
    r = run_cmd([ADB_PATH, "devices"], check=False)
    if not r:
        return []
    results = []
    for line in r.stdout.strip().splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            results.append((parts[0], parts[1]))
    return results


def _kill_adb_server():
    """终止 ADB 服务端（清理残留连接）"""
    print(f"  🔄 正在终止 ADB 服务 …")
    run_cmd([ADB_PATH, "kill-server"], check=False, timeout=10)
    time.sleep(1)


def _start_adb_server():
    """启动 ADB 服务端"""
    print(f"  🔄 正在启动 ADB 服务 …")
    run_cmd([ADB_PATH, "start-server"], check=False, timeout=10)
    time.sleep(2)


def _wait_for_authorization(serial, max_wait=30):
    """
    等待手机端授权 USB 调试。
    返回 True 如果在 max_wait 秒内授权成功。
    """
    print(f"\n  📱 {yellow('手机上弹出了 USB 调试授权对话框！')}")
    print(f"  📱 {bold('请在手机上点击「允许 USB 调试」')}")
    print(f"     (建议勾选「一律允许」以避免每次连接都需要授权)")
    print(f"\n  ⏳ 等待授权中 (最多 {max_wait} 秒) …", end="", flush=True)

    for i in range(max_wait):
        time.sleep(1)
        print(".", end="", flush=True)
        r = run_cmd([ADB_PATH, "devices"], check=False, timeout=5)
        if not r:
            continue
        for line in r.stdout.strip().splitlines()[1:]:
            parts = line.strip().split("\t")
            if len(parts) >= 2 and parts[0] == serial and parts[1] == "device":
                print(f"\n  ✅ {green('授权成功！')}")
                return True
    print(f"\n  ❌ {red('授权超时')}")
    return False


def connect_device():
    """
    健壮的 ADB 设备连接流程：
    1. 先尝试直接检测设备
    2. 处理 unauthorized（等待手机端授权）
    3. 处理 offline / 残留设备（重启 ADB 服务）
    4. 多设备时让用户选择
    """
    print_step("①", "ADB 连接设备")

    # ── 第一次尝试 ──
    all_devs = _parse_adb_devices()

    if not all_devs:
        # 完全没有设备，尝试重启 ADB 服务后再试
        print(f"  ⚠️  未检测到任何设备，尝试重启 ADB 服务 …")
        _kill_adb_server()
        _start_adb_server()
        all_devs = _parse_adb_devices()

    if not all_devs:
        print(f"  {red('❌ 未检测到任何已连接的设备')}")
        print(f"  请确认:")
        print(f"    · 手机已通过 USB 线连接到电脑")
        print(f"    · 手机「设置 → 开发者选项 → USB 调试」已开启")
        print(f"    · USB 连接模式选择了「文件传输 (MTP)」")
        return None

    # ── 分类设备状态 ──
    ready_devs = []       # state == 'device'
    unauthorized_devs = [] # state == 'unauthorized'
    offline_devs = []      # state == 'offline'
    other_devs = []        # 其他异常状态

    for serial, state in all_devs:
        if state == "device":
            ready_devs.append(serial)
        elif state == "unauthorized":
            unauthorized_devs.append(serial)
        elif state == "offline":
            offline_devs.append(serial)
        else:
            other_devs.append((serial, state))

    # 显示全部设备状态
    print(f"  检测到 {len(all_devs)} 个设备:")
    for serial, state in all_devs:
        icon = {"device": "✅", "unauthorized": "🔒", "offline": "💤"}.get(state, "❓")
        state_desc = {
            "device": green("已授权"),
            "unauthorized": yellow("未授权 (需要在手机上确认)"),
            "offline": red("离线"),
        }.get(state, red(state))
        print(f"    {icon} {serial}  [{state_desc}]")

    # ── 处理 offline 设备：重启 ADB 服务清理残留 ──
    if offline_devs and not ready_devs:
        print(f"\n  ⚠️  发现 {len(offline_devs)} 个离线设备，可能是 ADB 服务残留")
        choice = input(f"  是否重启 ADB 服务来清理？(Y/n): ").strip().lower()
        if choice != "n":
            _kill_adb_server()
            _start_adb_server()
            # 重新扫描
            all_devs = _parse_adb_devices()
            ready_devs = [s for s, st in all_devs if st == "device"]
            unauthorized_devs = [s for s, st in all_devs if st == "unauthorized"]
            offline_devs = [s for s, st in all_devs if st == "offline"]

            if ready_devs:
                print(f"  ✅ 重启后发现 {len(ready_devs)} 个可用设备")
            elif unauthorized_devs:
                print(f"  🔒 重启后发现 {len(unauthorized_devs)} 个未授权设备")
            else:
                print(f"  {red('❌ 重启后仍无可用设备')}")
                return None

    # ── 处理 unauthorized 设备 ──
    if unauthorized_devs and not ready_devs:
        # 所有设备都是未授权状态
        if len(unauthorized_devs) == 1:
            serial = unauthorized_devs[0]
            if _wait_for_authorization(serial):
                ready_devs.append(serial)
            else:
                print(f"\n  💡 提示: 如果手机上没有弹出授权对话框，请尝试:")
                print(f"     1. 在手机「设置 → 开发者选项」中撤销 USB 调试授权")
                print(f"     2. 拔插 USB 线重新连接")
                print(f"     3. 确保 USB 连接模式为「文件传输 (MTP)」")
                return None
        else:
            print(f"\n  🔒 有多个未授权设备，请逐一在手机上授权 USB 调试")
            for serial in unauthorized_devs:
                print(f"\n  ── 等待设备 {serial} 授权 ──")
                if _wait_for_authorization(serial):
                    ready_devs.append(serial)

    if not ready_devs:
        print(f"\n  {red('❌ 没有已授权的可用设备')}")
        return None

    # ── 单设备 → 直接使用 ──
    if len(ready_devs) == 1:
        serial = ready_devs[0]
        model_r = run_cmd([ADB_PATH, "-s", serial, "shell", "getprop", "ro.product.model"], check=False)
        model = model_r.stdout.strip() if model_r and model_r.stdout else "unknown"
        print(f"\n  ✅ 已连接: {green(serial)} ({model})")
        return serial

    # ── 多设备 → 用户选择 ──
    print(f"\n  检测到 {len(ready_devs)} 台可用设备:\n")
    for i, serial in enumerate(ready_devs, 1):
        model_r = run_cmd([ADB_PATH, "-s", serial, "shell", "getprop", "ro.product.model"], check=False)
        model = model_r.stdout.strip() if model_r and model_r.stdout else "unknown"
        print(f"    {cyan(str(i))}. {serial}  ({model})")

    while True:
        try:
            choice = input(f"\n  请选择设备 [1-{len(ready_devs)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(ready_devs):
                return ready_devs[idx]
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        print(f"  ⚠️  无效选项")


# ============================================================================
#  发现手机备份路径
# ============================================================================

def find_phone_backup(serial):
    """在手机上查找备份路径，返回 (backup_root, timestamp) 或 (None, None)"""
    print_step("②", "定位手机备份")

    for root in PHONE_BACKUP_ROOTS:
        r = run_cmd([ADB_PATH, "-s", serial, "shell", f"ls {root}/Data/ 2>/dev/null"], check=False)
        if not r or r.returncode != 0 or not r.stdout.strip():
            continue
        timestamps = sorted(r.stdout.strip().splitlines(), reverse=True)
        if timestamps:
            ts = timestamps[0].strip()
            # 验证 Layout 目录存在
            check = run_cmd(
                [ADB_PATH, "-s", serial, "shell", f"ls {root}/Data/{ts}/Layout/ 2>/dev/null"],
                check=False)
            if check and check.returncode == 0:
                print(f"  ✅ 备份路径: {green(root)}")
                print(f"     时间戳:   {green(ts)}")
                # 显示备份大小
                size_r = run_cmd(
                    [ADB_PATH, "-s", serial, "shell", f"du -sh {root}/"],
                    check=False)
                if size_r and size_r.stdout:
                    size = size_r.stdout.strip().split()[0]
                    print(f"     大小:     {size}")
                return root, ts

    print(f"  {red('❌ 未找到手机备份')}")
    print(f"  请先在手机上用「手机搬家 / PhoneClone」创建一份备份")
    return None, None


# ============================================================================
#  拉取备份到本地
# ============================================================================

def pull_backup(serial, phone_backup_root):
    """从手机拉取整个 Backup 目录到本地 (跳过无权限文件)"""
    print_step("1", "拉取备份到本地")

    local_backup = os.path.join(LOCAL_WORK_DIR, "Backup")

    if os.path.isdir(local_backup):
        print(f"  ⚠️  本地已存在: {os.path.relpath(local_backup, SCRIPT_DIR)}")
        choice = input(f"     覆盖？(y/N): ").strip().lower()
        if choice != "y":
            print(f"  ✅ 使用现有本地备份")
            return local_backup
        print(f"  🗑️  清理旧文件 …")
        shutil.rmtree(local_backup, ignore_errors=True)

    os.makedirs(local_backup, exist_ok=True)

    # 先尝试修复权限 (需要 root 或文件属于 shell 用户)
    adb_shell(f"chmod -R a+r {phone_backup_root}/ 2>/dev/null", timeout=10)

    # 列举手机上的所有文件
    print(f"  📋 扫描手机文件列表 …")
    list_r = adb_shell(
        f"find {phone_backup_root}/ -type f 2>/dev/null",
        timeout=30)
    if not list_r or not list_r.stdout.strip():
        print(f"  {red('❌ 无法列举文件')}")
        return None

    all_files = [l.strip() for l in list_r.stdout.strip().splitlines() if l.strip()]
    print(f"  📄 共 {len(all_files)} 个文件")

    # 逐文件拉取 (跳过无权限的)
    print(f"  📥 正在拉取 …")
    t0 = time.time()
    pulled = 0
    skipped = 0
    for remote_path in all_files:
        # 计算本地路径: 去掉 phone_backup_root 前缀
        rel = remote_path[len(phone_backup_root):]
        if rel.startswith("/"):
            rel = rel[1:]
        local_path = os.path.join(local_backup, rel)
        local_dir = os.path.dirname(local_path)
        os.makedirs(local_dir, exist_ok=True)

        r = subprocess.run(
            [ADB_PATH, "-s", serial, "pull", remote_path, local_path],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0:
            pulled += 1
        else:
            skipped += 1
            # 如果是 0 字节 journal 文件，创建空文件
            if remote_path.endswith("-journal") or remote_path.endswith(".nomedia"):
                with open(local_path, "wb") as f:
                    pass
                pulled += 1
                skipped -= 1

    elapsed = time.time() - t0

    # 统计大小
    total = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, fnames in os.walk(local_backup)
        for f in fnames
    )
    print(f"  ✅ 拉取完成: {pulled} 个文件, {total / 1024 / 1024:.1f} MB, 耗时 {elapsed:.1f}s")
    if skipped:
        print(f"  ⚠️  跳过 {skipped} 个无权限文件 (通常是空 journal 文件，不影响)")
    return local_backup


# ============================================================================
#  从数据库提取包名 (调用 fetch_categories.py --from-db)
# ============================================================================

def _find_script(name):
    """查找附属脚本路径 (打包时在 BUNDLE_DIR，开发时在 SCRIPT_DIR)"""
    for d in (BUNDLE_DIR, SCRIPT_DIR):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return os.path.join(SCRIPT_DIR, name)  # fallback


def _run_script_inprocess(script_path, argv_args, cwd=None):
    """
    在当前进程中执行附属 .py 脚本（替代 subprocess 调用）。
    PyInstaller 打包后 sys.executable 指向 exe 本身，
    不能再用 subprocess.run([sys.executable, script, ...]) 启动子脚本，
    否则会重新执行 exe 主程序。

    原理：临时替换 sys.argv，用 exec() 执行脚本文件。
    返回 True 表示成功，False 表示失败。
    """
    saved_argv = sys.argv
    saved_cwd = os.getcwd()
    try:
        # 模拟命令行: python script.py --arg1 --arg2 ...
        sys.argv = [script_path] + list(argv_args)
        if cwd:
            os.chdir(cwd)
        with open(script_path, "r", encoding="utf-8") as f:
            code = f.read()
        # 使用独立的全局命名空间执行，避免污染当前命名空间
        script_globals = {
            "__file__": script_path,
            "__name__": "__main__",
            "__builtins__": __builtins__,
        }
        exec(compile(code, script_path, "exec"), script_globals)
        return True
    except SystemExit as e:
        # argparse 的 --help 或错误会抛 SystemExit
        return (e.code or 0) == 0
    except KeyboardInterrupt:
        # 用户按了 Ctrl+C —— 子脚本内部若有自己的 KeyboardInterrupt 处理
        # 则不会走到这里；走到这里说明中断发生在未被脚本捕获的位置。
        # 视为正常中断，不要让它冒泡到主循环导致整个程序退出。
        print(f"\n  ⚠️  操作已中断")
        return False
    except Exception as e:
        print(f"  ❌ 执行 {os.path.basename(script_path)} 出错: {e}")
        return False
    finally:
        sys.argv = saved_argv
        os.chdir(saved_cwd)


def step_extract_packages(work_dir):
    """步骤2: 从备份 DB 提取包名 + 应用名"""
    print_step("2", "从备份数据库提取应用列表")

    script = _find_script("fetch_categories.py")
    if not _run_script_inprocess(script, ["--from-db", "--workdir", work_dir], cwd=work_dir):
        return False

    # 如果脚本目录下有已有的 app_categories.json，提示用户是否复用
    existing_config = os.path.join(SCRIPT_DIR, "app_categories.json")
    work_config = os.path.join(work_dir, "app_categories.json")
    if os.path.isfile(existing_config) and existing_config != work_config:
        # 检查已有配置是否有实际分类数据
        try:
            with open(existing_config, "r", encoding="utf-8") as f:
                raw = f.read()
            clean = re.sub(r'//[^\n]*', '', raw)
            cfg = json.loads(clean)
            n_cats = len(cfg.get("app_categories", {}))
            if n_cats > 0:
                print(f"\n  💡 发现已有分类配置 ({n_cats} 个应用已分类)")
                use_existing = input(f"     是否复用？(Y/n): ").strip().lower()
                if use_existing != "n":
                    shutil.copy2(existing_config, work_config)
                    print(f"  ✅ 已复制已有分类配置到工作目录")
        except Exception:
            pass

    return True


# ============================================================================
#  云端分类 (调用 fetch_categories.py --classify)
# ============================================================================

def step_classify(work_dir):
    """步骤3: 多渠道云端自动分类"""
    print_step("3", "多渠道云端自动分类 (Google Play + 应用宝)")

    # 检查 packages.txt 是否存在
    pkg_file = os.path.join(work_dir, "packages.txt")
    if not os.path.isfile(pkg_file):
        print(f"  ⚠️  未找到 packages.txt — 请先完成步骤2")
        return False

    try:
        import requests
    except ImportError:
        print(f"  ⚠️  跳过 (需要 requests 库)")
        return True  # 非致命

    script = _find_script("fetch_categories.py")
    return _run_script_inprocess(script, ["--classify", "--workdir", work_dir], cwd=work_dir)


def step_classify_ai(work_dir):
    """步骤3A: AI 智能分类 (OpenAI 兼容 API)"""
    print_step("3A", "AI 智能分类")

    # 检查 packages.txt 是否存在
    pkg_file = os.path.join(work_dir, "packages.txt")
    if not os.path.isfile(pkg_file):
        print(f"  ⚠️  未找到 packages.txt — 请先完成步骤2")
        return False

    script = _find_script("fetch_categories.py")
    return _run_script_inprocess(script, ["--classify-ai", "--workdir", work_dir], cwd=work_dir)


def step_ai_setup(work_dir):
    """配置 AI API 参数"""
    script = _find_script("fetch_categories.py")
    return _run_script_inprocess(script, ["--ai-setup", "--workdir", work_dir], cwd=work_dir)


# ============================================================================
#  交互式分类
# ============================================================================

def step_interactive(work_dir):
    """步骤4: 交互式处理未分类应用"""
    print_step("4", "交互式处理未分类应用")

    config_file = os.path.join(work_dir, "app_categories.json")
    if not os.path.isfile(config_file):
        print(f"  ⚠️  未找到 app_categories.json — 请先完成步骤2~3")
        return False

    # 统计未分类数量
    with open(config_file, "r", encoding="utf-8") as f:
        raw = f.read()
    # 简单去注释
    clean = re.sub(r'//[^\n]*', '', raw)
    try:
        config = json.loads(clean)
    except json.JSONDecodeError:
        print(f"  ⚠️  JSON 解析失败")
        return False

    unclassified = config.get("unclassified", [])
    classified = config.get("app_categories", {})
    total = len(classified) + len(unclassified)

    print(f"  已分类: {green(str(len(classified)))} / {total}")
    print(f"  未分类: {yellow(str(len(unclassified)))}")

    if not unclassified:
        print(f"  ✅ 全部已分类，无需操作")
        return True

    choice = input(f"\n  是否进入交互式分类？(Y/n): ").strip().lower()
    if choice == "n":
        print(f"  ⏭️  跳过 (未分类应用将放入「其他」文件夹)")
        return True

    script = _find_script("fetch_categories.py")
    return _run_script_inprocess(script, ["--interactive", "--workdir", work_dir], cwd=work_dir)


# ============================================================================
#  补充应用名
# ============================================================================

def step_enrich_names(work_dir):
    """补充应用名注释"""
    script = _find_script("fetch_categories.py")
    return _run_script_inprocess(script, ["--enrich-names", "--workdir", work_dir], cwd=work_dir)


# ============================================================================
#  生成布局 (调用 reorganize_layout_oneclick.py)
# ============================================================================

def step_reorganize(work_dir):
    """步骤5: 生成新布局"""
    print_step("5", "生成新布局 → 写入 DB + XML + tar")

    config_file = os.path.join(work_dir, "app_categories.json")
    if not os.path.isfile(config_file):
        print(f"  ⚠️  未找到 app_categories.json — 请先完成分类步骤")
        return False

    script = _find_script("reorganize_layout_oneclick.py")
    return _run_script_inprocess(script, ["--workdir", work_dir], cwd=work_dir)


# ============================================================================
#  推回手机
# ============================================================================

def push_backup(serial, phone_backup_root, local_backup_dir):
    """将修补后的备份推回手机 (完整替换)"""
    print_step("6", "推回手机 (完整替换)")

    print(f"  ⚠️  这将 {bold('完全覆盖')} 手机上的备份:")
    print(f"     {phone_backup_root}/")
    print()
    confirm = input(f"  确认推送？(y/N): ").strip().lower()
    if confirm != "y":
        print(f"  ⏭️  已取消推送")
        return False

    # 先清空手机上的备份目录内容
    print(f"  🗑️  清理手机上旧备份 …")
    adb_shell(f"rm -rf {phone_backup_root}/Data/", timeout=30)
    adb_shell(f"rm -rf {phone_backup_root}/.Preview/", timeout=10)
    adb_shell(f"rm -rf {phone_backup_root}/PhoneClone/", timeout=10)

    # 只推送原始备份结构中的目录/文件，跳过工具生成的辅助文件
    SKIP_ITEMS = {"packages.txt", "app_categories.json", "__pycache__"}

    # 清理 Layout/data/ (tar 解压产物，已重新打包到 tar，不需要推上去)
    import glob as _glob
    for data_dir in _glob.glob(os.path.join(local_backup_dir, "Data", "*", "Layout", "data")):
        if os.path.isdir(data_dir):
            print(f"  🗑️  清理本地 tar 解压产物: {os.path.relpath(data_dir, local_backup_dir)}")
            shutil.rmtree(data_dir, ignore_errors=True)
    # 同时清理 .bak 文件 (恢复用，不需要推上去)
    for bak_file in _glob.glob(os.path.join(local_backup_dir, "Data", "*", "Layout", "*.bak")):
        os.remove(bak_file)

    print(f"  📤 正在推送 …")
    t0 = time.time()
    for item in os.listdir(local_backup_dir):
        if item in SKIP_ITEMS:
            continue
        src = os.path.join(local_backup_dir, item)
        dst = phone_backup_root + "/" + item
        r = subprocess.run(
            [ADB_PATH, "-s", serial, "push", src, dst],
            capture_output=False,
            timeout=300,
        )
        if r.returncode != 0:
            print(f"  {red(f'❌ 推送失败: {item}')}")
            return False

    elapsed = time.time() - t0
    print(f"  ✅ 推送完成，耗时 {elapsed:.1f}s")

    # 验证
    print(f"\n  📋 验证手机端文件:")
    verify_r = adb_shell(f"ls -la {phone_backup_root}/Data/*/Layout/ 2>/dev/null", timeout=10)
    if verify_r and verify_r.stdout:
        lines = [l for l in verify_r.stdout.strip().splitlines()
                 if "launcher" in l.lower() or ".tar" in l.lower() or ".xml" in l.lower()]
        for l in lines[:8]:
            print(f"     {l.strip()}")

    return True


# ============================================================================
#  分类统计预览
# ============================================================================

def show_stats(work_dir):
    """显示当前分类统计"""
    config_file = os.path.join(work_dir, "app_categories.json")
    if not os.path.isfile(config_file):
        print(f"  (暂无分类数据)")
        return

    with open(config_file, "r", encoding="utf-8") as f:
        raw = f.read()
    clean = re.sub(r'//[^\n]*', '', raw)
    try:
        config = json.loads(clean)
    except json.JSONDecodeError:
        return

    cats = config.get("app_categories", {})
    order = config.get("category_order", [])
    uncl = config.get("unclassified", [])

    # 按分类统计
    by_cat = {}
    for pkg, cat in cats.items():
        by_cat.setdefault(cat, []).append(pkg)

    print(f"\n  📊 分类统计:")
    for cat in order:
        n = len(by_cat.get(cat, []))
        if n:
            print(f"     {cat:12s}  {n} 个应用")
    # 不在 order 里的
    for cat in sorted(by_cat):
        if cat not in order and by_cat[cat]:
            print(f"     {cat:12s}  {len(by_cat[cat])} 个应用")
    print(f"     {'未分类':12s}  {len(uncl)} 个")
    print(f"     {'合计':12s}  {len(cats) + len(uncl)} 个")


# ============================================================================
#  交互式主菜单
# ============================================================================

def show_menu(serial, phone_root, ts, local_backup):
    """显示主菜单"""
    print()
    print(cyan("╔══════════════════════════════════════════════════════╗"))
    print(cyan("║") + bold("   ColorOS 桌面布局一键修补工具 — 全流程              ") + cyan("║"))
    print(cyan("╠══════════════════════════════════════════════════════╣"))
    print(cyan("║") + f"  设备: {serial:<46s}" + cyan("║"))
    print(cyan("║") + f"  备份: {ts:<46s}" + cyan("║"))
    has_local = "✅ 已拉取" if local_backup and os.path.isdir(local_backup) else "❌ 未拉取"
    print(cyan("║") + f"  本地: {has_local:<46s}" + cyan("║"))
    print(cyan("╠══════════════════════════════════════════════════════╣"))
    print(cyan("║") + "                                                      " + cyan("║"))
    print(cyan("║") + "  1. 从手机拉取备份到本地                              " + cyan("║"))
    print(cyan("║") + "  2. 从备份数据库提取应用列表                          " + cyan("║"))
    print(cyan("║") + "  3. 多渠道云端自动分类 (GP + 应用宝)                  " + cyan("║"))
    print(cyan("║") + "  3A.🤖 AI 智能分类 (OpenAI 兼容 API)                  " + cyan("║"))
    print(cyan("║") + "  4. 交互式处理未分类应用                              " + cyan("║"))
    print(cyan("║") + "  5. 生成新布局 → 写入 DB + XML + tar                  " + cyan("║"))
    print(cyan("║") + "  6. 推回手机 (完整替换备份)                            " + cyan("║"))
    print(cyan("║") + "                                                      " + cyan("║"))
    print(cyan("║") + "  S. 查看分类统计                                      " + cyan("║"))
    print(cyan("║") + "  C. ⚙️  AI 分类设置 (URL / Key / Model)                " + cyan("║"))
    print(cyan("║") + "  R. 恢复手机上备份为原始状态                          " + cyan("║"))
    print(cyan("║") + "  Q. 退出                                              " + cyan("║"))
    print(cyan("║") + "                                                      " + cyan("║"))
    print(cyan("║") + f"  💡 请按顺序执行 1 → 2 → 3/3A → 4 → 5 → 6          " + cyan("║"))
    print(cyan("║") + "                                                      " + cyan("║"))
    print(cyan("╚══════════════════════════════════════════════════════╝"))


def main():
    print_header("ColorOS 桌面布局一键修补工具")

    # ---- 环境检测 ----
    if not check_environment():
        print(f"\n  {red('环境检测未通过，请先修复上述问题')}")
        wait_for_exit("按任意键退出…")
        sys.exit(1)

    # ---- 连接设备 ----
    serial = connect_device()
    if not serial:
        wait_for_exit("按任意键退出…")
        sys.exit(1)

    # ---- 定位备份 ----
    phone_root, ts = find_phone_backup(serial)
    if not phone_root:
        wait_for_exit("按任意键退出…")
        sys.exit(1)

    # ---- 工作目录 ----
    local_backup = os.path.join(LOCAL_WORK_DIR, "Backup")
    # 如果工作目录下已经有 Backup/Data，视为有效
    if not os.path.isdir(os.path.join(local_backup, "Data")):
        local_backup = None

    # ---- 主循环 ----
    while True:
        # 确定当前有效工作目录
        work_dir = local_backup if local_backup and os.path.isdir(local_backup) else None

        show_menu(serial, phone_root, ts, local_backup)

        if work_dir:
            show_stats(work_dir)

        try:
            choice = input(f"\n  请输入选项: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  👋 再见！")
            break

        if choice == "1":
            result = pull_backup(serial, phone_root)
            if result:
                local_backup = result

        elif choice == "2":
            if not work_dir:
                print(f"\n  ⚠️  请先拉取备份 (选项 1)")
            else:
                step_extract_packages(work_dir)

        elif choice == "3":
            if not work_dir:
                print(f"\n  ⚠️  请先拉取备份 (选项 1)")
            else:
                step_classify(work_dir)

        elif choice == "3A":
            if not work_dir:
                print(f"\n  ⚠️  请先拉取备份 (选项 1)")
            else:
                step_classify_ai(work_dir)

        elif choice == "C":
            # AI 配置设置 — 工作目录可能尚未建立，用 SCRIPT_DIR
            target_dir = work_dir or SCRIPT_DIR
            step_ai_setup(target_dir)

        elif choice == "4":
            if not work_dir:
                print(f"\n  ⚠️  请先拉取备份 (选项 1)")
            else:
                step_interactive(work_dir)

        elif choice == "5":
            if not work_dir:
                print(f"\n  ⚠️  请先拉取备份 (选项 1)")
            else:
                step_reorganize(work_dir)

        elif choice == "6":
            if not work_dir:
                print(f"\n  ⚠️  请先拉取备份 (选项 1)")
            else:
                push_backup(serial, phone_root, local_backup)

        elif choice == "S":
            if work_dir:
                show_stats(work_dir)
            else:
                print(f"\n  暂无数据 (请先拉取备份)")

        elif choice == "R":
            # 恢复
            if not work_dir:
                print(f"\n  ⚠️  请先拉取备份")
            else:
                # 先检查是否存在 .bak 文件
                import glob as _g
                layout_dirs = _g.glob(os.path.join(work_dir, "Data", "*", "Layout"))
                has_bak = False
                for ld in layout_dirs:
                    if _g.glob(os.path.join(ld, "**", "*.bak"), recursive=True):
                        has_bak = True
                        break

                if not has_bak:
                    print(f"\n  ℹ️  当前备份文件就是原始状态（未找到 .bak 文件）")
                    print(f"     .bak 文件在步骤 5（生成新布局）时自动创建")
                    print(f"     如果您尚未执行过步骤 5，则无需恢复")
                else:
                    print(f"\n  🔄 恢复本地备份为原始状态 …")
                    script = _find_script("reorganize_layout_oneclick.py")
                    _run_script_inprocess(script, ["--restore", "--workdir", work_dir], cwd=work_dir)
                    # 询问是否推回
                    push_choice = input(f"\n  是否推回手机？(y/N): ").strip().lower()
                    if push_choice == "y":
                        push_backup(serial, phone_root, local_backup)

        elif choice == "Q":
            print(f"\n  👋 再见！")
            break

        else:
            print(f"\n  ⚠️  无效选项")

        if choice and choice.upper() != "Q":
            try:
                input("\n  按回车键继续…")
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":
    main()
