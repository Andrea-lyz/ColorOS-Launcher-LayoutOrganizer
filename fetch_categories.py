#!/usr/bin/env python3
"""
应用分类辅助工具 v3.0
====================
功能：
  1. 从备份数据库提取所有应用的包名和名称
  2. 多渠道自动识别应用类别：
     ① Google Play (国际应用覆盖好)
     ② 腾讯应用宝 sj.qq.com (中国应用覆盖好)
     ③ AI 分类 (兼容 OpenAI API，支持联网搜索)
     三个渠道均查不到的应用，列入 JSON 的 "unclassified" 供手动分类
  3. 交互式处理未分类的包名
  4. 为 app_categories 中的条目补充人类可读的应用名称
  5. 生成 / 合并到 app_categories.json 供主脚本使用

使用方法：
  python fetch_categories.py --from-db             # 从备份数据库提取包名+应用名
  python fetch_categories.py --classify            # 多渠道自动分类 (GP + 应用宝)
  python fetch_categories.py --classify-ai         # AI 智能分类 (OpenAI 兼容 API)
  python fetch_categories.py --ai-setup            # 配置 AI API (URL / Key / Model)
  python fetch_categories.py --interactive         # 交互式处理未分类的包名
  python fetch_categories.py --enrich-names        # 补充所有应用的人类可读名称
  python fetch_categories.py --all                 # 一步到位 (from-db + classify + interactive + enrich-names)
  python fetch_categories.py --stats               # 查看分类统计
  python fetch_categories.py --workdir <DIR>       # 指定工作目录（供主控脚本调用）

依赖：
  pip install requests  (--classify / --enrich-names 需要)
  pip install openai    (--classify-ai 需要)

输出文件：
  packages.txt         — 从数据库提取的包名列表（每行一个）
  app_categories.json  — 分类映射表（主脚本读取此文件）
  ai_config.json       — AI API 配置文件（--ai-setup 生成）
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

# Windows PowerShell 默认 GBK，强制 UTF-8 输出
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

# ============================================================================
#  中断控制 —— Windows 打包 exe / 批处理中 Ctrl+C 在阻塞 I/O 期间不可靠，
#  改用 msvcrt.kbhit() 非阻塞检测按键（按任意键即可触发中断标志）。
# ============================================================================

_cancel_flag = False


def _check_cancel():
    """非阻塞检查用户是否按了键盘，按了就设置中断标志。返回 True 表示应中断。"""
    global _cancel_flag
    if _cancel_flag:
        return True
    try:
        import msvcrt
        if msvcrt.kbhit():
            msvcrt.getch()  # 消费按键
            _cancel_flag = True
            return True
    except ImportError:
        pass  # 非 Windows 环境，跳过
    return False


def _reset_cancel():
    """重置中断标志，并清空 Windows 键盘缓冲区中残留的按键"""
    global _cancel_flag
    _cancel_flag = False
    try:
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getch()
    except ImportError:
        pass


def _run_with_cancel(func, *args, poll_interval=0.2, **kwargs):
    """
    在后台线程中执行 func(*args, **kwargs)，同时在主线程轮询按键中断。
    如果用户在执行期间按了键，设置 _cancel_flag 并等待线程结束后返回结果。
    返回 func 的返回值（可能为 None），或者在线程异常时返回 None。
    """
    import threading

    result_box = [None]
    error_box = [None]

    def _worker():
        try:
            result_box[0] = func(*args, **kwargs)
        except Exception as e:
            error_box[0] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    while t.is_alive():
        _check_cancel()
        t.join(timeout=poll_interval)

    if error_box[0] is not None:
        raise error_box[0]
    return result_box[0]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGES_FILE = os.path.join(SCRIPT_DIR, "packages.txt")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "app_categories.json")
AI_CONFIG_FILE = os.path.join(SCRIPT_DIR, "ai_config.json")

# ============================================================================
#  渠道 1: Google Play 类别映射
# ============================================================================

PLAY_CATEGORY_MAP = {
    # 应用
    "art_and_design":       "媒体工具",
    "auto_and_vehicles":    "出行旅行",
    "beauty":               "生活服务",
    "books_and_reference":  "影音娱乐",
    "business":             "系统工具",
    "comics":               "影音娱乐",
    "communication":        "社交通讯",
    "dating":               "社交通讯",
    "education":            "学校学习",
    "entertainment":        "影音娱乐",
    "events":               "生活服务",
    "finance":              "金融支付",
    "food_and_drink":       "外卖生活",
    "health_and_fitness":   "生活服务",
    "house_and_home":       "智能家居",
    "libraries_and_demo":   "系统工具",
    "lifestyle":            "生活服务",
    "maps_and_navigation":  "出行旅行",
    "medical":              "生活服务",
    "music_and_audio":      "影音娱乐",
    "news_and_magazines":   "资讯社区",
    "parenting":            "生活服务",
    "personalization":      "系统工具",
    "photography":          "媒体工具",
    "productivity":         "系统工具",
    "shopping":             "购物电商",
    "social":               "社交通讯",
    "sports":               "影音娱乐",
    "tools":                "系统工具",
    "travel_and_local":     "出行旅行",
    "video_players":        "影音娱乐",
    "weather":              "系统工具",
    "word":                 "学校学习",
    # 游戏
    "game":                 "游戏",
    "game_action":          "游戏",
    "game_adventure":       "游戏",
    "game_arcade":          "游戏",
    "game_board":           "游戏",
    "game_card":            "游戏",
    "game_casino":          "游戏",
    "game_casual":          "游戏",
    "game_educational":     "游戏",
    "game_music":           "游戏",
    "game_puzzle":          "游戏",
    "game_racing":          "游戏",
    "game_role_playing":    "游戏",
    "game_simulation":      "游戏",
    "game_sports":          "游戏",
    "game_strategy":        "游戏",
    "game_trivia":          "游戏",
    "game_word":            "游戏",
}

# ============================================================================
#  渠道 2: 腾讯应用宝分类映射 (sj.qq.com)
# ============================================================================

QQ_CATEGORY_MAP = {
    # 社交
    "好友社交":     "社交通讯",
    "社交":         "社交通讯",
    "即时通讯":     "社交通讯",
    "通讯":         "社交通讯",
    "聊天社交":     "社交通讯",
    "社区":         "资讯社区",
    "综合社区":     "资讯社区",
    "综合社区/论坛": "资讯社区",
    # 影音
    "视频":         "影音娱乐",
    "短视频":       "影音娱乐",
    "音乐":         "影音娱乐",
    "直播":         "影音娱乐",
    "在线视频":     "影音娱乐",
    "影视":         "影音娱乐",
    "漫画":         "影音娱乐",
    "阅读":         "影音娱乐",
    "小说":         "影音娱乐",
    "娱乐":         "影音娱乐",
    "K歌":          "影音娱乐",
    "听书":         "影音娱乐",
    "音频":         "影音娱乐",
    "动漫":         "影音娱乐",
    # 购物
    "网上购物":     "购物电商",
    "购物":         "购物电商",
    "电商":         "购物电商",
    "团购":         "购物电商",
    "优惠":         "购物电商",
    "比价":         "购物电商",
    # 金融
    "移动支付":     "金融支付",
    "支付":         "金融支付",
    "银行":         "金融支付",
    "理财":         "金融支付",
    "基金":         "金融支付",
    "股票":         "金融支付",
    "炒股":         "金融支付",
    "借贷":         "金融支付",
    "保险":         "金融支付",
    "记账":         "金融支付",
    "其他基金":     "金融支付",
    # 出行
    "地图导航":     "出行旅行",
    "出行":         "出行旅行",
    "旅游":         "出行旅行",
    "公交":         "出行旅行",
    "打车":         "出行旅行",
    "住宿":         "出行旅行",
    "航班":         "出行旅行",
    "火车":         "出行旅行",
    "导航":         "出行旅行",
    "酒店":         "出行旅行",
    "租车":         "出行旅行",
    # 外卖
    "外卖":         "外卖生活",
    "美食":         "外卖生活",
    "菜谱":         "外卖生活",
    "餐饮":         "外卖生活",
    # 生活
    "生活":         "生活服务",
    "健康":         "生活服务",
    "运动健康":     "生活服务",
    "健身":         "生活服务",
    "医疗":         "生活服务",
    "天气":         "系统工具",
    "快递":         "生活服务",
    "房产":         "生活服务",
    "家政":         "生活服务",
    "缴费":         "生活服务",
    "便民":         "生活服务",
    "生活服务":     "生活服务",
    # 工具
    "实用工具":     "系统工具",
    "工具":         "系统工具",
    "系统":         "系统工具",
    "安全":         "系统工具",
    "输入法":       "系统工具",
    "效率":         "系统工具",
    "办公":         "系统工具",
    "文件管理":     "系统工具",
    "桌面":         "系统工具",
    "WIFI":         "系统工具",
    "wifi":         "系统工具",
    # 学习
    "教育":         "学校学习",
    "学习":         "学校学习",
    "翻译":         "学校学习",
    "考试":         "学校学习",
    "词典":         "学校学习",
    "外语":         "学校学习",
    "儿童":         "学校学习",
    # 拍照 / 媒体
    "拍摄美化":     "媒体工具",
    "美化":         "媒体工具",
    "拍照":         "媒体工具",
    "相机":         "媒体工具",
    "图片编辑":     "媒体工具",
    "壁纸":         "媒体工具",
    # 新闻
    "新闻":         "资讯社区",
    "资讯":         "资讯社区",
    "论坛":         "资讯社区",
    # 浏览器
    "浏览器":       "浏览器",
    # 智能家居
    "智能硬件":     "智能家居",
    "智能家居":     "智能家居",
    "物联网":       "智能家居",
    # 游戏 (应用宝的游戏页面标签)
    "角色扮演":     "游戏",
    "动作冒险":     "游戏",
    "策略":         "游戏",
    "休闲益智":     "游戏",
    "棋牌":         "游戏",
    "竞速":         "游戏",
    "射击":         "游戏",
    "体育":         "游戏",
    "模拟":         "游戏",
    "经营":         "游戏",
    "卡牌":         "游戏",
    "创新品类":     "游戏",
    "游戏社区":     "游戏",
    "MMORPG":       "游戏",
    "ARPG":         "游戏",
    "回合制":       "游戏",
    "塔防":         "游戏",
    "音乐游戏":     "游戏",
}

# OPPO/ColorOS 系统应用名称映射（不需要联网查询）
SYSTEM_APP_NAMES = {
    "com.android.settings":     "设置",
    "com.coloros.filemanager":   "文件管理",
    "com.oplus.camera":         "相机",
    "com.coloros.gallery3d":    "相册",
    "com.coloros.calendar":     "日历",
    "com.coloros.note":         "笔记",
    "com.coloros.alarmclock":   "时钟",
    "com.coloros.calculator":   "计算器",
    "com.android.contacts":     "联系人",
    "com.android.mms":          "信息",
    "com.android.email":        "邮件",
    "com.coloros.weather2":     "天气",
    "com.heytap.browser":       "浏览器",
    "com.coloros.compass2":     "指南针",
    "com.coloros.soundrecorder": "录音",
    "com.coloros.translate":    "翻译",
    "com.oplus.tips":           "使用技巧",
    "com.android.chrome":       "Chrome",
    "com.android.vending":      "Play 商店",
}

# 常见国际应用名称映射（应用宝上不一定有的国际 app）
KNOWN_INTERNATIONAL_APP_NAMES = {
    "com.twitter.android":          "Twitter / X",
    "com.instagram.android":        "Instagram",
    "com.whatsapp":                 "WhatsApp",
    "org.telegram.messenger":       "Telegram",
    "com.facebook.katana":          "Facebook",
    "com.facebook.orca":            "Messenger",
    "com.facebook.lite":            "Facebook Lite",
    "com.discord":                  "Discord",
    "com.snapchat.android":         "Snapchat",
    "com.reddit.frontpage":         "Reddit",
    "com.pinterest":                "Pinterest",
    "com.linkedin.android":         "LinkedIn",
    "com.tumblr":                   "Tumblr",
    "com.spotify.music":            "Spotify",
    "com.netflix.mediaclient":      "Netflix",
    "com.google.android.youtube":   "YouTube",
    "com.amazon.mShop.android.shopping": "Amazon Shopping",
    "com.paypal.android.p2pmobile": "PayPal",
    "com.ubercab":                  "Uber",
    "com.skype.raider":             "Skype",
    "us.zoom.videomeetings":        "Zoom",
    "com.microsoft.teams":          "Microsoft Teams",
    "com.microsoft.office.outlook": "Outlook",
    "com.google.android.apps.maps": "Google Maps",
    "com.google.android.gm":        "Gmail",
    "com.google.android.apps.docs": "Google Docs",
    "com.google.android.apps.photos": "Google Photos",
    "com.google.android.keep":      "Google Keep",
    "com.google.android.calendar":  "Google Calendar",
    "com.google.android.apps.translate": "Google Translate",
    "com.google.android.apps.meetings": "Google Meet",
    "com.google.android.googlequicksearchbox": "Google",
    "com.google.android.dialer":    "Google Phone",
    "com.amazon.kindle":            "Kindle",
    "tv.twitch.android.app":        "Twitch",
    "org.mozilla.firefox":          "Firefox",
    "com.microsoft.emmx":           "Edge",
    "com.brave.browser":            "Brave",
    "com.opera.browser":            "Opera",
    "org.videolan.vlc":             "VLC",
    "com.shopee.id":                "Shopee",
    "com.tiktok.tiktok_tv":        "TikTok TV",
}

# ============================================================================
#  加载 / 保存配置
# ============================================================================

def _strip_json_comments(text):
    """去掉 JSON 中的行尾 // 注释，返回纯净 JSON 字符串"""
    lines = []
    for line in text.splitlines():
        # 找 // 注释：必须在引号外面
        # 简单策略：从行尾往前找 //，确认它在最后一个 " 之后
        last_quote = line.rfind('"')
        if last_quote >= 0:
            comment_pos = line.find('//', last_quote + 1)
            if comment_pos >= 0:
                line = line[:comment_pos].rstrip()
                # 确保逗号正确
                if line.endswith(','):
                    pass  # 已有逗号，保持
                elif line.endswith('"'):
                    # 检查下一个非空行是否是 } 或 ]，如果不是需要逗号
                    pass  # 逗号在原文中已经正确处理
        lines.append(line)
    return '\n'.join(lines)


def _extract_names_from_comments(raw_text):
    """从 app_categories 区域的行尾 // 注释中提取 {pkg: appName}"""
    names = {}
    in_app_cats = False
    for line in raw_text.splitlines():
        if '"app_categories"' in line and '{' in line:
            in_app_cats = True
            continue
        if in_app_cats:
            stripped = line.strip()
            if stripped.startswith('}'):
                break
            m = re.match(r'^\s*"([^"]+)"\s*:\s*"[^"]+"[,]?\s*//\s*(.+?)\s*$', line)
            if m:
                names[m.group(1)] = m.group(2)
    return names


def load_config():
    """加载现有的 app_categories.json（支持行尾 // 注释），不存在则返回空结构"""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
        # 从注释中还原 app_names（文件中不再单独存储该字段）
        names_from_comments = _extract_names_from_comments(raw)
        clean = _strip_json_comments(raw)
        config = json.loads(clean)
        # 合并：注释中的名字 + 可能残留的旧 app_names 字段
        merged_names = config.get("app_names", {})
        merged_names.update(names_from_comments)  # 注释优先
        config["app_names"] = merged_names
        return config
    return {
        "_说明": "应用分类配置文件 — 供 reorganize_layout_oneclick.py 读取",
        "_用法": "1) 用 fetch_categories.py 生成初始分类  2) 手动微调本文件  3) 运行 reorganize_layout_oneclick.py",
        "category_order": [],
        "app_categories": {},
        "unclassified": [],
    }


def save_config(config):
    """保存到 app_categories.json（app_categories 区域带行尾 // 应用名注释）"""
    app_cats = config.get("app_categories", {})
    app_names = config.get("app_names", {})

    # app_names 只存在于内存中，不写入文件（通过行尾注释体现）
    config_to_save = {k: v for k, v in config.items() if k != "app_names"}

    # 先用标准 json.dumps 生成基础文本
    raw = json.dumps(config_to_save, ensure_ascii=False, indent=2)

    # 在 app_categories 区域的每行后面追加 // 应用名
    if app_names and app_cats:
        lines = raw.splitlines()
        new_lines = []
        in_app_cats = False
        for line in lines:
            if '"app_categories"' in line and '{' in line:
                in_app_cats = True
                new_lines.append(line)
                continue
            if in_app_cats:
                # 检测 app_categories 块结束
                stripped = line.strip()
                if stripped.startswith('}'):
                    in_app_cats = False
                    new_lines.append(line)
                    continue
                # 匹配 "pkg": "cat" 行
                m = re.match(r'^(\s*"([^"]+)":\s*"[^"]+"[,]?)\s*$', line)
                if m:
                    full_line = m.group(1)
                    pkg = m.group(2)
                    name = app_names.get(pkg, "")
                    if name:
                        line = f"{full_line}  // {name}"
            new_lines.append(line)
        raw = '\n'.join(new_lines)

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(raw + '\n')
    print(f"  💾 已保存: {os.path.basename(CONFIG_FILE)}")


# ============================================================================
#  Step 1: ADB 导出包名
# ============================================================================

def dump_packages():
    """通过 ADB 导出第三方应用包名列表"""
    print("\n📱 正在通过 ADB 导出应用列表…\n")

    try:
        # 第三方应用 (-3)
        result = subprocess.run(
            ["adb", "shell", "pm", "list", "packages", "-3"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"  ❌ ADB 执行失败: {result.stderr.strip()}")
            print("     请确认：1) 手机已连接  2) USB 调试已开启  3) adb 在 PATH 中")
            return []

        packages = sorted(set(
            line.replace("package:", "").strip()
            for line in result.stdout.strip().splitlines()
            if line.strip()
        ))

        # 写入文件
        with open(PACKAGES_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(packages) + "\n")

        print(f"  ✅ 已导出 {len(packages)} 个第三方应用 → {os.path.basename(PACKAGES_FILE)}")
        return packages

    except FileNotFoundError:
        print("  ❌ 未找到 adb 命令。请安装 Android SDK Platform Tools 并添加到 PATH。")
        return []
    except subprocess.TimeoutExpired:
        print("  ❌ ADB 连接超时。请检查手机连接。")
        return []


def load_packages():
    """从 packages.txt 加载包名列表"""
    if not os.path.exists(PACKAGES_FILE):
        print(f"  ⚠️  {os.path.basename(PACKAGES_FILE)} 不存在，请先运行 --dump")
        return []
    with open(PACKAGES_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


# ============================================================================
#  Step 2: 多渠道网络分类
# ============================================================================

def _get_requests():
    """延迟导入 requests"""
    try:
        import requests
        return requests
    except ImportError:
        print("  ❌ 需要 requests 库: pip install requests")
        sys.exit(1)


def _http_headers(lang="en"):
    """通用 HTTP 请求头"""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": f"{'en-US,en;q=0.9' if lang == 'en' else 'zh-CN,zh;q=0.9'}",
    }


# ---- 渠道 1: Google Play ----

def classify_via_google_play(pkg):
    """
    通过 Google Play Store 页面获取应用名称和类别。
    返回 (app_name, category_zh, source) 或 (None, None, None)。
    """
    requests = _get_requests()
    url = f"https://play.google.com/store/apps/details?id={pkg}&hl=en"

    try:
        resp = requests.get(url, headers=_http_headers("en"), timeout=8)
        if resp.status_code != 200:
            return None, None, None

        text = resp.text

        # ---- 提取应用名 ----
        app_name = None
        # 方法1: ld+json 结构化数据（最可靠，同时提取应用名和分类）
        ld_data = None
        ld_match = re.search(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            text, re.DOTALL)
        if ld_match:
            try:
                ld_data = json.loads(ld_match.group(1))
                app_name = ld_data.get("name")
            except (json.JSONDecodeError, AttributeError):
                pass
        # 方法2: og:title meta
        if not app_name:
            og_match = re.search(
                r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', text)
            if og_match:
                raw = og_match.group(1).strip()
                # 去掉 " - Apps on Google Play" 后缀
                app_name = re.sub(r'\s*-\s*Apps on Google Play$', '', raw) or None
        # 方法3: <title ...>xxx</title>（可能带 id 等属性）
        if not app_name:
            title_match = re.search(
                r'<title[^>]*>([^<]+?)(?:\s*-\s*Apps on Google Play)?</title>', text)
            if title_match:
                app_name = title_match.group(1).strip() or None

        # ---- 提取分类 ----
        # 方法A: ld+json 的 applicationCategory（最可靠）
        if ld_data:
            app_cat = ld_data.get("applicationCategory", "")
            if app_cat:
                cat_zh = PLAY_CATEGORY_MAP.get(app_cat.lower())
                if cat_zh:
                    return app_name, cat_zh, "Google Play"

        # 方法B: /store/apps/category/ URL，跳过 FAMILY（导航链接）
        for cat_m in re.finditer(r'/store/apps/category/([A-Z_]+)', text):
            raw_cat = cat_m.group(1)
            if raw_cat == "FAMILY":
                continue
            cat_zh = PLAY_CATEGORY_MAP.get(raw_cat.lower())
            if cat_zh:
                return app_name, cat_zh, "Google Play"

        # 备用：尝试从 itemprop="genre" 提取
        genre_match = re.search(r'itemprop="genre"[^>]*content="([^"]+)"', text)
        if genre_match:
            genre = genre_match.group(1).lower().replace(" ", "_").replace("&", "and")
            cat_zh = PLAY_CATEGORY_MAP.get(genre)
            return app_name, cat_zh, "Google Play"

        return app_name, None, None

    except Exception:
        return None, None, None


# ---- 渠道 2: 腾讯应用宝 (sj.qq.com) ----

def classify_via_qqstore(pkg):
    """
    通过腾讯应用宝页面的 __NEXT_DATA__ JSON 获取应用名称和类别。
    结构化数据路径:
      __NEXT_DATA__.props.pageProps.dynamicCardResponse.data.components
      → cardId="yybn_game_basic_info" 的 component
      → data.itemData[0] 中包含:
         name (应用名), tags (分类标签,逗号分隔), cate_name (大分类)
    返回 (app_name, category_zh, source) 或 (None, None, None)。
    """
    requests = _get_requests()
    url = f"https://sj.qq.com/appdetail/{pkg}"

    try:
        resp = requests.get(url, headers=_http_headers("zh"), timeout=5)
        if resp.status_code != 200:
            return None, None, None

        text = resp.text

        # 提取 __NEXT_DATA__ JSON
        nd_match = re.search(
            r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.DOTALL)
        if not nd_match:
            return None, None, None

        try:
            nd = json.loads(nd_match.group(1))
        except json.JSONDecodeError:
            return None, None, None

        # 遍历 components 找到主应用信息卡片
        components = (nd.get("props", {}).get("pageProps", {})
                       .get("dynamicCardResponse", {}).get("data", {})
                       .get("components", []))

        app_name = None
        tags_str = None
        cate_name = None

        for comp in components:
            card_id = comp.get("cardId", "")
            # 主信息卡片的 cardId 是 yybn_game_basic_info（不论是游戏还是应用）
            if card_id != "yybn_game_basic_info":
                continue
            items = comp.get("data", {}).get("itemData", [])
            if not items:
                continue
            item = items[0]
            # 校验包名匹配
            if item.get("pkg_name", "") != pkg:
                continue
            app_name = item.get("name") or None
            tags_str = item.get("tags", "")          # "好友社交" 或 "角色扮演,ARPG,冒险"
            cate_name = item.get("cate_name", "")    # "社交" / "角色扮演" 等
            break

        if not app_name:
            # 尝试从 seoMeta 提取应用名作为兜底
            seo_title = (nd.get("props", {}).get("pageProps", {})
                          .get("seoMeta", {}).get("title", ""))
            # 格式: "微信下载安装-微信APP官网客户端下载-应用宝官网"
            # 如果包含"相关推荐"说明是重定向页面，应用实际不存在
            if (seo_title and "应用宝" in seo_title
                    and "相关推荐" not in seo_title):
                name_part = seo_title.split("下载")[0].strip()
                if name_part and name_part not in ("应用宝", "腾讯应用宝"):
                    app_name = name_part

        if not app_name:
            return None, None, None

        # 从 tags 和 cate_name 映射到我们的分类
        # 优先匹配 tags（更精细），再匹配 cate_name
        for tag in (tags_str or "").split(","):
            tag = tag.strip()
            if tag and tag in QQ_CATEGORY_MAP:
                return app_name, QQ_CATEGORY_MAP[tag], "应用宝"

        if cate_name and cate_name in QQ_CATEGORY_MAP:
            return app_name, QQ_CATEGORY_MAP[cate_name], "应用宝"

        # 找到了应用名但分类标签不在映射表中
        return app_name, None, None

    except Exception:
        return None, None, None


# ---- 多渠道聚合 ----

def classify_package(pkg):
    """
    多渠道查询应用名和分类。
    优先级：Google Play → 腾讯应用宝
    返回 (app_name, category_zh, source)
    """
    # 渠道 1: Google Play
    name1, cat1, src1 = classify_via_google_play(pkg)
    if cat1:
        return name1, cat1, src1

    # 渠道 2: 腾讯应用宝
    name2, cat2, src2 = classify_via_qqstore(pkg)
    if cat2:
        # 优先使用应用宝找到的名字（中文名更友好）；如果 Google Play 有英文名也记录
        final_name = name2 or name1
        return final_name, cat2, src2

    # 都没找到分类，返回最佳可用应用名
    best_name = name2 or name1  # 优先中文名
    return best_name, None, None


def lookup_app_name(pkg, skip_google=False):
    """
    仅查询应用名（不需要分类），用于 enrich_names。
    优先系统内置映射 → 国际应用映射 → 应用宝 → Google Play。
    返回应用名字符串或 None。
    """
    # 先查系统应用映射
    if pkg in SYSTEM_APP_NAMES:
        return SYSTEM_APP_NAMES[pkg]

    # 常见国际应用映射（无需网络）
    if pkg in KNOWN_INTERNATIONAL_APP_NAMES:
        return KNOWN_INTERNATIONAL_APP_NAMES[pkg]

    # 腾讯应用宝（中文名友好）
    name2, _, _ = classify_via_qqstore(pkg)
    if name2:
        return name2

    # Google Play
    if not skip_google:
        name1, _, _ = classify_via_google_play(pkg)
        if name1:
            return name1

    return None


def classify_all(packages):
    """批量分类所有包名（多渠道）"""
    _get_requests()  # 提前检查

    config = load_config()
    existing = config.get("app_categories", {})
    app_names = config.get("app_names", {})
    categories_used = set(config.get("category_order", []))
    unclassified_list = config.get("unclassified", [])
    # 已在 unclassified 中的包名集合
    unclassified_pkgs = {item["packageName"] if isinstance(item, dict) else item
                         for item in unclassified_list}

    new_count = 0
    not_found = []
    total = len(packages)

    print(f"\n🔍 正在从 Google Play + 腾讯应用宝 查询 {total} 个应用的分类…")
    print(f"  💡 随时按任意键可中断，已查询的结果会自动保存\n")

    interrupted = False
    SAVE_INTERVAL = 20  # 每 20 个增量保存一次
    _reset_cancel()

    for i, pkg in enumerate(packages, 1):
        if pkg in existing:
            continue  # 已有分类，跳过

        # 检查中断标志（非阻塞按键检测）
        if _check_cancel():
            print(f"\n  ⚠️  用户中断！正在保存已查询的 {new_count} 条分类…")
            interrupted = True
            break

        try:
            prefix = f"  [{i}/{total}]"
            app_name, cat_zh, source = _run_with_cancel(classify_package, pkg)

            # 网络请求期间可能用户按了键
            if _check_cancel():
                print(f"\n  ⚠️  用户中断！正在保存已查询的 {new_count} 条分类…")
                interrupted = True
                break

            if cat_zh:
                existing[pkg] = cat_zh
                if app_name:
                    app_names[pkg] = app_name
                categories_used.add(cat_zh)
                new_count += 1
                src_tag = f"[{source}]" if source else ""
                print(f"{prefix} ✅ {pkg} → {app_name or '?'} → {cat_zh} {src_tag}")
                # 如果之前在 unclassified 中，移除
                if pkg in unclassified_pkgs:
                    unclassified_list = [
                        item for item in unclassified_list
                        if (item["packageName"] if isinstance(item, dict) else item) != pkg
                    ]
                    unclassified_pkgs.discard(pkg)
            elif app_name:
                print(f"{prefix} ⚠️  {pkg} ({app_name}) — 两个渠道均未识别分类")
                not_found.append((pkg, app_name))
            else:
                print(f"{prefix} ❓ {pkg} — 两个渠道均未找到此应用")
                not_found.append((pkg, None))

            # 礼貌性延迟，避免被限速
            time.sleep(0.5)
        except KeyboardInterrupt:
            print(f"\n\n  ⚠️  用户中断！正在保存已查询的 {new_count} 条分类…")
            interrupted = True
            break
        except Exception as e:
            print(f"{prefix} ❌ {pkg} — 异常: {e}")

        # 增量保存
        if new_count > 0 and new_count % SAVE_INTERVAL == 0:
            config["app_categories"] = existing
            config["app_names"] = app_names
            config["unclassified"] = unclassified_list
            save_config(config)

    _reset_cancel()

    # 将仍未分类的应用写入 unclassified
    if not interrupted:
        for pkg, name in not_found:
            if pkg not in unclassified_pkgs and pkg not in existing:
                entry = {"packageName": pkg}
                if name:
                    entry["appName"] = name
                unclassified_list.append(entry)
                unclassified_pkgs.add(pkg)

    # 更新 category_order
    config["app_categories"] = existing
    config["app_names"] = app_names
    order = config.get("category_order", [])
    for cat in sorted(categories_used):
        if cat not in order:
            order.append(cat)
    config["category_order"] = order
    config["unclassified"] = unclassified_list

    save_config(config)

    print(f"\n  📊 新增 {new_count} 个分类")
    if interrupted:
        print(f"  💾 已安全保存。下次运行 --classify 会继续剩余部分。")
    elif not_found:
        print(f"  ⚠️  {len(not_found)} 个应用仍需手动分类（已写入 JSON \"unclassified\" 字段）：")
        for pkg, name in not_found[:10]:
            print(f"      {pkg}" + (f" ({name})" if name else ""))
        if len(not_found) > 10:
            print(f"      … 共 {len(not_found)} 个")
        print(f"\n  💡 请手动编辑 {os.path.basename(CONFIG_FILE)} 中的 \"unclassified\" 条目，")
        print(f"     将它们移到 \"app_categories\" 并指定分类，或使用 --interactive 交互处理")


# ============================================================================
#  渠道 3: AI 分类 (兼容 OpenAI API)
# ============================================================================

DEFAULT_AI_CONFIG = {
    "base_url": "http://127.0.0.1:8045/v1",
    "api_key": "",
    "model": "gemini-3-pro-high",
}

# 我们允许 AI 使用的分类名（与现有 Google Play / 应用宝 映射一致 + 4 个新增分类）
ALLOWED_CATEGORIES = [
    "社交通讯", "影音娱乐", "购物电商", "金融支付", "出行旅行",
    "外卖生活", "生活服务", "系统工具", "学校学习", "媒体工具",
    "资讯社区", "浏览器", "智能家居", "游戏",
    # ↓ 新增分类
    "系统应用", "代理工具", "Root工具", "AI工具",
]

AI_SYSTEM_PROMPT = """你是一个 Android 应用分类专家。用户会给你一批 Android 包名（package name）。

【重要】你必须联网搜索每个包名，在 Google Play、应用宝、GitHub、APKPure 等平台上查找该应用的真实信息，然后根据应用的实际功能进行分类。不要仅凭包名猜测。

【分类列表】只能使用以下 18 个分类名之一：
  社交通讯 — 微信、QQ、Telegram、Discord、酷安等即时通讯和社交平台
  影音娱乐 — 视频、音乐、直播、漫画、小说等
  购物电商 — 淘宝、京东、拼多多、亚马逊等购物平台
  金融支付 — 支付宝、银行、理财、股票、加密货币钱包等
  出行旅行 — 地图导航、打车、机票酒店、旅游类
  外卖生活 — 外卖点餐、美食菜谱、餐饮类
  生活服务 — 健康运动、快递、天气、物业、便民服务等日常生活类
  系统工具 — ⚠️ 仅限：输入法、文件管理器、计算器、手电筒等纯工具型小应用
  系统应用 — 手机厂商预装应用（如 OPPO/一加/华为/小米的商店、社区、会员、设置、日历、时钟、天气、浏览器、自带音乐软件等）
  代理工具 — VPN、代理、翻墙、网络加速器、Clash、V2Ray 等科学上网工具
  Root工具 — Magisk、LSPosed、MT管理器、Shizuku、root检测、ADB工具等
  AI工具   — ChatGPT、DeepSeek、Gemini、Copilot、Perplexity、通义千问等 AI 对话/助手类
  学校学习 — 教育、翻译、词典、考试、网课平台等
  媒体工具 — 拍照修图、相机、图片编辑、壁纸等
  资讯社区 — 新闻、论坛、知乎、微博、Reddit 等资讯和社区
  智能家居 — 米家、HomeKit、Cudy等智能硬件和物联网控制类
  游戏     — 所有游戏类应用和游戏、游戏社交平台，包括QQ安全中心等游戏安全类似应用

【特别注意】
- "系统工具" 范围很窄！不要把社交、金融、AI、浏览器、代理等应用归入系统工具
- 开发者工具（GitHub、终端、代码编辑器）→ 系统工具
- 应用商店（Google Play、F-Droid）→ 系统工具
- 如果实在无法识别，分类填 "未知"

【输出格式】严格按以下格式逐行输出，不要有任何多余文字、标题、Markdown 格式、解释、空行：
包名|应用名|分类名

示例：
com.tencent.mm|微信|社交通讯
com.taobao.taobao|淘宝|购物电商
com.android.chrome|Chrome|浏览器
com.heytap.market|OPPO 软件商店|系统应用
com.follow.clash|Clash|代理工具
bin.mt.plus.canary|MT管理器|Root工具
com.deepseek.chat|DeepSeek|AI工具"""


def load_ai_config():
    """加载 AI 配置（URL / API Key / Model）"""
    if os.path.exists(AI_CONFIG_FILE):
        try:
            with open(AI_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # 合并默认值
            merged = dict(DEFAULT_AI_CONFIG)
            merged.update(cfg)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_AI_CONFIG)


def save_ai_config(config):
    """保存 AI 配置"""
    with open(AI_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"  💾 已保存 AI 配置: {os.path.basename(AI_CONFIG_FILE)}")


def configure_ai_settings():
    """交互式配置 AI 参数"""
    cfg = load_ai_config()
    print(f"\n⚙️  AI 分类设置 (兼容 OpenAI API)")
    print(f"  当前配置:")
    print(f"    Base URL : {cfg['base_url']}")
    print(f"    API Key  : {'*' * 8 + cfg['api_key'][-8:] if len(cfg.get('api_key', '')) > 8 else cfg.get('api_key', '(未设置)')}")
    print(f"    Model    : {cfg['model']}")
    print()
    print(f"  直接回车保留当前值，输入新值覆盖:")

    url = input(f"    Base URL [{cfg['base_url']}]: ").strip()
    if url:
        cfg["base_url"] = url.rstrip("/")

    key = input(f"    API Key: ").strip()
    if key:
        cfg["api_key"] = key

    model = input(f"    Model [{cfg['model']}]: ").strip()
    if model:
        cfg["model"] = model

    save_ai_config(cfg)
    print(f"  ✅ AI 配置已更新")
    return cfg


def _call_ai_api(packages_chunk, ai_config):
    """
    调用 OpenAI 兼容 API，发送一批包名，返回原始文本响应。
    """
    try:
        from openai import OpenAI
    except ImportError:
        print(f"  ❌ 需要 openai 库: pip install openai")
        return None

    client = OpenAI(
        base_url=ai_config["base_url"],
        api_key=ai_config["api_key"],
    )

    user_content = "请分类以下 Android 包名（每行一个）:\n\n" + "\n".join(packages_chunk)

    try:
        response = client.chat.completions.create(
            model=ai_config["model"],
            messages=[
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,  # 低温度，确保输出稳定
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"  ❌ AI API 调用失败: {e}")
        return None


def _parse_ai_response(text):
    """
    解析 AI 回复，提取 包名|应用名|分类名 格式。
    返回 {pkg: (app_name, category)} 字典。
    """
    results = {}
    if not text:
        return results

    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        parts = line.split("|")
        if len(parts) >= 3:
            pkg = parts[0].strip()
            app_name = parts[1].strip()
            category = parts[2].strip()
            if pkg and app_name:
                results[pkg] = (app_name, category)
        elif len(parts) == 2:
            # 兼容 包名|分类名 的简略格式
            pkg = parts[0].strip()
            category = parts[1].strip()
            if pkg:
                results[pkg] = (None, category)
    return results


def classify_all_via_ai(packages):
    """
    使用 AI (OpenAI 兼容 API) 批量分类所有包名。
    分批发送（每批最多 50 个），合并结果写入 config。
    """
    ai_config = load_ai_config()

    if not ai_config.get("api_key"):
        print(f"  ❌ AI API Key 未配置，请先设置 (主菜单选项 C)")
        return False

    config = load_config()
    existing = config.get("app_categories", {})
    app_names = config.get("app_names", {})
    categories_used = set(config.get("category_order", []))
    unclassified_list = config.get("unclassified", [])
    unclassified_pkgs = {item["packageName"] if isinstance(item, dict) else item
                         for item in unclassified_list}

    # 只处理尚未分类的包名
    todo = [pkg for pkg in packages if pkg not in existing]
    if not todo:
        print(f"  ✅ 所有应用均已分类，无需 AI 处理")
        return True

    print(f"\n🤖 AI 分类模式")
    print(f"  API: {ai_config['base_url']}")
    print(f"  Model: {ai_config['model']}")
    print(f"  待分类: {len(todo)} 个包名")
    print(f"  💡 随时按 Ctrl+C 可中断，已查询的结果会自动保存\n")

    # 分批（每批最多 50 个包名，避免超过 token 限制）
    BATCH_SIZE = 50
    new_count = 0
    unknown_pkgs = []
    interrupted = False
    _reset_cancel()

    try:
      for batch_start in range(0, len(todo), BATCH_SIZE):
        # 检查中断标志（非阻塞按键检测）
        if _check_cancel():
            print(f"\n  ⚠️  用户中断！正在保存已查询的 {new_count} 条分类…")
            interrupted = True
            break

        batch = todo[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"  📡 [{batch_num}/{total_batches}] 发送 {len(batch)} 个包名到 AI …")

        response_text = _run_with_cancel(_call_ai_api, batch, ai_config)
        # AI 调用期间可能用户按了键
        if _check_cancel():
            print(f"\n  ⚠️  用户中断！正在保存已查询的 {new_count} 条分类…")
            interrupted = True
            break
        if not response_text:
            print(f"  ⚠️  该批次 AI 返回为空，跳过")
            continue

        results = _parse_ai_response(response_text)
        print(f"  📋 AI 返回了 {len(results)} 个结果")

        for pkg in batch:
            if pkg in results:
                name, cat = results[pkg]
                # 规范化分类名
                if cat in ALLOWED_CATEGORIES:
                    existing[pkg] = cat
                    if name:
                        app_names[pkg] = name
                    categories_used.add(cat)
                    new_count += 1
                    print(f"    ✅ {pkg} → {name or '?'} → {cat}")
                    # 从 unclassified 移除
                    if pkg in unclassified_pkgs:
                        unclassified_list = [
                            item for item in unclassified_list
                            if (item["packageName"] if isinstance(item, dict) else item) != pkg
                        ]
                        unclassified_pkgs.discard(pkg)
                elif cat == "未知":
                    print(f"    ❓ {pkg} → {name or '?'} → (AI 无法识别)")
                    unknown_pkgs.append((pkg, name))
                else:
                    # AI 返回了非预设分类名，也接受但给提示
                    existing[pkg] = cat
                    if name:
                        app_names[pkg] = name
                    categories_used.add(cat)
                    new_count += 1
                    print(f"    ✅ {pkg} → {name or '?'} → {cat} (自定义分类)")
                    if pkg in unclassified_pkgs:
                        unclassified_list = [
                            item for item in unclassified_list
                            if (item["packageName"] if isinstance(item, dict) else item) != pkg
                        ]
                        unclassified_pkgs.discard(pkg)
            else:
                print(f"    ⚠️  {pkg} → (AI 未返回结果)")
                unknown_pkgs.append((pkg, None))

        # 批次间短暂间隔
        if batch_start + BATCH_SIZE < len(todo):
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n\n  ⚠️  用户中断！正在保存已查询的 {new_count} 条分类…")
        interrupted = True

    _reset_cancel()

    # 将仍未分类的写入 unclassified
    if not interrupted:
        for pkg, name in unknown_pkgs:
            if pkg not in unclassified_pkgs and pkg not in existing:
                entry = {"packageName": pkg}
                if name:
                    entry["appName"] = name
                unclassified_list.append(entry)
                unclassified_pkgs.add(pkg)

    # 更新 category_order
    config["app_categories"] = existing
    config["app_names"] = app_names
    order = config.get("category_order", [])
    for cat in sorted(categories_used):
        if cat not in order:
            order.append(cat)
    config["category_order"] = order
    config["unclassified"] = unclassified_list

    save_config(config)

    print(f"\n  📊 AI 新增 {new_count} 个分类")
    if interrupted:
        print(f"  💾 已安全保存。下次运行 --classify-ai 会继续剩余部分。")
    elif unknown_pkgs:
        print(f"  ⚠️  {len(unknown_pkgs)} 个应用 AI 无法识别（已写入 unclassified）")
    return True


# ============================================================================
#  Step 3: 交互式分类
# ============================================================================

def interactive_classify(packages):
    """交互式处理未分类的包名"""
    config = load_config()
    existing = config.get("app_categories", {})
    order = config.get("category_order", [])
    unclassified_list = config.get("unclassified", [])

    unclassified = [pkg for pkg in packages if pkg not in existing]
    if not unclassified:
        print("\n  ✅ 所有应用均已分类！")
        return

    # 构建 unclassified 中已有的名称映射
    name_map = {}
    for item in unclassified_list:
        if isinstance(item, dict):
            name_map[item["packageName"]] = item.get("appName")

    # 合并所有已知名称来源: DB 提取的 app_names > unclassified 中的 > 系统内置 > 国际应用
    all_names = {}
    all_names.update(KNOWN_INTERNATIONAL_APP_NAMES)
    all_names.update(SYSTEM_APP_NAMES)
    all_names.update(name_map)
    all_names.update(config.get("app_names", {}))

    print(f"\n✏️  交互式分类 — 共 {len(unclassified)} 个未分类应用")
    print(f"   现有分类: {', '.join(order) if order else '(无)'}")
    print(f"   输入分类名分配，输入 s 跳过，输入 q 保存退出\n")

    changed = False
    for i, pkg in enumerate(unclassified, 1):
        app_name = all_names.get(pkg)

        display = f"{pkg}" + (f" ({app_name})" if app_name else "")
        cat = input(f"  [{i}/{len(unclassified)}] {display}\n    分类: ").strip()

        if cat.lower() == "q":
            break
        if cat.lower() == "s" or not cat:
            continue

        existing[pkg] = cat
        if cat not in order:
            order.append(cat)
        changed = True
        print(f"    → {cat}")

        # 从 unclassified 列表移除
        unclassified_list = [
            item for item in unclassified_list
            if (item["packageName"] if isinstance(item, dict) else item) != pkg
        ]

    if changed:
        config["app_categories"] = existing
        config["category_order"] = order
        config["unclassified"] = unclassified_list
        save_config(config)


# ============================================================================
#  Step 4: 为所有条目补充应用名
# ============================================================================

def _check_google_play_reachable():
    """快速测试 Google Play 是否可达（3 秒超时）"""
    requests = _get_requests()
    try:
        resp = requests.get(
            "https://play.google.com/store/apps/details?id=com.google.android.gm&hl=en",
            headers=_http_headers("en"), timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def enrich_all_names():
    """
    为 app_categories 中的条目查询并补充应用名。
    存入内存 app_names，保存时自动写为行尾 // 注释。
    """
    config = load_config()
    app_categories = config.get("app_categories", {})
    app_names = config.get("app_names", {})
    changed = False

    # 预检测 Google Play 是否可达
    print("\n🌐 检测 Google Play 可达性…", end=" ", flush=True)
    google_ok = _check_google_play_reachable()
    if google_ok:
        print("✅ 可用")
    else:
        print("❌ 不可用（将仅使用腾讯应用宝 + 本地映射）")

    skip_google = not google_ok

    # ---- 为 app_categories 补充应用名 ----
    need_lookup = [pkg for pkg in app_categories if pkg not in app_names]
    print(f"\n🏷️  为 app_categories 补充应用名 ({len(need_lookup)} 个待查)…")
    print(f"  💡 随时按Ctrl+C可中断，已查询的结果会自动保存\n")
    found = 0
    missed = 0
    SAVE_INTERVAL = 20  # 每 20 个增量保存一次
    _reset_cancel()

    for i, pkg in enumerate(need_lookup, 1):
        # 检查中断标志（非阻塞按键检测）
        if _check_cancel():
            print(f"\n  ⚠️  用户中断！正在保存已获取的 {found} 条名称…")
            config["app_names"] = app_names
            save_config(config)
            print(f"  💾 已安全保存。下次运行 --enrich-names 会继续剩余部分。")
            _reset_cancel()
            return

        try:
            prefix = f"  [{i}/{len(need_lookup)}]"
            app_name = _run_with_cancel(lookup_app_name, pkg, skip_google=skip_google)

            # 网络请求期间可能用户按了键
            if _check_cancel():
                print(f"\n  ⚠️  用户中断！正在保存已获取的 {found} 条名称…")
                config["app_names"] = app_names
                save_config(config)
                print(f"  💾 已安全保存。下次运行 --enrich-names 会继续剩余部分。")
                _reset_cancel()
                return

            if app_name:
                app_names[pkg] = app_name
                changed = True
                found += 1
                print(f"{prefix} ✅ {pkg} → {app_name}")
            else:
                missed += 1
                print(f"{prefix} ❓ {pkg} — 未能获取应用名")
            time.sleep(0.3)
        except KeyboardInterrupt:
            print(f"\n\n  ⚠️  用户中断！正在保存已获取的 {found} 条名称…")
            config["app_names"] = app_names
            save_config(config)
            print(f"  💾 已安全保存。下次运行 --enrich-names 会继续剩余部分。")
            _reset_cancel()
            return
        except Exception as e:
            missed += 1
            print(f"{prefix} ❌ {pkg} — 异常: {e}")

        # 增量保存
        if changed and i % SAVE_INTERVAL == 0:
            config["app_names"] = app_names
            save_config(config)
            changed = False  # 重置标记

    _reset_cancel()

    # 最终保存
    if changed:
        config["app_names"] = app_names
        save_config(config)

    print(f"\n  📊 应用名补充完成: 新增 {found} 个")
    if missed:
        print(f"  ⚠️  {missed} 个应用未能获取名称（可手动在 app_categories 条目后添加 // 应用名 注释）")


def _ensure_tar_extracted(layout_dir):
    """自动解压 com.android.launcher.tar（如果 data/ 目录尚不存在）"""
    import tarfile, stat
    from pathlib import Path
    layout_dir = Path(layout_dir)
    tar_path = layout_dir / "com.android.launcher.tar"
    data_dir = layout_dir / "data"

    if not tar_path.exists():
        print(f"  ❌ 未找到 tar 文件: {tar_path}")
        return False

    if data_dir.is_dir():
        print("  ✅ data/ 目录已存在，跳过解压")
        return True

    print("  📦 正在解压 com.android.launcher.tar …")
    with tarfile.open(str(tar_path), "r") as tar:
        tar.extractall(str(layout_dir), filter="data")
    print(f"  ✅ 已解压到 {data_dir}")

    # 移除只读属性
    for root, dirs, files in os.walk(str(data_dir)):
        for name in dirs + files:
            fp = os.path.join(root, name)
            os.chmod(fp, stat.S_IWRITE | os.stat(fp).st_mode)
    return True


def load_packages_from_db():
    """从最新备份的数据库中提取包名和应用名（自动解压 tar）"""
    import sqlite3
    from pathlib import Path
    
    data_dir = Path("Data")
    if not data_dir.exists():
        print("  ❌ 未找到 Data/ 目录")
        return None, None
    
    # 找最新备份
    backup_dirs = sorted([d for d in data_dir.iterdir() if d.is_dir()], reverse=True)
    if not backup_dirs:
        print("  ❌ 未找到备份目录")
        return None, None
    
    latest_backup = backup_dirs[0]
    layout_dir = latest_backup / "Layout"

    if not layout_dir.is_dir():
        print(f"  ❌ 未找到 Layout 目录: {layout_dir}")
        return None, None

    # 自动解压 tar
    if not _ensure_tar_extracted(layout_dir):
        return None, None

    # 用 glob 查找 launcher.db（路径可能有变化）
    matches = list(layout_dir.glob("data/**/launcher.db"))
    if not matches:
        print(f"  ❌ 解压后仍未找到 launcher.db")
        return None, None
    db_path = matches[0]
    
    print(f"  📊 读取数据库: {db_path.relative_to('.')}")
    
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        
        # 检测表名
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        
        items_table = None
        if "singledesktopitems_draw" in tables:
            items_table = "singledesktopitems_draw"
        elif "singledesktopitems" in tables:
            items_table = "singledesktopitems"
        
        if not items_table:
            print(f"  ❌ 数据库中未找到 items 表！现有表: {tables}")
            return None, None
        
        # 从数据库提取所有应用
        cur.execute(f"SELECT title, intent FROM {items_table} WHERE intent IS NOT NULL AND title IS NOT NULL")
        rows = cur.fetchall()
        
        packages = []
        app_names_map = {}
        
        for title, intent in rows:
            m = re.search(r'component=([^/]+)/', intent)
            if m:
                pkg = m.group(1)
                title = title.strip()
                if pkg not in app_names_map:
                    packages.append(pkg)
                    app_names_map[pkg] = title
        
        conn.close()
        
        print(f"  ✅ 从数据库提取了 {len(packages)} 个应用")
        return packages, app_names_map
    
    except Exception as e:
        print(f"  ❌ 读取数据库失败: {e}")
        return None, None
    already = len(app_categories) - len(need_lookup)
    print(f"  ✔ 已有名称: {already + found}/{len(app_categories)}")


# ============================================================================
#  主函数
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="应用分类辅助工具 v2.0 — 为 reorganize_layout_oneclick.py 生成 app_categories.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
        epilog="""
查询渠道：
  ① Google Play (国际应用覆盖好)
  ② 腾讯应用宝 sj.qq.com (中国应用覆盖好)
  两个渠道均查不到的应用，列入 JSON 的 "unclassified" 供手动分类

示例：
  python fetch_categories.py --from-db             # 从备份数据库提取包名+应用名
  python fetch_categories.py --classify            # 多渠道自动分类
  python fetch_categories.py --interactive         # 交互式处理剩余
  python fetch_categories.py --enrich-names        # 补充所有应用的人类可读名称
  python fetch_categories.py --all                 # 以上全部一步完成
  python fetch_categories.py --stats               # 查看分类统计
""",
    )
    parser.add_argument("--from-db", action="store_true",
                        help="从备份数据库直接提取包名和应用名（自动解压 tar）")
    parser.add_argument("--classify", action="store_true", help="多渠道自动分类 (Google Play + 应用宝)")
    parser.add_argument("--classify-ai", action="store_true", help="AI 分类 (兼容 OpenAI API)")
    parser.add_argument("--ai-setup", action="store_true", help="交互式配置 AI API 参数")
    parser.add_argument("--interactive", action="store_true", help="交互式处理未分类包名")
    parser.add_argument("--enrich-names", action="store_true", help="为所有应用补充人类可读名称 (screen0 + app_categories)")
    parser.add_argument("--all", action="store_true", help="完整流程: from-db + classify + interactive + enrich-names")
    parser.add_argument("--stats", action="store_true", help="显示当前分类统计")
    parser.add_argument("--workdir", type=str, metavar="DIR",
                        help="指定工作目录（覆盖脚本所在目录，供外部调用）")

    args = parser.parse_args()

    # 支持外部指定工作目录
    global SCRIPT_DIR, PACKAGES_FILE, CONFIG_FILE, AI_CONFIG_FILE
    if args.workdir and os.path.isdir(args.workdir):
        SCRIPT_DIR = os.path.abspath(args.workdir)
        PACKAGES_FILE = os.path.join(SCRIPT_DIR, "packages.txt")
        CONFIG_FILE = os.path.join(SCRIPT_DIR, "app_categories.json")
        AI_CONFIG_FILE = os.path.join(SCRIPT_DIR, "ai_config.json")

    if not any([args.from_db, args.classify, args.classify_ai, args.ai_setup,
                args.interactive, args.enrich_names, args.all, args.stats]):
        parser.print_help()
        return

    # --ai-setup
    if args.ai_setup:
        configure_ai_settings()
        if not any([args.from_db, args.classify, args.classify_ai,
                    args.interactive, args.enrich_names, args.all, args.stats]):
            return

    # --from-db
    if args.from_db:
        packages, app_names_map = load_packages_from_db()
        if packages:
            # 保存包名列表
            with open(PACKAGES_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(packages) + "\n")
            # 保存应用名映射（会写入后续的 classify）
            config = load_config()
            config["app_names"] = app_names_map
            save_config(config)
            print(f"  ✅ 已从数据库导入 {len(packages)} 个包名，应用名已补充到配置文件注释")
        return

    # --stats
    if args.stats:
        config = load_config()
        cats = config.get("app_categories", {})
        order = config.get("category_order", [])
        unclassified = config.get("unclassified", [])
        by_cat = {}
        for pkg, cat in cats.items():
            by_cat.setdefault(cat, []).append(pkg)
        print(f"\n📊 分类统计 (共 {len(cats)} 个应用, {len(by_cat)} 个分类):\n")
        for cat in order:
            pkgs = by_cat.pop(cat, [])
            if pkgs:
                print(f"  {cat}: {len(pkgs)} 个")
        for cat, pkgs in sorted(by_cat.items()):
            print(f"  {cat}: {len(pkgs)} 个 (不在 category_order 中)")
        if unclassified:
            print(f"\n  ⚠️  未分类: {len(unclassified)} 个")
            for item in unclassified[:5]:
                if isinstance(item, dict):
                    name = item.get("appName", "")
                    pkg = item["packageName"]
                    print(f"      {pkg}" + (f" ({name})" if name else ""))
                else:
                    print(f"      {item}")
            if len(unclassified) > 5:
                print(f"      … 共 {len(unclassified)} 个")
        return

    # --all: 完整流程
    if args.all:
        packages, app_names_map = load_packages_from_db()
        if packages:
            with open(PACKAGES_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(packages) + "\n")
            config = load_config()
            config["app_names"] = app_names_map
            save_config(config)
            print(f"  ✅ 已从数据库导入 {len(packages)} 个包名")
            classify_all(packages)
            interactive_classify(packages)
        enrich_all_names()
        return

    # --classify
    if args.classify:
        packages = load_packages()
        if packages:
            classify_all(packages)

    # --classify-ai
    if args.classify_ai:
        packages = load_packages()
        if packages:
            classify_all_via_ai(packages)

    # --interactive
    if args.interactive:
        packages = load_packages()
        if packages:
            interactive_classify(packages)

    # --enrich-names
    if args.enrich_names:
        enrich_all_names()


if __name__ == "__main__":
    main()
